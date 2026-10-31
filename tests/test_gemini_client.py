import json
import unittest
from unittest.mock import patch

import httpx

from vision.gemini_client import (
    AnalysisPhase,
    GeminiReceiptAnalyzer,
    LocalVisionError,
    VisionFailureCode,
)


class GeminiReceiptAnalyzerTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_uses_gemini_schema_and_keeps_key_out_of_url(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertNotIn("key", request.url.params)
            self.assertEqual(request.headers["x-goog-api-key"], "synthetic-key")
            schema = json.loads(request.content)["generationConfig"]["responseSchema"]
            self.assertEqual(schema["type"], "OBJECT")
            self.assertEqual(schema["properties"]["total_amount"]["type"], "NUMBER")
            self.assertTrue(schema["properties"]["date"]["nullable"])
            self.assertNotIn("anyOf", json.dumps(schema))
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text":
                    '{"date":null,"merchant":null,"total_amount":null,"confidence":0}'
                }]}, "finishReason": "STOP"}],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("vision.gemini_client.httpx.AsyncClient", return_value=client):
            text = await GeminiReceiptAnalyzer(
                api_key="synthetic-key", model="test-model"
            )._call_gemini([b"synthetic"], "")
        self.assertIn("confidence", text)

    async def test_parses_json_response(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "date": "2026-10-26",
                                        "merchant": "Negozio Test",
                                        "total_amount": "12.50",
                                        "confidence": 0.9,
                                    }
                                )
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
        ))
        with (
            patch("vision.gemini_client.preprocess_receipt", return_value=[b"image"]),
            patch("vision.gemini_client._ocr_text", return_value="Totale 12,50"),
            patch("vision.gemini_client.httpx.AsyncClient", return_value=client),
        ):
            receipt = await GeminiReceiptAnalyzer(
                api_key="key",
                model="gemini-1.5-flash",
            ).analyze(b"test", "receipt.jpg")
        self.assertEqual(receipt.merchant, "Negozio Test")

    async def test_emits_progress_phases(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": '{"confidence": 0.5}'}]},
                    "finishReason": "STOP",
                }
            ]
        }
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
        ))
        progress = []
        with (
            patch("vision.gemini_client.preprocess_receipt", return_value=[b"image"]),
            patch("vision.gemini_client._ocr_text", return_value=""),
            patch("vision.gemini_client.httpx.AsyncClient", return_value=client),
        ):
            await GeminiReceiptAnalyzer(api_key="key", model="gemini-1.5-flash").analyze_with_progress(
                b"test", "receipt.jpg", on_progress=progress.append
            )
        self.assertEqual(
            [item.phase for item in progress],
            [
                AnalysisPhase.PREPARING_IMAGE,
                AnalysisPhase.READING_IMAGE,
                AnalysisPhase.LOADING_MODEL,
                AnalysisPhase.WRITING,
            ],
        )

    async def test_maps_http_error_codes(self) -> None:
        for status, expected in (
            (404, VisionFailureCode.MODEL_NOT_FOUND),
            (400, VisionFailureCode.INVALID_REQUEST),
            (403, VisionFailureCode.AUTHENTICATION),
            (429, VisionFailureCode.QUOTA),
            (500, VisionFailureCode.SERVICE_ERROR),
        ):
            with self.subTest(status=status):
                client = httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(status, json={"error": {"message": "failed"}})
                ))
                with (
                    patch("vision.gemini_client.preprocess_receipt", return_value=[b"image"]),
                    patch("vision.gemini_client._ocr_text", return_value=""),
                    patch("vision.gemini_client.httpx.AsyncClient", return_value=client),
                ):
                    with self.assertRaises(LocalVisionError) as caught:
                        await GeminiReceiptAnalyzer(api_key="key", model="gemini-1.5-flash").analyze(
                            b"test", "receipt.jpg"
                        )
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(caught.exception.status_code, status)

    async def test_rejects_incomplete_or_empty_candidates(self) -> None:
        for payload, expected in (
            ({"candidates": [{"content": {"parts": [{"text": ""}]}}]}, VisionFailureCode.EMPTY_RESULT),
            ({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]},
             VisionFailureCode.INCOMPLETE_RESPONSE),
        ):
            with self.subTest(expected=expected):
                client = httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=payload)
                ))
                with (
                    patch("vision.gemini_client.preprocess_receipt", return_value=[b"image"]),
                    patch("vision.gemini_client._ocr_text", return_value=""),
                    patch("vision.gemini_client.httpx.AsyncClient", return_value=client),
                ):
                    with self.assertRaises(LocalVisionError) as caught:
                        await GeminiReceiptAnalyzer(api_key="key", model="gemini-1.5-flash").analyze(
                            b"test", "receipt.jpg"
                        )
                self.assertEqual(caught.exception.code, expected)

    async def test_reports_invalid_receipt_json(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": '{"total_amount": "-1", "confidence": 0.3}'}]},
                    "finishReason": "STOP",
                }
            ]
        }
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
        ))
        with (
            patch("vision.gemini_client.preprocess_receipt", return_value=[b"image"]),
            patch("vision.gemini_client._ocr_text", return_value=""),
            patch("vision.gemini_client.httpx.AsyncClient", return_value=client),
        ):
            with self.assertRaises(LocalVisionError) as caught:
                await GeminiReceiptAnalyzer(api_key="key", model="gemini-1.5-flash").analyze(
                    b"test", "receipt.jpg"
                )
        self.assertEqual(caught.exception.code, VisionFailureCode.INVALID_RECEIPT)


if __name__ == "__main__":
    unittest.main()
