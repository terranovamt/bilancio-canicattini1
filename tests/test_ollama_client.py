import json
import asyncio
import base64
from io import BytesIO
import unittest
from unittest.mock import patch

import httpx
from PIL import Image, ImageDraw

from vision.ollama_client import (
    AnalysisPhase,
    LocalVisionError,
    OllamaReceiptAnalyzer,
    VisionFailureCode,
)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, delay: float = 0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self) -> None:
        self.closed = True

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
    async def run_stream(self, stream, *, timeout=600, on_progress=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        ))
        with (
            patch("vision.ollama_client.preprocess_receipt", return_value=[b"image"]),
            patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
        ):
            return await OllamaReceiptAnalyzer(
                base_url="http://ollama:11434", model="test", timeout_seconds=timeout
            ).analyze_with_progress(b"input", "image.png", on_progress=on_progress)

    async def test_stream_assembles_fragments_and_waits_for_completion(self) -> None:
        content = json.dumps({**_VALID_RECEIPT, "merchant": "Caffè"}, ensure_ascii=False)
        chunks = [
            json.dumps({"message": {"content": part}, "done": False}, ensure_ascii=False).encode()
            + b"\n"
            for part in (content[:20], content[20:70], content[70:])
        ]
        chunks.append(b'{"message":{"content":""},"done":true}\n')
        wire = b"\n" + b"".join(chunks)
        stream = ChunkStream([wire[:15], wire[15:48], wire[48:]])
        progress = []
        with self.assertLogs("vision.ollama_client", level="INFO") as logs:
            receipt = await self.run_stream(stream, on_progress=progress.append)
        self.assertEqual(receipt.merchant, "Caffè")
        self.assertEqual(progress[0].phase, AnalysisPhase.PREPARING_IMAGE)
        writing = [item for item in progress if item.phase is AnalysisPhase.WRITING]
        self.assertEqual(len(writing), 3)
        self.assertEqual(writing[-1].characters, len(content))
        self.assertTrue(stream.closed)
        self.assertNotIn("Caffè", "\n".join(logs.output))

    async def test_reports_model_loading_then_image_reading_from_local_status(self) -> None:
        status_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/ps":
                status_calls.append(request.method)
                models = [] if len(status_calls) == 1 else [{"name": "test:latest"}]
                return httpx.Response(200, json={"models": models})
            content = json.dumps(_VALID_RECEIPT)
            return httpx.Response(200, stream=ChunkStream([
                json.dumps({"message": {"content": content}, "done": True}).encode() + b"\n"
            ], delay=0.2))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        progress = []
        with (
            patch("vision.ollama_client.preprocess_receipt", return_value=[b"image"]),
            patch("vision.ollama_client.httpx.AsyncClient", return_value=client),
            patch("vision.ollama_client._PHASE_POLL_SECONDS", 0.05),
        ):
            await OllamaReceiptAnalyzer(
                base_url="http://ollama:11434", model="test"
            ).analyze_with_progress(b"input", "image.png", on_progress=progress.append)
        phases = [item.phase for item in progress]
        self.assertIn(AnalysisPhase.LOADING_MODEL, phases)
        self.assertIn(AnalysisPhase.READING_IMAGE, phases)
        self.assertLess(
            phases.index(AnalysisPhase.LOADING_MODEL), phases.index(AnalysisPhase.READING_IMAGE)
        )
        self.assertEqual(phases[-1], AnalysisPhase.WRITING)
        self.assertEqual(set(status_calls), {"GET"})

    async def test_logs_phase_timings_without_receipt_content(self) -> None:
        content = json.dumps({**_VALID_RECEIPT, "merchant": "Private Shop"})
        line = json.dumps({
            "message": {"content": content}, "done": True,
            "total_duration": 362_000_000_000, "load_duration": 12_000_000_000,
            "prompt_eval_count": 1500, "prompt_eval_duration": 300_000_000_000,
            "eval_count": 120, "eval_duration": 50_000_000_000,
        })
        with self.assertLogs("vision.ollama_client", level="INFO") as logs:
            await self.run_stream(ChunkStream([(line + "\n").encode()]))
        output = "\n".join(logs.output)
        self.assertIn("total=362.0s load=12.0s image_and_prompt=300.0s (1500 tokens)", output)
        self.assertIn("answer=50.0s (120 tokens)", output)
        self.assertNotIn("Private Shop", output)

    async def test_timeout_logs_the_phase_reached(self) -> None:
        stream = ChunkStream([b'{"message":{"content":""},"done":false}\n'], delay=10)
        with self.assertLogs("vision.ollama_client", level="WARNING") as logs:
            with self.assertRaises(LocalVisionError):
                await self.run_stream(stream, timeout=0.05)
        self.assertIn("timed out during phase preparing_image", "\n".join(logs.output))

    async def test_valid_partial_json_without_final_marker_is_never_accepted(self) -> None:
        line = json.dumps({"message": {"content": json.dumps(_VALID_RECEIPT)}, "done": False})
        stream = ChunkStream([(line + "\n").encode()])
        with self.assertRaises(LocalVisionError) as caught:
            await self.run_stream(stream)
        self.assertEqual(caught.exception.code, VisionFailureCode.INCOMPLETE_RESPONSE)
        self.assertTrue(stream.closed)

    async def test_total_timeout_applies_even_when_chunks_keep_arriving(self) -> None:
        stream = ChunkStream(
            [b'{"message":{"content":" "},"done":false}\n'] * 100, delay=0.01
        )
        with self.assertRaises(LocalVisionError) as caught:
            await self.run_stream(stream, timeout=0.05)
        self.assertEqual(caught.exception.code, VisionFailureCode.TIMEOUT)
        self.assertTrue(stream.closed)

    async def test_error_after_partial_content_is_not_treated_as_success(self) -> None:
        for last, expected in (
            (b'{"error":"out of memory private"}\n', VisionFailureCode.MEMORY),
            (b'not-json-private\n', VisionFailureCode.INVALID_RESPONSE),
            (b'{"message":{"content":""},"done":true,"done_reason":"length"}\n',
             VisionFailureCode.INCOMPLETE_RESPONSE),
        ):
            with self.subTest(expected=expected):
                stream = ChunkStream([b'{"message":{"content":"{"},"done":false}\n', last])
                with self.assertRaises(LocalVisionError) as caught:
                    await self.run_stream(stream)
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private", str(caught.exception))
                self.assertTrue(stream.closed)

    async def test_cancellation_closes_stream(self) -> None:
        stream = ChunkStream([b'{"message":{"content":""},"done":false}\n'], delay=10)
        task = asyncio.create_task(self.run_stream(stream))
        await asyncio.wait_for(stream.started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)

    async def test_read_timeout_mid_stream_is_classified_and_closes_response(self) -> None:
        class TimeoutStream(ChunkStream):
            async def __aiter__(self):
                yield b'{"message":{"content":"{"},"done":false}\n'
                raise httpx.ReadTimeout("private")

        stream = TimeoutStream([])
        with self.assertRaises(LocalVisionError) as caught:
            await self.run_stream(stream)
        self.assertEqual(caught.exception.code, VisionFailureCode.TIMEOUT)
        self.assertTrue(stream.closed)
        self.assertNotIn("private", str(caught.exception))

    async def test_rejects_excessive_generated_content(self) -> None:
        line = json.dumps({"message": {"content": "x" * 1_000_001}, "done": True})
        stream = ChunkStream([(line + "\n").encode()])
        with self.assertRaises(LocalVisionError) as caught:
            await self.run_stream(stream)
        self.assertEqual(caught.exception.code, VisionFailureCode.INVALID_RESPONSE)
        self.assertTrue(stream.closed)

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
                json={"message": {"content": json.dumps(_VALID_RECEIPT)}, "done": True},
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
        self.assertTrue(captured_request["stream"])
        self.assertFalse(captured_request["think"])
        user_message = captured_request["messages"][1]
        self.assertEqual(user_message["images"], ["cHJvY2Vzc2VkLWltYWdl"])
        self.assertEqual(
            set(captured_request["format"]["properties"]),
            {"date", "merchant", "total_amount", "confidence"},
        )

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
            return httpx.Response(200, json={"message": {"content": "not json"}, "done": True})

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
            ({"message": {"content": "", "thinking": "private trace"}, "done": True}, VisionFailureCode.EMPTY_RESULT),
            ({"message": {"content": "   "}, "done": True}, VisionFailureCode.EMPTY_RESULT),
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
