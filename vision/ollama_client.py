from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from enum import Enum

import httpx

from vision.preprocess import preprocess_receipt
from vision.receipt_schema import ReceiptData, ReceiptParseError, parse_receipt_json

_SYSTEM_PROMPT = """
You analyze receipt images. Return only one JSON object that follows the supplied
JSON schema. Do not include Markdown, explanations, or text outside the JSON.
Use an ISO date in YYYY-MM-DD format. Use null for unknown scalar fields and an
empty array when no reliable VAT breakdown or line items can be read. Never
guess a value. Keep confidence between 0 and 1 and lower it when any field is
uncertain. Read the merchant, total, currency, VAT, payment method, line items,
and suggested category from the receipt.
""".strip()


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
    """Raised when the local vision service cannot return valid receipt data."""

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


def _service_error_code(response: httpx.Response) -> VisionFailureCode:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return VisionFailureCode.SERVICE_ERROR
    detail = body.get("error") if isinstance(body, dict) else None
    if not isinstance(detail, str):
        return VisionFailureCode.SERVICE_ERROR
    detail = detail.casefold()
    # Classify known service failures; never expose or log the response body.
    if any(fragment in detail for fragment in (
        "out of memory", "not enough memory", "requires more system memory",
        "unable to allocate", "failed to allocate",
    )):
        return VisionFailureCode.MEMORY
    if response.status_code == 404 and "model" in detail and "not found" in detail:
        return VisionFailureCode.MODEL_NOT_FOUND
    if any(fragment in detail for fragment in (
        "does not support images", "does not support vision", "unsupported architecture",
        "requires a newer version",
    )):
        return VisionFailureCode.UNSUPPORTED_MODEL
    return VisionFailureCode.SERVICE_ERROR


class OllamaReceiptAnalyzer:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 600,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        if not model.strip():
            raise ValueError("model must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._chat_url = f"{base_url.rstrip('/')}/api/chat"
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds

    async def analyze(self, file_bytes: bytes, file_name: str) -> ReceiptData:
        return await self.analyze_with_progress(file_bytes, file_name, on_progress=None)

    async def analyze_with_progress(
        self,
        file_bytes: bytes,
        file_name: str,
        *,
        on_progress: Callable[[], None] | None,
    ) -> ReceiptData:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._analyze(file_bytes, file_name, on_progress)
        except TimeoutError as error:
            raise LocalVisionError(
                "The local vision service timed out", code=VisionFailureCode.TIMEOUT
            ) from error

    async def _analyze(
        self, file_bytes: bytes, file_name: str, on_progress: Callable[[], None] | None
    ) -> ReceiptData:
        processed_images = await asyncio.to_thread(
            preprocess_receipt,
            file_bytes,
            file_name,
        )
        encoded_images = [
            base64.b64encode(image).decode("ascii") for image in processed_images
        ]
        request_body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Analyze the attached receipt image or images.",
                    "images": encoded_images,
                },
            ],
            "stream": True,
            "think": False,
            "format": ReceiptData.model_json_schema(),
            "options": {"temperature": 0},
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                trust_env=False,
            ) as client:
                async with client.stream("POST", self._chat_url, json=request_body) as response:
                    if response.is_error:
                        await response.aread()
                    response.raise_for_status()
                    parts: list[str] = []
                    size = 0
                    completed = False
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError as error:
                            raise LocalVisionError(
                                "The local vision service returned an invalid response",
                                code=VisionFailureCode.INVALID_RESPONSE,
                            ) from error
                        if not isinstance(chunk, dict):
                            raise LocalVisionError(
                                "The local vision service returned an invalid response",
                                code=VisionFailureCode.INVALID_RESPONSE,
                            )
                        if chunk.get("error"):
                            raise LocalVisionError(
                                "The local vision service reported an error",
                                code=_service_error_code(httpx.Response(200, json=chunk)),
                            )
                        message = chunk.get("message")
                        if not isinstance(message, dict):
                            raise LocalVisionError(
                                "The local vision service returned no receipt result",
                                code=VisionFailureCode.EMPTY_RESULT,
                            )
                        content = message.get("content", "")
                        if not isinstance(content, str):
                            raise LocalVisionError(
                                "The local vision service returned an invalid response",
                                code=VisionFailureCode.INVALID_RESPONSE,
                            )
                        size += len(content)
                        if size > 1_000_000:
                            raise LocalVisionError(
                                "The local vision result exceeded the size limit",
                                code=VisionFailureCode.INVALID_RESPONSE,
                            )
                        parts.append(content)
                        if content and on_progress is not None:
                            on_progress()
                        if chunk.get("done_reason") == "length":
                            raise LocalVisionError(
                                "The local vision service returned an incomplete response",
                                code=VisionFailureCode.INCOMPLETE_RESPONSE,
                            )
                        if chunk.get("done") is True:
                            completed = True
                            break
                    if not completed:
                        raise LocalVisionError(
                            "The local vision service returned an incomplete response",
                            code=VisionFailureCode.INCOMPLETE_RESPONSE,
                        )
        except httpx.HTTPStatusError as error:
            raise LocalVisionError(
                "The local vision service returned an error",
                code=_service_error_code(error.response),
                status_code=error.response.status_code,
            ) from error
        except httpx.TimeoutException as error:
            raise LocalVisionError(
                "The local vision service timed out",
                code=VisionFailureCode.TIMEOUT,
            ) from error
        except httpx.RequestError as error:
            raise LocalVisionError(
                "The local vision service is unavailable",
                code=VisionFailureCode.CONNECTION,
            ) from error

        content = "".join(parts)
        if not content.strip():
            raise LocalVisionError(
                "The local vision service returned no receipt result",
                code=VisionFailureCode.EMPTY_RESULT,
            )
        try:
            return parse_receipt_json(content)
        except ReceiptParseError as error:
            raise LocalVisionError(
                "The local vision result failed validation",
                code=VisionFailureCode.INVALID_RECEIPT,
            ) from error
