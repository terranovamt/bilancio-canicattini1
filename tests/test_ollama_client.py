import json
import unittest
from unittest.mock import patch

import httpx

from vision.ollama_client import LocalVisionError, OllamaReceiptAnalyzer

_VALID_RECEIPT = {
    "date": "2026-10-26",
    "merchant": "Test Shop",
    "total_amount": "12.50",
    "currency": "EUR",
    "vat": [],
    "payment_method": "card",
    "line_items": [],
    "suggested_category": None,
    "confidence": 0.9,
    "notes": None,
}


class OllamaReceiptAnalyzerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_preprocessed_image_only_to_local_service(self) -> None:
        captured_request: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured_request.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"message": {"content": json.dumps(_VALID_RECEIPT)}},
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )
        with (
            patch(
                "vision.ollama_client.preprocess_receipt",
                return_value=[b"processed-image"],
            ),
            patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
        ):
            receipt = await OllamaReceiptAnalyzer(
                base_url="http://ollama:11434",
                model="local-test-model",
            ).analyze(b"input-image", "receipt.jpg")

        self.assertEqual(receipt.merchant, "Test Shop")
        self.assertEqual(captured_request["model"], "local-test-model")
        self.assertFalse(captured_request["stream"])
        user_message = captured_request["messages"][1]
        self.assertEqual(user_message["images"], ["cHJvY2Vzc2VkLWltYWdl"])
        self.assertIn("format", captured_request)

    async def test_surfaces_local_service_connection_failure(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )
        with (
            patch(
                "vision.ollama_client.preprocess_receipt",
                return_value=[b"processed-image"],
            ),
            patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
        ):
            with self.assertRaisesRegex(LocalVisionError, "unavailable"):
                await OllamaReceiptAnalyzer(
                    base_url="http://ollama:11434",
                    model="local-test-model",
                ).analyze(b"input-image", "receipt.jpg")

    async def test_rejects_invalid_model_response(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": "not json"}})

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )
        with (
            patch(
                "vision.ollama_client.preprocess_receipt",
                return_value=[b"processed-image"],
            ),
            patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
        ):
            with self.assertRaisesRegex(ValueError, "failed validation"):
                await OllamaReceiptAnalyzer(
                    base_url="http://ollama:11434",
                    model="local-test-model",
                ).analyze(b"input-image", "receipt.jpg")

    def test_requires_local_model_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "model"):
            OllamaReceiptAnalyzer(
                base_url="http://ollama:11434",
                model=" ",
            )


if __name__ == "__main__":
    unittest.main()
