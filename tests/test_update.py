import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def find_shell() -> str | None:
    shell = shutil.which("sh")
    if shell is not None:
        return shell
    git = shutil.which("git")
    if git is not None:
        candidate = Path(git).resolve().parents[1] / "usr" / "bin" / "sh.exe"
        if candidate.is_file():
            return str(candidate)
    return None


SHELL = find_shell()


@unittest.skipIf(SHELL is None, "A POSIX shell is needed to test the NAS updater")
class UpdateScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "new package"
        self.target = self.root / "installed project"
        self.mock_bin = self.root / "tools"
        for folder in (self.source, self.target, self.mock_bin):
            folder.mkdir()
        self.files = (
            "Dockerfile", "docker-compose.yml", "requirements.txt", "config.py",
            "logging_config.py", ".dockerignore", "README.md",
        )
        self.folders = ("bot", "bilancio", "db", "storage", "vision", "docs")
        for name in self.files:
            (self.source / name).write_text("new\n", encoding="utf-8")
            (self.target / name).write_text("old\n", encoding="utf-8")
        for name in self.folders:
            for root, content in ((self.source, "new\n"), (self.target, "old\n")):
                (root / name).mkdir()
                (root / name / "example.py").write_text(content, encoding="utf-8")
        shutil.copyfile(PROJECT_ROOT / "aggiorna.sh", self.source / "aggiorna.sh")
        shutil.copyfile(PROJECT_ROOT / "scarica.sh", self.source / "scarica.sh")
        shutil.copyfile(PROJECT_ROOT / "automatico.sh", self.source / "automatico.sh")
        self.protected = [self.target / ".env"]
        (self.target / ".env").write_text("private placeholder\n", encoding="utf-8")
        for name in ("data", "scontrini", "ollama-data"):
            (self.target / name).mkdir()
            protected = self.target / name / "preserve"
            protected.write_bytes(b"persistent placeholder")
            self.protected.append(protected)
        self.before = {path: path.read_bytes() for path in self.protected}
        self.calls = self.root / "docker-calls"
        docker = self.mock_bin / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$MOCK_CALLS"\n'
            'case "$*" in\n'
            '  "compose ps --status running -q bot")\n'
            '    [ "${MOCK_PS_FAIL:-0}" = "0" ] || exit 1\n'
            '    if [ "${MOCK_BOT_STOPPED:-0}" = "0" ]; then printf "test-container\\n"; fi ;;\n'
            '  "compose exec "*) printf "%s\\n" "${MOCK_RUNNING_DRY_RUN:-true}" ;;\n'
            '  "compose run "*) [ "${MOCK_CONFIG_FAIL:-0}" = "0" ] ;;\n'
            '  "compose build "*) [ "${MOCK_BUILD_FAIL:-0}" = "0" ] ;;\n'
            '  "compose ps -a -q bot")\n'
            '    if [ "${MOCK_NO_CONTAINER:-0}" = "0" ]; then printf "test-container\\n"; fi ;;\n'
            '  "compose ps -q bot") printf "test-container\\n" ;;\n'
            '  "compose up -d --no-deps --force-recreate bot") [ "${MOCK_UP_FAIL:-0}" = "0" ] ;;\n'
            '  "inspect --format {{.Image}} "*) printf "sha256:old\\n" ;;\n'
            '  "inspect --format {{.Config.Image}} "*) printf "test-image\\n" ;;\n'
            '  inspect*)\n'
            '    if grep -q "^image tag test-image:rollback test-image$" "$MOCK_CALLS"; then\n'
            '      printf "%s\\n" "${MOCK_ROLLBACK_STATUS:-running healthy}"\n'
            '    else printf "%s\\n" "${MOCK_STATUS:-running healthy}"; fi ;;\n'
            '  *) exit 0 ;;\n'
            "esac\n",
            encoding="utf-8",
            newline="\n",
        )
        docker.chmod(0o755)
        assert SHELL is not None
        self.env = {
            **os.environ,
            "PATH": os.pathsep.join(
                (str(self.mock_bin), str(Path(SHELL).parent), os.environ.get("PATH", ""))
            ),
            "MOCK_CALLS": self.calls.as_posix(),
        }

    def run_update(self, **environment: str) -> subprocess.CompletedProcess[str]:
        assert SHELL is not None
        return subprocess.run(
            [SHELL, self.source.joinpath("aggiorna.sh").as_posix(), self.target.as_posix()],
            env={**self.env, **environment},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def assert_private_files_preserved(self) -> None:
        for path, content in self.before.items():
            self.assertEqual(path.read_bytes(), content)

    def install_status(self) -> dict[str, str]:
        path = self.target / "data" / "update" / "install"
        fields: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            fields[key] = value
        return fields

    def assert_code_is(self, content: str) -> None:
        for name in self.files:
            self.assertEqual((self.target / name).read_text(encoding="utf-8"), content)
        for name in self.folders:
            self.assertEqual(
                (self.target / name / "example.py").read_text(encoding="utf-8"), content
            )

    def test_updates_program_and_backs_up_code_without_private_data(self) -> None:
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Aggiornamento installato", result.stdout)
        self.assert_private_files_preserved()
        for name in self.files:
            self.assertEqual((self.target / name).read_text(encoding="utf-8"), "new\n")
        backups = list((self.target / ".update-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "config.py").read_text(encoding="utf-8"), "old\n")
        self.assertFalse((backups[0] / ".env").exists())
        self.assertFalse((backups[0] / "data").exists())
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("compose build --build-arg APP_REVISION= ", calls)
        self.assertIn("image tag sha256:old test-image:rollback", calls)
        self.assertIn("compose up -d --no-deps --force-recreate bot", calls)
        self.assertNotIn("down", calls)
        self.assertEqual(self.install_status()["result"], "ok")
        self.assertFalse((self.target / ".update.lock").exists())

    def test_updates_live_running_bot_without_changing_mode(self) -> None:
        result = self.run_update(MOCK_RUNNING_DRY_RUN="false")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Aggiornamento installato", result.stdout)
        self.assert_private_files_preserved()

    def test_stopped_bot_updates_without_exec_and_checks_next_configuration(self) -> None:
        result = self.run_update(MOCK_BOT_STOPPED="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertNotIn("compose exec", calls)
        self.assertIn("compose run --rm --no-deps --entrypoint python bot", calls)
        self.assertIn("compose up -d --no-deps --force-recreate bot", calls)
        self.assert_private_files_preserved()

    def test_stopped_bot_with_invalid_next_configuration_is_rejected(self) -> None:
        result = self.run_update(MOCK_BOT_STOPPED="1", MOCK_CONFIG_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prossimo avvio", result.stderr)
        self.assertFalse((self.target / ".update-backups").exists())
        self.assert_private_files_preserved()

    def test_status_query_failure_is_not_treated_as_stopped_bot(self) -> None:
        result = self.run_update(MOCK_PS_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stato del bot", result.stderr)
        self.assertFalse((self.target / ".update-backups").exists())
        self.assert_private_files_preserved()

    def test_refuses_invalid_configuration_for_next_start(self) -> None:
        result = self.run_update(MOCK_CONFIG_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prossimo avvio", result.stderr)
        self.assertFalse((self.target / ".update-backups").exists())
        self.assert_private_files_preserved()

    def test_build_failure_does_not_recreate_container(self) -> None:
        result = self.run_update(MOCK_BUILD_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Costruzione fallita", result.stderr)
        self.assertNotIn("compose up", self.calls.read_text(encoding="utf-8"))
        self.assert_code_is("old\n")
        self.assertEqual(self.install_status()["result"], "failed")
        self.assertEqual(self.install_status()["reason"], "build")
        self.assert_private_files_preserved()

    def test_unhealthy_container_is_not_reported_as_success(self) -> None:
        result = self.run_update(MOCK_STATUS="running unhealthy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Container non pronto", result.stderr)
        self.assertIn("ripristinata e riavviata la versione precedente", result.stderr)
        self.assertNotIn("Aggiornamento installato", result.stdout)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("image tag test-image:rollback test-image", calls)
        self.assertIn("compose up -d --no-deps --force-recreate --no-build bot", calls)
        self.assert_code_is("old\n")
        self.assertEqual(self.install_status()["result"], "rolled_back")
        self.assertEqual(self.install_status()["reason"], "start")
        self.assert_private_files_preserved()

    def test_failed_start_rolls_back_without_restarting_stopped_bot(self) -> None:
        result = self.run_update(MOCK_BOT_STOPPED="1", MOCK_UP_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("compose up --no-start --no-deps --force-recreate --no-build bot", calls)
        self.assertNotIn("compose up -d --no-deps --force-recreate --no-build bot", calls)
        self.assert_code_is("old\n")
        self.assertEqual(self.install_status()["result"], "rolled_back")

    def test_failed_rollback_is_reported_for_manual_check(self) -> None:
        result = self.run_update(
            MOCK_STATUS="running unhealthy", MOCK_ROLLBACK_STATUS="exited"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Anche la versione precedente", result.stderr)
        self.assertEqual(self.install_status()["result"], "failed")
        self.assertEqual(self.install_status()["reason"], "rollback")

    def test_without_previous_image_failure_is_not_hidden(self) -> None:
        result = self.run_update(MOCK_NO_CONTAINER="1", MOCK_STATUS="exited")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nessuna immagine precedente", result.stderr)
        self.assertEqual(self.install_status()["reason"], "rollback")
        self.assert_code_is("old\n")

    def test_configuration_refusal_is_recorded_for_telegram(self) -> None:
        result = self.run_update(MOCK_CONFIG_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.install_status()["reason"], "config")

    def test_git_revision_is_built_into_image_and_recorded(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("Git is required")
        for command in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
             "commit", "-q", "-m", "Versione di prova"],
        ):
            subprocess.run(["git", "-C", str(self.source), *command], check=True,
                           capture_output=True, timeout=30)
        revision = subprocess.run(
            ["git", "-C", str(self.source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn(f"--build-arg APP_REVISION={revision} ", calls)
        self.assertIn("--build-arg APP_COMMIT_SUBJECT=Versione di prova bot", calls)
        status = self.install_status()
        self.assertEqual(status["installed_revision"], revision)
        self.assertIn("Versione di prova", status["summary"])

    def test_stale_lock_from_crashed_update_is_removed(self) -> None:
        lock = self.target / ".update.lock"
        lock.mkdir()
        (lock / "pid").write_text("999999\n", encoding="utf-8")
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("blocco lasciato", result.stdout)
        self.assertFalse(lock.exists())

    def test_refuses_package_extracted_inside_live_project(self) -> None:
        inside = self.target / "extracted"
        shutil.copytree(self.source, inside)
        self.source = inside
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Estrai lo ZIP fuori", result.stderr)
        self.assertFalse(self.calls.exists())
        self.assert_private_files_preserved()

    def test_incomplete_package_does_not_modify_installed_code(self) -> None:
        (self.source / "config.py").unlink()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Pacchetto incompleto", result.stderr)
        self.assertEqual((self.target / "config.py").read_text(encoding="utf-8"), "old\n")
        self.assert_private_files_preserved()

    def test_refuses_concurrent_update(self) -> None:
        (self.target / ".update.lock").mkdir()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gia in corso", result.stderr)
        self.assert_private_files_preserved()


if __name__ == "__main__":
    unittest.main()
