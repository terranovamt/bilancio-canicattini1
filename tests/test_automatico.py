import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_update import PROJECT_ROOT, SHELL
from bot.updates import RunningVersion, UpdateStatusStore, pending_notices

MOCK_UPDATER = r"""#!/bin/sh
printf "updated\n" >> "$MOCK_RESULT"
revision=$(git -C "$(dirname "$0")" rev-parse HEAD)
previous=$(sed -n 's/^installed_revision=//p' "$1/data/update/install" 2>/dev/null || true)
if [ "${MOCK_UPDATE_EXIT:-0}" = "0" ]; then
    printf 'result=ok\nfinished_at=2026-10-29T12:00:00Z\nrevision=%s\ninstalled_revision=%s\n' \
        "$revision" "$revision" > "$1/data/update/install"
else
    printf 'result=rolled_back\nfinished_at=2026-10-29T12:00:00Z\nrevision=%s\ninstalled_revision=%s\nreason=start\n' \
        "$revision" "$previous" > "$1/data/update/install"
fi
exit "${MOCK_UPDATE_EXIT:-0}"
"""


def read_fields(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    return fields


@unittest.skipIf(SHELL is None or shutil.which("git") is None, "Git and sh are required")
class AutomaticUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.source = self.root / "source"
        self.target = self.root / "installed"
        self.tools = self.root / "tools"
        self.status = self.target / "data" / "update"
        (self.target / "data").mkdir(parents=True)
        self.tools.mkdir()
        (self.target / ".env").write_text("private placeholder", encoding="utf-8")
        self.git("init", "--bare", str(self.remote))
        self.git("clone", str(self.remote), str(self.source))
        self.git("-C", str(self.source), "checkout", "-b", "deploy")
        shutil.copyfile(PROJECT_ROOT / "automatico.sh", self.source / "automatico.sh")
        (self.source / "aggiorna.sh").write_text(MOCK_UPDATER, encoding="utf-8", newline="\n")
        self.commit(self.source, "Prima versione")
        self.git("-C", str(self.source), "push", "-u", "origin", "deploy")
        sudo = self.tools / "sudo"
        sudo.write_text(
            '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do case "$1" in\n'
            '  -u) shift 2 ;; -H) shift ;; *) break ;;\nesac; done\nexec "$@"\n',
            encoding="utf-8", newline="\n",
        )
        sudo.chmod(0o755)
        assert SHELL is not None
        self.result_file = self.root / "result"
        self.env = {
            **os.environ,
            "PATH": os.pathsep.join(
                (str(self.tools), str(Path(SHELL).parent), os.environ.get("PATH", ""))
            ),
            "MOCK_RESULT": self.result_file.as_posix(),
        }

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True, timeout=30
        )

    def commit(self, repository: Path, message: str) -> None:
        self.git("-C", str(repository), "add", ".")
        self.git(
            "-C", str(repository), "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid", "commit", "-m", message,
        )

    def head(self, repository: Path) -> str:
        return self.git("-C", str(repository), "rev-parse", "HEAD").stdout.strip()

    def publish(self, message: str) -> str:
        publisher = self.root / "publisher"
        if not publisher.exists():
            self.git("clone", "--branch", "deploy", str(self.remote), str(publisher))
        (publisher / "release.txt").write_text(message, encoding="utf-8")
        self.commit(publisher, message)
        self.git("-C", str(publisher), "push", "origin", "deploy")
        return self.head(publisher)

    def run_auto(self, mode: str, **env: str) -> subprocess.CompletedProcess[str]:
        assert SHELL is not None
        return subprocess.run(
            [SHELL, (self.source / "automatico.sh").as_posix(), mode, self.target.as_posix()],
            env={**self.env, **env}, capture_output=True, text=True,
            check=False, timeout=120,
        )

    def updates(self) -> int:
        if not self.result_file.exists():
            return 0
        return len(self.result_file.read_text().splitlines())

    def request(self) -> None:
        self.status.mkdir(exist_ok=True)
        (self.status / "request").write_text("requested_at=2026-10-29T12:00:00Z\n")

    def test_check_records_available_version_without_installing(self) -> None:
        result = self.run_auto("controlla")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        check = read_fields(self.status / "check")
        self.assertEqual(check["available_revision"], self.head(self.source))
        self.assertIn("Prima versione", check["available_summary"])
        self.assertEqual(check["error"], "")
        self.assertEqual(self.updates(), 0)
        self.assertFalse((self.source / ".git" / "nas-deploy.lock").exists())

    def test_repeated_checks_detect_new_git_commit_without_installing_or_repeating_notice(self) -> None:
        running_revision = self.head(self.source)
        published_revision = self.publish("Nuovo aggiornamento")
        store = UpdateStatusStore(self.status)
        running = RunningVersion(running_revision, None, None)

        for check_number in range(2):
            result = self.run_auto("controlla")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                read_fields(self.status / "check")["available_revision"],
                published_revision,
            )
            notices, marker = pending_notices(store, running)
            self.assertEqual(len(notices), 1 if check_number == 0 else 0)
            if notices:
                self.assertIn("Nuovo aggiornamento", notices[0])
                store.write_notified(marker)
            self.assertEqual(self.head(self.source), running_revision)
            self.assertEqual(self.updates(), 0)
            self.assertFalse((self.status / "request").exists())

    def test_telegram_request_installs_new_version_and_is_consumed(self) -> None:
        self.run_auto("installa")
        expected = self.publish("Seconda versione")
        self.request()
        result = self.run_auto("controlla")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.head(self.source), expected)
        self.assertEqual(read_fields(self.status / "install")["installed_revision"], expected)
        self.assertFalse((self.status / "request").exists())
        self.assertEqual(self.updates(), 2)

    def test_scheduled_install_runs_once_per_version(self) -> None:
        for _ in range(2):
            result = self.run_auto("installa")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.updates(), 1)
        self.assertIn("gia installata", result.stdout)
        self.publish("Nuova versione")
        self.assertEqual(self.run_auto("installa").returncode, 0)
        self.assertEqual(self.updates(), 2)

    def test_failed_version_is_not_retried_automatically_but_can_be_requested(self) -> None:
        result = self.run_auto("installa", MOCK_UPDATE_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(read_fields(self.status / "install")["result"], "rolled_back")
        result = self.run_auto("installa")
        self.assertEqual(result.returncode, 0)
        self.assertIn("non si e installata in precedenza", result.stdout)
        self.assertEqual(self.updates(), 1)
        self.request()
        self.assertEqual(self.run_auto("controlla").returncode, 0)
        self.assertEqual(self.updates(), 2)
        self.assertEqual(read_fields(self.status / "install")["result"], "ok")

    def test_request_when_up_to_date_is_answered(self) -> None:
        self.run_auto("installa")
        self.request()
        result = self.run_auto("controlla")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        install = read_fields(self.status / "install")
        self.assertEqual(install["result"], "skipped")
        self.assertEqual(install["reason"], "up_to_date")
        self.assertEqual(self.updates(), 1)

    def test_download_failure_is_recorded(self) -> None:
        self.git("-C", str(self.source), "remote", "set-url", "origin", str(self.root / "missing"))
        self.request()
        result = self.run_auto("controlla")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(read_fields(self.status / "check")["error"], "download")
        self.assertEqual(read_fields(self.status / "install")["reason"], "download")
        self.assertEqual(self.updates(), 0)

    def test_busy_lock_skips_quietly(self) -> None:
        lock = self.source / ".git" / "nas-deploy.lock"
        lock.mkdir()
        result = self.run_auto("installa")
        self.assertEqual(result.returncode, 0)
        self.assertIn("gia in corso", result.stdout)
        self.assertEqual(self.updates(), 0)
        self.assertTrue(lock.exists())

    def test_dirty_checkout_is_not_overwritten(self) -> None:
        (self.source / "local.txt").write_text("local change", encoding="utf-8")
        result = self.run_auto("installa")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(read_fields(self.status / "install")["reason"], "dirty")
        self.assertEqual(self.updates(), 0)

    def test_rejects_unknown_mode(self) -> None:
        result = self.run_auto("sempre")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Modalita sconosciuta", result.stderr)


if __name__ == "__main__":
    unittest.main()
