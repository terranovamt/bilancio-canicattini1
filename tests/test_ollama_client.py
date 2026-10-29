import json
import base64
from io import BytesIO
import unittest
from unittest.mock import patch

import httpx
from PIL import Image, ImageDraw

from vision.ollama_client import LocalVisionError, OllamaReceiptAnalyzer, VisionFailureCode

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
    async def test_real_preprocessing_and_structured_response_with_synthetic_image(self) -> None:
        image = Image.new("RGB", (480, 640), "white")
        ImageDraw.Draw(image).text(
            (30, 30), "TEST SHOP\n26/10/2026\nTOTAL EUR 12.50", fill="black"
        )
        source = BytesIO()
        image.save(source, format="PNG")
        image.close()

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            processed = base64.b64decode(body["messages"][1]["images"][0], validate=True)
            with Image.open(BytesIO(processed)) as prepared:
                self.assertEqual(prepared.format, "PNG")
                self.assertGreater(prepared.width, 0)
                self.assertLessEqual(max(prepared.size), 2400)
            self.assertFalse(body["think"])
            return httpx.Response(200, json={
                "message": {"content": json.dumps(_VALID_RECEIPT)},
                "done": True, "done_reason": "stop",
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("vision.ollama_client.httpx.AsyncClient", return_value=client):
            receipt = await OllamaReceiptAnalyzer(
                base_url="http://ollama:11434", model="local-test-model"
            ).analyze(source.getvalue(), "synthetic.png")
        self.assertEqual(str(receipt.total_amount), "12.50")

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
        self.assertFalse(captured_request["think"])
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
            with self.assertRaisesRegex(LocalVisionError, "failed validation") as caught:
                await OllamaReceiptAnalyzer(
                    base_url="http://ollama:11434",
                    model="local-test-model",
                ).analyze(b"input-image", "receipt.jpg")
            self.assertEqual(caught.exception.code, VisionFailureCode.INVALID_RECEIPT)

    async def test_classifies_http_errors_without_exposing_raw_response(self) -> None:
        cases = (
            (404, 'model "private-model" not found', VisionFailureCode.MODEL_NOT_FOUND),
            (500, "model requires more system memory private-data", VisionFailureCode.MEMORY),
            (500, "out of memory private-data", VisionFailureCode.MEMORY),
            (400, "model does not support images private-data", VisionFailureCode.UNSUPPORTED_MODEL),
            (400, "model requires a newer version private-data", VisionFailureCode.UNSUPPORTED_MODEL),
            (500, "unknown failure private-data", VisionFailureCode.SERVICE_ERROR),
            (404, "private-data", VisionFailureCode.SERVICE_ERROR),
        )
        for status, detail, expected in cases:
            with self.subTest(status=status, code=expected):
                client = httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(status, json={"error": detail})
                    ), trust_env=False,
                )
                with (
                    patch("vision.ollama_client.preprocess_receipt", return_value=[b"image"]),
                    patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
                    self.assertRaises(LocalVisionError) as caught,
                ):
                    await OllamaReceiptAnalyzer(
                        base_url="http://ollama:11434", model="test"
                    ).analyze(b"input", "image.png")
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn("private", str(caught.exception))

    async def test_distinguishes_timeout_from_network_failure(self) -> None:
        for error_type, expected in (
            (httpx.ReadTimeout, VisionFailureCode.TIMEOUT),
            (httpx.ConnectTimeout, VisionFailureCode.TIMEOUT),
            (httpx.ConnectError, VisionFailureCode.CONNECTION),
        ):
            with self.subTest(error=error_type.__name__):
                async def handler(request: httpx.Request) -> httpx.Response:
                    raise error_type("private error", request=request)
                client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                with (
                    patch("vision.ollama_client.preprocess_receipt", return_value=[b"image"]),
                    patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
                    self.assertRaises(LocalVisionError) as caught,
                ):
                    await OllamaReceiptAnalyzer(
                        base_url="http://ollama:11434", model="test"
                    ).analyze(b"input", "image.png")
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private", str(caught.exception))

    async def test_rejects_incomplete_empty_and_service_error_responses(self) -> None:
        valid_content = {"content": json.dumps(_VALID_RECEIPT)}
        cases = (
            ({"message": valid_content, "done": False}, VisionFailureCode.INCOMPLETE_RESPONSE),
            ({"message": valid_content, "done_reason": "length"}, VisionFailureCode.INCOMPLETE_RESPONSE),
            ({"message": {"content": "", "thinking": "private trace"}}, VisionFailureCode.EMPTY_RESULT),
            ({"message": {"content": "   "}}, VisionFailureCode.EMPTY_RESULT),
            ({"message": None}, VisionFailureCode.EMPTY_RESULT),
            ({"error": "out of memory private"}, VisionFailureCode.MEMORY),
            (["private"], VisionFailureCode.INVALID_RESPONSE),
        )
        for body, expected in cases:
            with self.subTest(code=expected, body_type=type(body).__name__):
                client = httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=body)
                ))
                with (
                    patch("vision.ollama_client.preprocess_receipt", return_value=[b"image"]),
                    patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
                    self.assertRaises(LocalVisionError) as caught,
                ):
                    await OllamaReceiptAnalyzer(
                        base_url="http://ollama:11434", model="test"
                    ).analyze(b"input", "image.png")
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private", str(caught.exception))

    async def test_rejects_non_json_service_body_without_echoing_it(self) -> None:
        for status, expected in (
            (200, VisionFailureCode.INVALID_RESPONSE),
            (503, VisionFailureCode.SERVICE_ERROR),
        ):
            with self.subTest(status=status):
                client = httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(status, text="private diagnostic")
                ))
                with (
                    patch("vision.ollama_client.preprocess_receipt", return_value=[b"image"]),
                    patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
                    self.assertRaises(LocalVisionError) as caught,
                ):
                    await OllamaReceiptAnalyzer(
                        base_url="http://ollama:11434", model="test"
                    ).analyze(b"input", "image.png")
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private", str(caught.exception))

    def test_requires_local_model_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "model"):
            OllamaReceiptAnalyzer(
                base_url="http://ollama:11434",
                model=" ",
            )


if __name__ == "__main__":
    unittest.main()
