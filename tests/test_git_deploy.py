import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_update import PROJECT_ROOT, SHELL


@unittest.skipIf(SHELL is None or shutil.which("git") is None, "Git and sh are required")
class GitDeployTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.source = self.root / "source"
        self.target = self.root / "installed"
        self.tools = self.root / "tools"
        self.target.mkdir()
        self.tools.mkdir()
        (self.target / ".env").write_text("private placeholder", encoding="utf-8")
        self.git("init", "--bare", str(self.remote))
        self.git("clone", str(self.remote), str(self.source))
        self.git("-C", str(self.source), "checkout", "-b", "deploy")
        shutil.copyfile(PROJECT_ROOT / "scarica.sh", self.source / "scarica.sh")
        (self.source / "aggiorna.sh").write_text(
            '#!/bin/sh\nprintf "updated\\n" >> "$MOCK_RESULT"\n'
            'exit "${MOCK_UPDATE_EXIT:-0}"\n', encoding="utf-8", newline="\n"
        )
        self.git("-C", str(self.source), "add", ".")
        self.git(
            "-C", str(self.source), "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid", "commit", "-m", "Fixture"
        )
        self.git("-C", str(self.source), "push", "-u", "origin", "deploy")
        for name, body in {
            "id": '#!/bin/sh\nprintf "1000\\n"\n',
            "sudo": '#!/bin/sh\nexec "$@"\n',
        }.items():
            path = self.tools / name
            path.write_text(body, encoding="utf-8", newline="\n")
            path.chmod(0o755)
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

    def deploy(self, **env: str) -> subprocess.CompletedProcess[str]:
        assert SHELL is not None
        return subprocess.run(
            [SHELL, (self.source / "scarica.sh").as_posix(), self.target.as_posix()],
            env={**self.env, **env}, capture_output=True, text=True,
            check=False, timeout=30,
        )

    def test_manual_fetch_installs_and_retries_same_revision(self) -> None:
        for _ in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Versione installata:", result.stdout)
        self.assertEqual(self.result_file.read_text().splitlines(), ["updated", "updated"])
        self.assertEqual((self.target / ".env").read_text(), "private placeholder")
        self.assertFalse((self.source / ".git" / "nas-deploy.lock").exists())

    def test_dirty_checkout_is_not_overwritten(self) -> None:
        (self.source / "scarica.sh").write_text(
            (self.source / "scarica.sh").read_text() + "\n", newline="\n"
        )
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("modifiche locali", result.stderr)
        self.assertFalse(self.result_file.exists())

    def test_fetches_new_published_revision_before_installing(self) -> None:
        publisher = self.root / "publisher"
        self.git("clone", "--branch", "deploy", str(self.remote), str(publisher))
        (publisher / "release.txt").write_text("new release", encoding="utf-8")
        self.git("-C", str(publisher), "add", "release.txt")
        self.git(
            "-C", str(publisher), "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid", "commit", "-m", "New release"
        )
        self.git("-C", str(publisher), "push", "origin", "deploy")
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.source / "release.txt").read_text(), "new release")
        expected = self.git("-C", str(publisher), "rev-parse", "HEAD").stdout.strip()
        self.assertIn(f"Versione installata: {expected}", result.stdout)
        short = self.git("-C", str(publisher), "rev-parse", "--short", "HEAD").stdout.strip()
        self.assertIn(f"Ultimo commit installato: {short} - New release (", result.stdout)

    def test_refuses_concurrent_deploy(self) -> None:
        (self.source / ".git" / "nas-deploy.lock").mkdir()
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gia in corso", result.stderr)
        self.assertFalse(self.result_file.exists())

    def test_failed_installation_is_not_reported_as_deployed(self) -> None:
        result = self.deploy(MOCK_UPDATE_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Versione installata:", result.stdout)
        self.assertNotIn("Ultimo commit installato:", result.stdout)
        self.assertFalse((self.source / ".git" / "nas-deploy.lock").exists())

    def test_failed_fetch_does_not_run_updater(self) -> None:
        self.git("-C", str(self.source), "remote", "set-url", "origin", str(self.root / "missing"))
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Download fallito", result.stderr)
        self.assertFalse(self.result_file.exists())


if __name__ == "__main__":
    unittest.main()
