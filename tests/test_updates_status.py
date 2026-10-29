import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot import messages
from bot.updates import (
    RunningVersion,
    UpdateStatusStore,
    describe_version,
    install_notice,
    pending_notices,
    resolve_timezone,
)

OLD = "a" * 40
NEW = "b" * 40


class UpdateStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "update"
        self.store = UpdateStatusStore(self.directory)
        self.zone = resolve_timezone("UTC")
        self.running = RunningVersion.from_env(
            {
                "APP_REVISION": OLD,
                "APP_COMMIT_DATE": "2026-10-29T12:00:00+00:00",
                "APP_COMMIT_SUBJECT": "Versione attuale",
            }
        )

    def write(self, name: str, **fields: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / name).write_text(
            "".join(f"{key}={value}\n" for key, value in fields.items()), encoding="utf-8"
        )

    def describe(self) -> str:
        return describe_version(
            self.running,
            self.store.read_check(),
            self.store.read_install(),
            request_pending=self.store.has_request(),
            zone=self.zone,
        )

    def test_running_version_comes_from_image_environment(self) -> None:
        self.assertEqual(self.running.revision, OLD)
        text = self.describe()
        self.assertIn("Versione attiva: aaaaaaa del 29/10/2026 12:00 – Versione attuale", text)
        self.assertIn(messages.VERSION_NOT_CHECKED, text)

    def test_invalid_revision_is_reported_as_unknown(self) -> None:
        self.running = RunningVersion.from_env({"APP_REVISION": "not a commit"})
        self.assertIn(messages.VERSION_UNKNOWN, self.describe())

    def test_new_version_is_announced(self) -> None:
        self.write(
            "check",
            checked_at="2026-10-29T13:00:00Z",
            available_revision=NEW,
            available_summary="bbbbbbb - Nuova funzione (29/10/2026 13:00)",
            error="",
        )
        text = self.describe()
        self.assertIn("Nuova versione disponibile: bbbbbbb - Nuova funzione", text)
        self.assertIn("/aggiorna", text)

    def test_same_version_is_reported_as_latest(self) -> None:
        self.write("check", checked_at="2026-10-29T13:00:00Z", available_revision=OLD)
        self.assertIn("È l'ultima versione (controllo del 29/10/2026 13:00)", self.describe())

    def test_failed_fetch_does_not_announce_cached_revision(self) -> None:
        self.write(
            "check", checked_at="2026-10-29T13:00:00Z",
            available_revision=NEW, available_summary="bbbbbbb - Cached",
            error="download",
        )
        self.assertIn(messages.VERSION_CHECK_FAILED.split("{checked}")[0], self.describe())
        self.assertNotIn("Nuova versione disponibile", self.describe())
        self.assertEqual(pending_notices(self.store, self.running)[0], [])
        self.assertNotIn("available_revision", pending_notices(self.store, self.running)[1])

        self.write(
            "check", checked_at="2026-10-29T13:10:00Z",
            available_revision=NEW, available_summary="bbbbbbb - Verified", error="",
        )
        notices, marker = pending_notices(self.store, self.running)
        self.assertEqual(len(notices), 1)
        self.assertIn("Verified", notices[0])
        self.assertEqual(marker["available_revision"], NEW)

    def test_failed_check_and_failed_install_are_visible(self) -> None:
        self.write("check", checked_at="2026-10-29T13:00:00Z", available_revision=OLD, error="download")
        self.write(
            "install", result="rolled_back", finished_at="2026-10-29T13:05:00Z",
            revision=NEW, summary="bbbbbbb - Rotta", reason="start",
        )
        text = self.describe()
        self.assertIn("ultimo controllo degli aggiornamenti non è riuscito", text)
        self.assertIn("Ultimo aggiornamento non riuscito (bbbbbbb - Rotta)", text)
        self.assertIn(messages.UPDATE_REASONS["start"], text)

    def test_request_is_written_and_reported_as_pending(self) -> None:
        self.store.request_install(datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
        self.assertTrue(self.store.has_request())
        self.assertIn(
            "requested_at=2026-10-29T12:00:00Z",
            (self.directory / "request").read_text(encoding="utf-8"),
        )
        self.assertIn(messages.VERSION_REQUEST_PENDING, self.describe())

    def test_notices_are_sent_once_per_install_and_new_revision(self) -> None:
        self.write(
            "install", result="ok", finished_at="2026-10-29T13:05:00Z",
            revision=OLD, summary="aaaaaaa - Versione attuale",
        )
        self.write("check", checked_at="2026-10-29T14:00:00Z", available_revision=NEW,
                   available_summary="bbbbbbb - Dopo")
        notices, marker = pending_notices(self.store, self.running)
        self.assertEqual(len(notices), 2)
        self.assertIn("Aggiornamento installato: aaaaaaa", notices[0])
        self.assertIn("bbbbbbb - Dopo", notices[1])
        self.store.write_notified(marker)
        self.assertEqual(pending_notices(self.store, self.running)[0], [])

    def test_install_notice_texts(self) -> None:
        self.write(
            "install", result="skipped", finished_at="2026-10-29T13:05:00Z", reason="up_to_date"
        )
        install = self.store.read_install()
        assert install is not None
        self.assertIn(messages.UPDATE_REASONS["up_to_date"], install_notice(install))

    def test_malformed_files_are_ignored(self) -> None:
        self.write("install", result="hacked", finished_at="ieri")
        self.write("check", checked_at="non valido")
        self.assertIsNone(self.store.read_install())
        self.assertIsNone(self.store.read_check())

    def test_control_characters_are_removed_from_summaries(self) -> None:
        self.write("check", checked_at="2026-10-29T13:00:00Z", available_revision=NEW,
                   available_summary="bbbbbbb - riga\x1bstrana")
        check = self.store.read_check()
        assert check is not None
        self.assertEqual(check.available_summary, "bbbbbbb - riga strana")


if __name__ == "__main__":
    unittest.main()
