# -*- coding: utf-8 -*-
"""tools/webpass.py: пароль страницы читается из env-файла так же, как в конфиге."""
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("nd_webpass", ROOT / "tools" / "webpass.py")
webpass = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(webpass)

TOY = "пароль-игрушечный-1"  # nd-redact: allow


class ReadCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ndtest-"))
        self.path = self.dir / "env"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_reads_the_token_among_other_keys(self):
        self.write("# комментарий\nDEEPSEEK_API_KEY=x\nND_WEB_TOKEN=%s\n" % TOY)  # nd-redact: allow
        self.assertEqual(webpass.read_token(self.path), TOY)

    def test_first_entry_wins_like_load_env(self):
        self.write("ND_WEB_TOKEN=first\nND_WEB_TOKEN=second\n")
        self.assertEqual(webpass.read_token(self.path), "first")

    def test_quotes_are_stripped(self):
        self.write('ND_WEB_TOKEN="abc-def"\n')
        self.assertEqual(webpass.read_token(self.path), "abc-def")

    def test_missing_or_empty_is_none(self):
        self.write("ND_WEB_PORT=8080\n")
        self.assertIsNone(webpass.read_token(self.path))
        self.write("ND_WEB_TOKEN=\n")
        self.assertIsNone(webpass.read_token(self.path))


class MainCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ndtest-"))

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = webpass.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_prints_the_password_from_home(self):
        (self.dir / "env").write_text("ND_WEB_TOKEN=%s\n" % TOY, encoding="utf-8")  # nd-redact: allow
        os.chmod(self.dir / "env", 0o600)
        code, out, err = self.run_main("--home", str(self.dir))
        self.assertEqual(code, 0)
        self.assertIn(TOY, out)  # nd-redact: allow
        self.assertNotIn("chmod", err)

    def test_warns_when_file_is_readable_by_others(self):
        (self.dir / "env").write_text("ND_WEB_TOKEN=%s\n" % TOY, encoding="utf-8")  # nd-redact: allow
        os.chmod(self.dir / "env", 0o644)
        code, out, err = self.run_main("--home", str(self.dir))
        self.assertEqual(code, 0)
        self.assertIn("chmod 600", err)

    def test_no_file_is_an_error_without_stdout(self):
        code, out, err = self.run_main("--home", str(self.dir / "нет"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("Файла нет", err)

    def test_file_without_token_is_an_error(self):
        (self.dir / "env").write_text("ND_WEB_PORT=8080\n", encoding="utf-8")
        code, out, err = self.run_main("--home", str(self.dir))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("ND_WEB_TOKEN", err)


if __name__ == "__main__":
    unittest.main()
