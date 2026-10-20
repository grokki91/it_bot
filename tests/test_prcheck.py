# -*- coding: utf-8 -*-
"""Готовность к мержу (tools/prcheck.py) и основа интеграции (tools/itest.py).

Полный прогон prcheck здесь не запускаем — он сам гоняет эти тесты. Смысл —
в правилах: из чего следует merge_ready, что засчитывается за PASS и что
скрипт интеграции не может отчитаться успехом, ничего не сверив.
"""
import hashlib
import importlib.util
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))


def load(name):
    spec = importlib.util.spec_from_file_location(
        "nd_" + name, os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


prcheck = load("prcheck")
itest = load("itest")


def sh(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e"]
                          + list(args), cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def make_repo(test):
    """Клон с origin/main и веткой feature — как в сессии над PR."""
    tmp = tempfile.mkdtemp(prefix="ndgit-")
    test.addCleanup(shutil.rmtree, tmp, True)
    sh(tmp, "init", "-q", "--bare", "remote.git")
    sh(tmp, "clone", "-q", "remote.git", "work")
    work = os.path.join(tmp, "work")
    sh(work, "checkout", "-q", "-b", "main")
    write(work, "a.py", "x = 1\n")
    sh(work, "add", "-A")
    sh(work, "commit", "-qm", "init")
    sh(work, "push", "-q", "-u", "origin", "main")
    sh(work, "checkout", "-q", "-b", "feature")
    return work


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def write(root, name, text):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class VerdictTest(unittest.TestCase):
    def test_rule(self):
        v = prcheck.verdict
        self.assertEqual(v("PASS", "PASS", "NOT_REQUIRED"), "YES")
        self.assertEqual(v("PASS", "PASS", "PASS"), "YES")
        self.assertEqual(v("PASS", "FAIL", "PASS"), "NO")
        self.assertEqual(v("PASS", "PASS", "FAIL"), "NO")
        # не проверено — не значит провалено, но и не YES
        self.assertEqual(v("PASS", "PASS", "BLOCKED"), "UNKNOWN")
        self.assertEqual(v("PASS", "PASS", "NOT_RUN"), "UNKNOWN")
        self.assertEqual(v("NOT_RUN", "PASS", "NOT_REQUIRED"), "UNKNOWN")
        self.assertEqual(v("NOT_RUN", "FAIL", "BLOCKED"), "NO")


class GitStateTest(unittest.TestCase):
    def test_fingerprint_follows_tree_not_index(self):
        work = make_repo(self)
        clean = prcheck.fingerprint(work)
        self.assertFalse(prcheck.own_changes(clean, root=work))
        write(work, "b.py", "y = 2\n")
        dirty = prcheck.fingerprint(work)
        self.assertNotEqual(dirty, clean)
        self.assertTrue(prcheck.own_changes(dirty, root=work))
        # настоящий индекс не тронут: новый файл всё ещё не добавлен
        self.assertIn("?? b.py", sh(work, "status", "--porcelain"))
        sh(work, "add", "-A")
        sh(work, "commit", "-qm", "b")
        self.assertEqual(prcheck.fingerprint(work),
                         sh(work, "rev-parse", "HEAD^{tree}").strip())
        self.assertEqual(prcheck.fingerprint(work), dirty)

    def test_pushed(self):
        work = make_repo(self)
        write(work, "b.py", "y = 2\n")
        self.assertFalse(prcheck.pushed(work))           # не закоммичено
        sh(work, "add", "-A")
        sh(work, "commit", "-qm", "b")
        self.assertFalse(prcheck.pushed(work))           # нет upstream
        sh(work, "push", "-q", "-u", "origin", "feature")
        self.assertTrue(prcheck.pushed(work))
        write(work, "a.py", "x = 3\n")
        self.assertFalse(prcheck.pushed(work))

    def test_changed_and_static(self):
        work = make_repo(self)
        write(work, "a.py", "def f(:\n")
        write(work, "notes.md", "<" * 7 + " HEAD\nмоё\n")
        self.assertEqual(prcheck.changed(root=work), ["a.py", "notes.md"])
        bad = prcheck.static(["a.py", "notes.md", "gone.py"], work)
        self.assertEqual(len(bad), 2)
        self.assertTrue(bad[0].startswith("a.py:1:"))
        self.assertIn("notes.md:1: маркер конфликта", bad)
        self.assertIsNone(prcheck.changed("origin/nope", work))

    def test_secrets_cover_files_not_yet_added(self):
        work = make_repo(self)
        self.assertEqual(prcheck.secrets(work), ("PASS", ""))
        write(work, "new.txt", "token " + "123456789" + ":AA" + "x" * 33 + "\n")
        status, why = prcheck.secrets(work)
        self.assertEqual(status, "FAIL")
        self.assertIn("new.txt:1", why)


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ndint-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "tools"))
        shutil.copy(os.path.join(ROOT, "tools", "itest.py"),
                    os.path.join(self.root, "tools"))

    def script(self, name, body):
        write(self.root, "tools/%s.py" % name, textwrap.dedent(body))
        return "tools/%s.py" % name

    def test_statuses(self):
        ok = self.script("itest_ok", """
            import itest
            itest.run(lambda rep: rep.metric("sent", 9849, 9849))
            """)
        status, lines, why = prcheck.integration([ok], self.root)
        self.assertEqual((status, why), ("PASS", []))
        self.assertEqual(lines, ["sent: expected=9849, actual=9849",
                                 "differences: 0"])

        bad = self.script("itest_bad", """
            import itest
            itest.run(lambda rep: rep.metric("sent", 9849, 9848))
            """)
        status, lines, _ = prcheck.integration([bad], self.root)
        self.assertEqual(status, "FAIL")
        self.assertIn("differences: 1", lines)

        blocked = self.script("itest_blocked", """
            import itest, tempfile
            itest.run(lambda rep: itest.prod_copy(tempfile.mkdtemp()))
            """)
        status, lines, _ = prcheck.integration([blocked], self.root)
        self.assertEqual(status, "BLOCKED")
        self.assertTrue(lines[-1].startswith("reason: нет живой базы"))
        self.assertEqual(prcheck.integration([ok, blocked], self.root)[0],
                         "BLOCKED")

    def test_pass_needs_both_line_and_exit_code(self):
        liar = self.script("itest_liar", """
            print("integration: PASS")
            raise SystemExit(1)
            """)
        silent = self.script("itest_silent", "pass\n")
        for name in (liar, silent):
            status, _, why = prcheck.integration([name], self.root)
            self.assertEqual(status, "FAIL", name)
            self.assertTrue(why and why[0].startswith(name), why)

    def test_requirement_by_paths(self):
        self.assertEqual(prcheck.integration(["a.py"], self.root)[0],
                         "NOT_REQUIRED")
        status, _, why = prcheck.integration(["newsdigest/profiles.py"],
                                             self.root)
        self.assertEqual(status, "NOT_RUN")
        self.assertIn("CI feeds", why[0])
        self.assertEqual(prcheck.integration(None, self.root)[0], "NOT_RUN")


class ItestTest(unittest.TestCase):
    def run_main(self, main):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            itest.run(main)
        return exit_.exception.code, out.getvalue()

    def test_nothing_checked_is_fail(self):
        code, out = self.run_main(lambda rep: None)
        self.assertEqual(code, 1)
        self.assertIn("reason: ничего не сверено", out)

    def test_exception_is_fail(self):
        def boom(rep):
            rep.metric("sent", 1, 1)
            raise RuntimeError("Telegram недоступен")
        code, out = self.run_main(boom)
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("integration: FAIL"))
        self.assertIn("reason: RuntimeError: Telegram недоступен", out)

    def test_compare_rows_not_counts(self):
        def main(rep):
            rep.compare("sent", {1: ("a",), 2: ("b",)}, {1: ("a",), 2: ("c",)})
            rep.check(False, "побочный эффект: письмо ушло")
        code, out = self.run_main(main)
        self.assertEqual(code, 1)
        self.assertIn("sent: expected=2, actual=2", out)
        self.assertIn("differences: 2", out)
        self.assertIn("sent: иначе 2", out)
        self.assertIn("нарушено: побочный эффект", out)

    def test_rows_and_integrity(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t(k, v)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, "a"), (1, "b")])
        with self.assertRaises(ValueError):
            itest.rows(conn, "SELECT k, v FROM t")
        self.assertEqual(itest.rows(conn, "SELECT rowid, v FROM t"),
                         {1: ("a",), 2: ("b",)})
        self.assertEqual(itest.integrity(conn), [])

    def test_prod_copy_leaves_live_base_alone(self):
        live = tempfile.mkdtemp(prefix="ndlive-")
        self.addCleanup(shutil.rmtree, live, True)
        env = dict(os.environ, ND_HOME=live)
        subprocess.run([sys.executable, "-c",
                        "from newsdigest import storage; storage.db().close()"],
                       cwd=ROOT, env=env, check=True)
        db = os.path.join(live, "digest.db")
        before = sha(db)
        script = os.path.join(live, "itest_send.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent("""
                import os, sys
                sys.path.insert(0, %r)
                import itest

                def main(rep):
                    home = itest.prod_copy()
                    from newsdigest import storage, telegram
                    sent = itest.capture_telegram()
                    conn = storage.db()
                    telegram.tg_send(42, "выпуск")
                    rep.metric("messages", 1, len(sent))
                    rep.metric("outbox", 1, conn.execute(
                        "SELECT COUNT(*) FROM outbox").fetchone()[0])
                    rep.check(str(home) != %r, "работа на копии")
                    rep.check(not itest.integrity(conn), "целостность базы")

                itest.run(main)
                """ % (os.path.join(ROOT, "tools"), live)))
        proc = subprocess.run([sys.executable, script], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("integration: PASS"))
        self.assertIn("differences: 0", proc.stdout)
        self.assertEqual(sha(db), before)


if __name__ == "__main__":
    unittest.main()
