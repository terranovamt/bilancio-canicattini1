from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
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

logger = logging.getLogger(__name__)

_PHASE_POLL_SECONDS = 5.0
_PHASE_POLL_TIMEOUT_SECONDS = 5.0


class AnalysisPhase(str, Enum):
    PREPARING_IMAGE = "preparing_image"
    LOADING_MODEL = "loading_model"
    READING_IMAGE = "reading_image"
    WRITING = "writing"


@dataclass(frozen=True)
class AnalysisProgress:
    phase: AnalysisPhase
    characters: int = 0


class _ProgressTracker:
    def __init__(self, on_progress: Callable[[AnalysisProgress], None] | None) -> None:
        self._on_progress = on_progress
        self.phase: AnalysisPhase | None = None
        self.characters = 0

    def emit(self, phase: AnalysisPhase, characters: int = 0) -> None:
        if phase is not self.phase:
            logger.info("Local receipt analysis phase: %s", phase.value)
        self.phase = phase
        self.characters = characters
        if self._on_progress is not None:
            self._on_progress(AnalysisProgress(phase, characters))


def _seconds(chunk: dict, key: str) -> float:
    value = chunk.get(key)
    return value / 1_000_000_000 if isinstance(value, int) else 0.0


def _count(chunk: dict, key: str) -> int:
    value = chunk.get(key)
    return value if isinstance(value, int) else 0


def _log_metrics(chunk: dict) -> None:
    # Ollama reports durations in nanoseconds; only timings and counts are logged.
    logger.info(
        "Local receipt analysis finished: total=%.1fs load=%.1fs "
        "image_and_prompt=%.1fs (%d tokens) answer=%.1fs (%d tokens)",
        _seconds(chunk, "total_duration"),
        _seconds(chunk, "load_duration"),
        _seconds(chunk, "prompt_eval_duration"),
        _count(chunk, "prompt_eval_count"),
        _seconds(chunk, "eval_duration"),
        _count(chunk, "eval_count"),
    )


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
        self._ps_url = f"{base_url.rstrip('/')}/api/ps"
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds

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
                return await self._analyze(file_bytes, file_name, tracker)
        except TimeoutError as error:
            logger.warning(
                "Local receipt analysis timed out during phase %s after %d characters",
                tracker.phase.value if tracker.phase else "start",
                tracker.characters,
            )
            raise LocalVisionError(
                "The local vision service timed out", code=VisionFailureCode.TIMEOUT
            ) from error

    def _is_this_model(self, entry: object) -> bool:
        if not isinstance(entry, dict):
            return False
        wanted = {self._model}
        if ":" not in self._model:
            wanted.add(f"{self._model}:latest")
        return entry.get("name") in wanted or entry.get("model") in wanted

    async def _model_loaded(self, client: httpx.AsyncClient) -> bool | None:
        try:
            response = await client.get(self._ps_url, timeout=_PHASE_POLL_TIMEOUT_SECONDS)
            response.raise_for_status()
            models = response.json().get("models")
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        if not isinstance(models, list):
            return None
        return any(self._is_this_model(entry) for entry in models)

    async def _watch_model(
        self, client: httpx.AsyncClient, tracker: _ProgressTracker
    ) -> None:
        while True:
            await asyncio.sleep(_PHASE_POLL_SECONDS)
            loaded = await self._model_loaded(client)
            if tracker.phase is AnalysisPhase.WRITING:
                return
            if loaded is not None:
                tracker.emit(
                    AnalysisPhase.READING_IMAGE if loaded else AnalysisPhase.LOADING_MODEL
                )

    async def _analyze(
        self, file_bytes: bytes, file_name: str, tracker: _ProgressTracker
    ) -> ReceiptData:
        tracker.emit(AnalysisPhase.PREPARING_IMAGE)
        processed_images = await asyncio.to_thread(
            preprocess_receipt,
            file_bytes,
            file_name,
        )
        logger.info(
            "Starting local receipt analysis: %d image(s), %d KB",
            len(processed_images),
            sum(len(image) for image in processed_images) // 1024,
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
                watcher = asyncio.create_task(self._watch_model(client, tracker))
                try:
                    content = await self._stream_chat(client, request_body, tracker)
                finally:
                    watcher.cancel()
                    try:
                        await watcher
                    except asyncio.CancelledError:
                        pass
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

    async def _stream_chat(
        self, client: httpx.AsyncClient, request_body: dict, tracker: _ProgressTracker
    ) -> str:
        async with client.stream("POST", self._chat_url, json=request_body) as response:
            if response.is_error:
                await response.aread()
            response.raise_for_status()
            parts: list[str] = []
            size = 0
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
                if content:
                    tracker.emit(AnalysisPhase.WRITING, size)
                if chunk.get("done_reason") == "length":
                    raise LocalVisionError(
                        "The local vision service returned an incomplete response",
                        code=VisionFailureCode.INCOMPLETE_RESPONSE,
                    )
                if chunk.get("done") is True:
                    _log_metrics(chunk)
                    return "".join(parts)
            raise LocalVisionError(
                "The local vision service returned an incomplete response",
                code=VisionFailureCode.INCOMPLETE_RESPONSE,
            )
