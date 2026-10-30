from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from io import BytesIO

import httpx
from PIL import Image, UnidentifiedImageError

from vision.preprocess import preprocess_receipt
from vision.receipt_schema import (
    ReceiptAnalysis,
    ReceiptData,
    ReceiptParseError,
    parse_analysis_json,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You read Italian receipt images.
Return only one JSON object that follows the supplied schema.
Use OCR text as a hint, but trust the image if OCR is noisy.
Fields:
- date as YYYY-MM-DD
- merchant as printed on the receipt
- total_amount as a number
- confidence between 0 and 1
Use null for a field you cannot read.
Never guess values.
""".strip()

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class AnalysisPhase(str, Enum):
    PREPARING_IMAGE = "preparing_image"
    LOADING_MODEL = "loading_model"
    READING_IMAGE = "reading_image"
    WRITING = "writing"


@dataclass(frozen=True)
class AnalysisProgress:
    phase: AnalysisPhase
    characters: int = 0


class VisionFailureCode(str, Enum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    MODEL_NOT_FOUND = "model_not_found"
    MEMORY = "memory"
    UNSUPPORTED_MODEL = "unsupported_model"
    SERVICE_ERROR = "service_error"
    INVALID_RESPONSE = "invalid_response"
    INCOMPLETE_RESPONSE = "incomplete_response"
    EMPTY_RESULT = "empty_result"
    INVALID_RECEIPT = "invalid_receipt"
    NOT_CONFIGURED = "not_configured"
    PREPROCESSING = "preprocessing"


class LocalVisionError(RuntimeError):
    """Raised when the configured AI service cannot return valid receipt data."""

    def __init__(
        self,
        message: str,
        *,
        code: VisionFailureCode = VisionFailureCode.SERVICE_ERROR,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class _ProgressTracker:
    def __init__(self, on_progress: Callable[[AnalysisProgress], None] | None) -> None:
        self._on_progress = on_progress
        self.phase: AnalysisPhase | None = None
        self.characters = 0

    def emit(self, phase: AnalysisPhase, characters: int = 0) -> None:
        self.phase = phase
        self.characters = characters
        if self._on_progress is not None:
            self._on_progress(AnalysisProgress(phase, characters))


def _error_code(response: httpx.Response) -> VisionFailureCode:
    if response.status_code == 404:
        return VisionFailureCode.MODEL_NOT_FOUND
    if response.status_code == 400:
        return VisionFailureCode.UNSUPPORTED_MODEL
    try:
        body = response.json()
    except json.JSONDecodeError:
        return VisionFailureCode.SERVICE_ERROR
    detail = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message", "")).casefold()
    if "resource exhausted" in detail:
        return VisionFailureCode.MEMORY
    return VisionFailureCode.SERVICE_ERROR


def _extract_text_from_response(payload: object) -> str:
    if not isinstance(payload, dict):
        raise LocalVisionError(
            "The AI service returned an invalid response",
            code=VisionFailureCode.INVALID_RESPONSE,
        )
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise LocalVisionError(
            "The AI service returned no candidate",
            code=VisionFailureCode.EMPTY_RESULT,
        )
    first = candidates[0]
    if not isinstance(first, dict):
        raise LocalVisionError(
            "The AI service returned an invalid candidate",
            code=VisionFailureCode.INVALID_RESPONSE,
        )
    if first.get("finishReason") == "MAX_TOKENS":
        raise LocalVisionError(
            "The AI service returned an incomplete response",
            code=VisionFailureCode.INCOMPLETE_RESPONSE,
        )
    content = first.get("content")
    if not isinstance(content, dict):
        raise LocalVisionError(
            "The AI service returned no content",
            code=VisionFailureCode.EMPTY_RESULT,
        )
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise LocalVisionError(
            "The AI service returned no content",
            code=VisionFailureCode.EMPTY_RESULT,
        )
    fragments = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            fragments.append(part["text"])
    text = "".join(fragments).strip()
    if not text:
        raise LocalVisionError(
            "The AI service returned no usable text",
            code=VisionFailureCode.EMPTY_RESULT,
        )
    return text


def _ocr_text(processed_images: list[bytes]) -> str:
    try:
        import pytesseract
        from pytesseract.pytesseract import TesseractNotFoundError
    except ModuleNotFoundError:
        logger.warning("OCR skipped: pytesseract is not installed")
        return ""
    except ImportError:
        logger.warning("OCR skipped: pytesseract import failed")
        return ""

    fragments: list[str] = []
    for image_bytes in processed_images:
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.load()
                fragments.append(
                    pytesseract.image_to_string(image, lang="ita+eng", config="--psm 6")
                )
        except (UnidentifiedImageError, OSError, RuntimeError, TesseractNotFoundError):
            logger.warning("OCR failed for one page")
    normalized = "\n".join(fragment.strip() for fragment in fragments if fragment.strip())
    return normalized[:6000]


class GeminiReceiptAnalyzer:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 600,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        if not model.strip():
            raise ValueError("model must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._url = f"{base_url.rstrip('/')}/models/{self._model}:generateContent"

    async def analyze(self, file_bytes: bytes, file_name: str) -> ReceiptData:
        return await self.analyze_with_progress(file_bytes, file_name, on_progress=None)

    async def analyze_with_progress(
        self,
        file_bytes: bytes,
        file_name: str,
        *,
        on_progress: Callable[[AnalysisProgress], None] | None,
    ) -> ReceiptData:
        tracker = _ProgressTracker(on_progress)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                tracker.emit(AnalysisPhase.PREPARING_IMAGE)
                processed = await asyncio.to_thread(preprocess_receipt, file_bytes, file_name)
                tracker.emit(AnalysisPhase.READING_IMAGE)
                ocr_text = await asyncio.to_thread(_ocr_text, processed)
                tracker.emit(AnalysisPhase.LOADING_MODEL)
                result = await self._call_gemini(processed, ocr_text)
                tracker.emit(AnalysisPhase.WRITING, len(result))
                return parse_analysis_json(result)
        except TimeoutError as error:
            raise LocalVisionError(
                "The AI service timed out",
                code=VisionFailureCode.TIMEOUT,
            ) from error
        except ReceiptParseError as error:
            raise LocalVisionError(
                "The AI result failed validation",
                code=VisionFailureCode.INVALID_RECEIPT,
            ) from error

    async def _call_gemini(self, processed_images: list[bytes], ocr_text: str) -> str:
        image_parts = [
            {
                "inline_data": {
                    "mime_type": "image/png",
                    "data": base64.b64encode(image).decode("ascii"),
                }
            }
            for image in processed_images
        ]
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": _SYSTEM_PROMPT},
                        {"text": f"OCR text (optional):\n{ocr_text or '[empty]'}"},
                        *image_parts,
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": ReceiptAnalysis.model_json_schema(),
            },
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(
                    self._url,
                    params={"key": self._api_key},
                    json=payload,
                )
        except httpx.TimeoutException as error:
            raise LocalVisionError(
                "The AI service timed out",
                code=VisionFailureCode.TIMEOUT,
            ) from error
        except httpx.RequestError as error:
            raise LocalVisionError(
                "The AI service is unavailable",
                code=VisionFailureCode.CONNECTION,
            ) from error

        if response.is_error:
            raise LocalVisionError(
                "The AI service returned an error",
                code=_error_code(response),
                status_code=response.status_code,
            )
        try:
            body = response.json()
        except ValueError as error:
            raise LocalVisionError(
                "The AI service returned an invalid JSON payload",
                code=VisionFailureCode.INVALID_RESPONSE,
            ) from error
        return _extract_text_from_response(body)
