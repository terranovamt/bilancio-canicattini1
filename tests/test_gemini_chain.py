import asyncio
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from bot import messages
from config import ConfigurationError, Settings
from db.gemini_quota import GeminiQuotaStore, day_window
from vision.gemini_client import (
    GeminiReceiptAnalyzer, LocalVisionError, VisionFailureCode, _retry_at,
)

MODELS = (
    "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite",
    "gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash",
    "gemini-3.1-pro-preview", "gemini-2.5-pro",
)
COMPLETE = json.dumps({
    "date": "2026-10-26", "merchant": "Test", "total_amount": 12, "confidence": 0.9,
})


class GeminiChainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GeminiQuotaStore(Path(self.temp.name) / "quota.sqlite3")
        self.analyzer = GeminiReceiptAnalyzer(
            api_key="synthetic-key", model="legacy", models=MODELS,
            rpm_limits=(30,) * 8, rpd_limits=(1500,) * 8, quota_store=self.store,
        )
        self.prepare = patch("vision.gemini_client.preprocess_receipt", return_value=[b"image"])
        self.ocr = patch("vision.gemini_client._ocr_text", return_value="text")
        self.preprocess_mock = self.prepare.start()
        self.ocr_mock = self.ocr.start()
        self.addCleanup(self.prepare.stop)
        self.addCleanup(self.ocr.stop)

    async def test_tries_all_eight_in_configured_order_and_prepares_once(self) -> None:
        call = AsyncMock(side_effect=['{"confidence":0.1}'] * 7 + [COMPLETE])
        notices = []
        with patch.object(self.analyzer, "_call_gemini", call):
            receipt = await self.analyzer.analyze_with_progress(b"test", "test.jpg", on_progress=notices.append)
        self.assertEqual(receipt.merchant, "Test")
        self.assertEqual([item.kwargs["model"] for item in call.await_args_list], list(MODELS))
        self.preprocess_mock.assert_called_once()
        self.ocr_mock.assert_called_once()
        self.assertEqual([item.model for item in notices if item.notice == "using_model"], list(MODELS))

    async def test_stops_at_first_reliable_model(self) -> None:
        call = AsyncMock(return_value=COMPLETE)
        with patch.object(self.analyzer, "_call_gemini", call):
            await self.analyzer.analyze(b"test", "test.jpg")
        call.assert_awaited_once()

    async def test_low_confidence_and_invalid_data_escalate(self) -> None:
        call = AsyncMock(side_effect=[
            COMPLETE.replace("0.9", "0.69"), '{"total_amount":-1,"confidence":1}',
            LocalVisionError("empty", code=VisionFailureCode.EMPTY_RESULT), COMPLETE,
        ])
        with patch.object(self.analyzer, "_call_gemini", call):
            result = await self.analyzer.analyze(b"test", "test.jpg")
        self.assertEqual(call.await_count, 4)
        self.assertEqual(result.confidence, 0.9)

    async def test_preserves_best_partial_and_warns(self) -> None:
        notices = []
        call = AsyncMock(side_effect=[
            '{"merchant":"Partial","total_amount":12,"confidence":0.6}',
            '{"merchant":"Worse","confidence":0.99}',
        ] + ['{"confidence":0}'] * 6)
        with patch.object(self.analyzer, "_call_gemini", call):
            result = await self.analyzer.analyze_with_progress(b"test", "test.jpg", on_progress=notices.append)
        self.assertEqual(result.merchant, "Partial")
        self.assertIsNone(result.date)
        self.assertEqual(notices[-1].notice, "partial")

    async def test_auth_connection_and_bad_request_stop_without_escalation(self) -> None:
        for code in (VisionFailureCode.AUTHENTICATION, VisionFailureCode.CONNECTION, VisionFailureCode.INVALID_REQUEST):
            call = AsyncMock(side_effect=LocalVisionError("failure", code=code))
            with patch.object(self.analyzer, "_call_gemini", call):
                with self.assertRaises(LocalVisionError):
                    await self.analyzer.analyze(b"test", "test.jpg")
            call.assert_awaited_once()

    async def test_quota_and_missing_models_skip_and_cooldown_survives(self) -> None:
        call = AsyncMock(side_effect=[
            LocalVisionError("quota", code=VisionFailureCode.QUOTA),
            LocalVisionError("missing", code=VisionFailureCode.MODEL_NOT_FOUND), COMPLETE,
        ])
        with patch.object(self.analyzer, "_call_gemini", call):
            await self.analyzer.analyze(b"test", "test.jpg")
        call = AsyncMock(return_value=COMPLETE)
        self.analyzer._quota_store = GeminiQuotaStore(Path(self.temp.name) / "quota.sqlite3")
        with patch.object(self.analyzer, "_call_gemini", call):
            await self.analyzer.analyze(b"test", "test.jpg")
        self.assertEqual(call.await_args.kwargs["model"], MODELS[1])

    async def test_total_budget_keeps_validated_partial(self) -> None:
        self.analyzer._timeout_seconds = 0.1
        count = 0

        async def call(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return '{"merchant":"Partial","confidence":0.5}'
            await asyncio.sleep(10)
            return COMPLETE

        with patch.object(self.analyzer, "_call_gemini", side_effect=call):
            result = await self.analyzer.analyze(b"test", "test.jpg")
        self.assertEqual(result.merchant, "Partial")
        self.assertEqual(count, 2)

    async def test_total_budget_without_partial_reports_timeout(self) -> None:
        self.analyzer._timeout_seconds = 0.05

        async def call(*args, **kwargs):
            await asyncio.sleep(10)
            return COMPLETE

        with patch.object(self.analyzer, "_call_gemini", side_effect=call):
            with self.assertRaises(LocalVisionError) as caught:
                await self.analyzer.analyze(b"test", "test.jpg")
        self.assertEqual(caught.exception.code, VisionFailureCode.TIMEOUT)

    async def test_all_reserved_models_fail_as_quota_without_http(self) -> None:
        for model in MODELS:
            self.store.record_cooldown(model, 0, 9999999999)
        call = AsyncMock()
        with patch.object(self.analyzer, "_call_gemini", call):
            with self.assertRaises(LocalVisionError) as caught:
                await self.analyzer.analyze(b"test", "test.jpg")
        self.assertEqual(caught.exception.code, VisionFailureCode.QUOTA)
        call.assert_not_awaited()

    async def test_failed_quota_storage_stops_calls_with_explicit_error(self) -> None:
        call = AsyncMock()
        with (
            patch.object(self.store, "reserve", side_effect=sqlite3.OperationalError("synthetic")),
            patch.object(self.analyzer, "_call_gemini", call),
        ):
            with self.assertRaises(LocalVisionError) as caught:
                await self.analyzer.analyze(b"test", "test.jpg")
        self.assertEqual(caught.exception.code, VisionFailureCode.SERVICE_ERROR)
        call.assert_not_awaited()

    async def test_requests_use_each_model_url_without_exposing_key(self) -> None:
        urls = []
        real_client = httpx.AsyncClient

        def handler(request):
            urls.append(str(request.url))
            self.assertNotIn("synthetic-key", str(request.url))
            text = '{"confidence":0}' if len(urls) == 1 else COMPLETE
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

        with patch("vision.gemini_client.httpx.AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler))):
            await self.analyzer.analyze(b"test", "test.jpg")
        self.assertIn(MODELS[0], urls[0])
        self.assertIn(MODELS[1], urls[1])


class GeminiQuotaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "quota.sqlite3"
        self.store = GeminiQuotaStore(self.path)
        self.now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc).timestamp()

    def test_rolling_minute_and_restart(self) -> None:
        self.assertTrue(self.store.reserve("a", 1, 100, 80, self.now).allowed)
        block = GeminiQuotaStore(self.path).reserve("a", 1, 100, 80, self.now + 1)
        self.assertFalse(block.allowed)
        self.assertEqual(block.retry_at, self.now + 60)
        self.assertTrue(self.store.reserve("a", 1, 100, 80, self.now + 60).allowed)

    def test_daily_reset_and_independent_models(self) -> None:
        self.store.reserve("a", 100, 1, 80, self.now)
        block = self.store.reserve("a", 100, 1, 80, self.now + 61)
        self.assertEqual(block.retry_at, day_window(self.now)[1])
        self.assertTrue(self.store.reserve("b", 100, 1, 80, self.now).allowed)
        self.assertTrue(self.store.reserve("a", 100, 1, 80, block.retry_at).allowed)

    def test_day_window_handles_dst(self) -> None:
        spring = datetime(2026, 3, 8, 12, tzinfo=timezone.utc).timestamp()
        autumn = datetime(2026, 11, 1, 12, tzinfo=timezone.utc).timestamp()
        self.assertEqual(day_window(spring)[1] - day_window(spring)[0], 23 * 3600)
        self.assertEqual(day_window(autumn)[1] - day_window(autumn)[0], 25 * 3600)

    def test_warning_is_at_configured_threshold_not_every_request(self) -> None:
        results = [self.store.reserve("a", 5, 100, 80, self.now + i) for i in range(5)]
        self.assertEqual([item.warning for item in results], [False, False, False, True, False])

    def test_atomic_reservations_across_connections(self) -> None:
        def reserve(_):
            return GeminiQuotaStore(self.path).reserve("a", 3, 100, 80, self.now).allowed
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(12))), 3)

    def test_remote_cooldown_with_and_without_retry(self) -> None:
        self.store.record_cooldown("a", self.now, self.now + 120)
        self.store.record_cooldown("a", self.now, self.now + 30)
        reservation = self.store.reserve("a", 100, 100, 80, self.now)
        self.assertEqual(reservation.retry_at, self.now + 120)
        self.assertTrue(reservation.remote)
        self.assertTrue(reservation.known_retry)
        self.store.record_cooldown("b", self.now, None)
        unknown = self.store.reserve("b", 100, 100, 80, self.now)
        self.assertFalse(unknown.known_retry)
        self.assertTrue(self.store.reserve("b", 100, 100, 80, day_window(self.now)[1]).allowed)

    def test_retry_parser_handles_safe_values_and_google_details(self) -> None:
        response = httpx.Response(429, headers={"Retry-After": "12"}, json={"error": {"details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30.5s"},
        ]}})
        self.assertEqual(_retry_at(response, self.now), self.now + 30.5)
        for invalid in ("NaN", "inf", "-1", "garbage"):
            self.assertIsNone(_retry_at(httpx.Response(429, headers={"Retry-After": invalid}), self.now))
        self.assertEqual(
            _retry_at(httpx.Response(429, headers={"Retry-After": "Mon, 26 Oct 2026 12:01:00 GMT"}), self.now),
            self.now + 60,
        )

    def test_user_messages_distinguish_unknown_remote_reset_and_local_counter(self) -> None:
        text = messages.gemini_notice("remote_limit", "test", None, "Europe/Rome")
        self.assertIn("non indica quando", text)
        self.assertIn("non e' un reset garantito", text)
        local = messages.gemini_notice("local_limit", "test", self.now + 60, "Europe/Rome")
        self.assertIn("limite configurato del bot", local)
        self.assertIn("13:01:00", local)

    def test_status_reads_counts_without_reserving_or_resetting_and_survives_restart(self) -> None:
        self.store.reserve("a", 5, 10, 80, self.now - 61)
        self.store.reserve("a", 5, 10, 80, self.now - 30)
        self.store.reserve("a", 5, 10, 80, self.now)
        reopened = GeminiQuotaStore(self.path)
        for _ in range(2):
            status, unused = reopened.status(("a", "b"), (5, 1), (10, 1), self.now)
            self.assertEqual((status.minute_requests, status.daily_requests), (2, 3))
            self.assertEqual((unused.minute_requests, unused.daily_requests), (0, 0))
            self.assertIsNone(status.local_retry_at)
        expired = reopened.status(("a",), (5,), (10,), self.now + 60)[0]
        self.assertEqual(expired.minute_requests, 0)
        self.assertEqual(expired.daily_requests, 3)
        reset = reopened.status(("a",), (5,), (10,), day_window(self.now)[1])[0]
        self.assertEqual(reset.daily_requests, 0)

    def test_status_reports_local_wait_after_lowered_limit_and_remote_cooldowns(self) -> None:
        for delta in (-40, -20, 0):
            self.store.reserve("a", 10, 100, 80, self.now + delta)
        self.store.record_cooldown("a", self.now, self.now + 90)
        self.store.record_cooldown("b", self.now, None)
        a, b = self.store.status(("a", "b"), (1, 1), (100, 1), self.now)
        self.assertEqual(a.local_retry_at, self.now + 60)
        self.assertEqual(a.remote_until_at, self.now + 90)
        self.assertTrue(a.remote_retry_known)
        self.assertFalse(b.remote_retry_known)
        self.assertEqual(b.remote_until_at, day_window(self.now)[1])
        after = self.store.status(("a",), (1,), (100,), self.now + 90)[0]
        self.assertIsNone(after.remote_until_at)
        self.assertIsNone(after.local_retry_at)
        daily = self.store.status(("a",), (100,), (1,), self.now)[0]
        self.assertEqual(daily.local_retry_at, day_window(self.now)[1])


class GeminiChainConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = {
            "BOT_TOKEN": "synthetic-token", "ALLOWED_USER_IDS": "1", "ADMIN_USER_ID": "1",
            "GEMINI_API_KEY": "synthetic-key", "GEMINI_MODELS": ",".join(MODELS),
            "GEMINI_RPM_LIMITS": "30,30,30,10,10,15,2,2",
            "GEMINI_RPD_LIMITS": "1500,1500,1500,1500,1500,1500,50,50",
        }

    def test_loads_all_models_and_aligned_limits(self) -> None:
        settings = Settings.from_env(self.env)
        self.assertEqual(settings.gemini_models, MODELS)
        self.assertEqual(settings.gemini_rpm_limits, (30, 30, 30, 10, 10, 15, 2, 2))
        self.assertEqual(settings.gemini_quota_warn_percent, 80)

    def test_rejects_misaligned_invalid_limits_and_ids(self) -> None:
        for name, value in (
            ("GEMINI_RPM_LIMITS", "1"), ("GEMINI_RPD_LIMITS", ""),
            ("GEMINI_RPM_LIMITS", "0,1,1,1,1,1,1,1"),
            ("GEMINI_MODELS", "a,a"), ("GEMINI_MODELS", "../secret"),
            ("GEMINI_API_KEY", ""), ("GEMINI_QUOTA_WARN_PERCENT", "101"),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env({**self.env, name: value})

    def test_legacy_single_model_does_not_enable_chain(self) -> None:
        env = {key: value for key, value in self.env.items() if key not in {
            "GEMINI_MODELS", "GEMINI_RPM_LIMITS", "GEMINI_RPD_LIMITS",
        }}
        env["GEMINI_MODEL"] = "legacy"
        self.assertEqual(Settings.from_env(env).gemini_models, ())
        self.assertEqual(Settings.from_env(env).gemini_model, "legacy")
