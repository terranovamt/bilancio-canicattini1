from __future__ import annotations

import asyncio
import base64
import json

import httpx

from vision.preprocess import preprocess_receipt
from vision.receipt_schema import ReceiptData, parse_receipt_json

_SYSTEM_PROMPT = """
You analyze receipt images. Return only one JSON object that follows the supplied
JSON schema. Do not include Markdown, explanations, or text outside the JSON.
Use an ISO date in YYYY-MM-DD format. Use null for unknown scalar fields and an
empty array when no reliable VAT breakdown or line items can be read. Never
guess a value. Keep confidence between 0 and 1 and lower it when any field is
uncertain. Read the merchant, total, currency, VAT, payment method, line items,
and suggested category from the receipt.
""".strip()


class LocalVisionError(RuntimeError):
    """Raised when the local vision service cannot return valid receipt data."""


class OllamaReceiptAnalyzer:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 180,
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
            "stream": False,
            "format": ReceiptData.model_json_schema(),
            "options": {"temperature": 0},
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(self._chat_url, json=request_body)
                response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise LocalVisionError(
                "The local vision service returned an error"
            ) from error
        except httpx.RequestError as error:
            raise LocalVisionError(
                "The local vision service is unavailable or timed out"
            ) from error

        try:
            response_data = response.json()
        except json.JSONDecodeError as error:
            raise LocalVisionError(
                "The local vision service returned an invalid response"
            ) from error
        if not isinstance(response_data, dict):
            raise LocalVisionError("The local vision service returned an invalid response")

        message = response_data.get("message")
        if not isinstance(message, dict):
            raise LocalVisionError("The local vision service returned no receipt result")
        content = message.get("content")
        if not isinstance(content, str):
            raise LocalVisionError("The local vision service returned no receipt result")
        return parse_receipt_json(content)
