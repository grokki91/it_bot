# -*- coding: utf-8 -*-
"""Автодеплой: юниты systemd и поведение deploy/autoupdate.sh."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import cli  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "autoupdate.sh"


class UnitCase(unittest.TestCase):
    """Команда autoupdate печатает готовые к установке файлы."""

    def generate(self, argv):
        args = cli.build_parser().parse_args(argv)
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(args.func(args), 0)
        return out.getvalue()

    def test_defaults(self):
        text = self.generate(["autoupdate"])
        self.assertIn("ExecStart=%s" % SCRIPT, text)
        self.assertIn("Environment=ND_BRANCH=main", text)
        self.assertIn("OnUnitActiveSec=5min", text)
        self.assertIn("Unit=newsdigest-update.service", text)

    def test_options(self):
        text = self.generate(["autoupdate", "--branch", "master",
                              "--minutes", "15", "--service", "nd"])
        self.assertIn("Environment=ND_BRANCH=master", text)
        self.assertIn("Environment=ND_SERVICE=nd", text)
        self.assertIn("OnUnitActiveSec=15min", text)

    def test_files_written(self):
        self.generate(["autoupdate"])
        for name in ("newsdigest-update.service", "newsdigest-update.timer"):
            self.assertTrue((cli.HOME / name).exists(), name)

    def test_script_is_executable(self):
        self.assertTrue(os.access(str(SCRIPT), os.X_OK))


@unittest.skipUnless(shutil.which("git") and shutil.which("sh"), "нужны git и sh")
class ScriptCase(unittest.TestCase):
    """Скрипт обновления: сеть и systemd не нужны, всё на локальных репозиториях."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ndauto-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.origin = self.tmp / "origin"
        self.work = self.tmp / "work"
        self.git(self.tmp, "init", "-q", "-b", "main", "origin")
        (self.origin / "code.py").write_text("one\n", encoding="utf-8")
        self.commit(self.origin, "первый")
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.work))
        (self.work / "deploy").mkdir()
        shutil.copy(str(SCRIPT), str(self.work / "deploy" / "autoupdate.sh"))

    def git(self, cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        return subprocess.run(("git",) + args, cwd=str(cwd), env=env,
                              check=True, capture_output=True, text=True)

    def commit(self, repo, message):
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", message)

    def push(self, name, text, message="правка"):
        (self.origin / name).write_text(text, encoding="utf-8")
        self.commit(self.origin, message)

    def update(self):
        env = dict(os.environ, ND_SERVICE="нет-такого-юнита")
        return subprocess.run(["sh", str(self.work / "deploy" / "autoupdate.sh")],
                              env=env, capture_output=True, text=True)

    def head(self):
        return self.git(self.work, "rev-parse", "HEAD").stdout.strip()

    def test_no_commits_is_silent(self):
        before = self.head()
        res = self.update()
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")
        self.assertEqual(self.head(), before)

    def test_code_change_is_pulled(self):
        before = self.head()
        self.push("code.py", "two\n")
        self.update()          # перезапуск демона здесь заведомо не удастся
        self.assertNotEqual(self.head(), before)
        self.assertEqual((self.work / "code.py").read_text(encoding="utf-8"), "two\n")

    def test_docs_only_change_skips_restart(self):
        self.push("README.md", "текст\n")
        res = self.update()
        self.assertEqual(res.returncode, 0)
        self.assertIn("только документация", res.stdout)

    def test_local_changes_are_not_touched(self):
        before = self.head()
        (self.work / "code.py").write_text("моя правка\n", encoding="utf-8")
        self.push("code.py", "two\n")
        res = self.update()
        self.assertEqual(res.returncode, 1)
        self.assertIn("незакоммиченные", res.stdout)
        self.assertEqual(self.head(), before)
        self.assertEqual((self.work / "code.py").read_text(encoding="utf-8"),
                         "моя правка\n")

    def test_other_branch_is_not_touched(self):
        before = self.head()
        self.git(self.work, "checkout", "-q", "-b", "hotfix")
        self.push("code.py", "two\n")
        res = self.update()
        self.assertEqual(res.returncode, 1)
        self.assertIn("hotfix", res.stdout)
        self.assertEqual(self.head(), before)

    def test_failed_selftest_rolls_back(self):
        before = self.head()
        self.push("code.py", "two\n")
        env = dict(os.environ, ND_SELFTEST="1", ND_SERVICE="нет-такого-юнита")
        res = subprocess.run(["sh", str(self.work / "deploy" / "autoupdate.sh")],
                             env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)      # тестов в этом репозитории нет
        self.assertIn("откат", res.stdout)
        self.assertEqual(self.head(), before)


if __name__ == "__main__":
    unittest.main()
