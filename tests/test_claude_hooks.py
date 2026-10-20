"""Хуки Claude Code из .claude/hooks: бюджет ходов и финал ответа."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

from test_prcheck import make_repo, prcheck, sh, write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, ".claude", "hooks")
#: каталог проекта для хука вне git — чтобы прогон внутри сессии Claude Code
#: не сверял финалы тестов с настоящим деревом
NOWHERE = tempfile.mkdtemp(prefix="ndhook-")
BLOCK = "code_pr: %s\nchecks: %s\nintegration: %s\nmerge_ready: %s"


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HOOKS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(name, payload, env=None):
    return subprocess.run(
        [sys.executable, os.path.join(HOOKS, name + ".py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env=dict(os.environ, CLAUDE_PROJECT_DIR=NOWHERE, **(env or {})),
        check=True).stdout


class TurnBudgetTest(unittest.TestCase):
    def test_counts_per_session_and_warns_near_budget(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"ND_HOOK_STATE": d, "ND_TURN_BUDGET": "5"}
            outs = [run("turn_budget", {"session_id": "s/1"}, env)
                    for _ in range(6)]
            self.assertEqual(outs[:3], ["", "", ""])
            self.assertIn("4 из 5", outs[3])
            self.assertIn("исчерпан: 5 из 5", outs[4])
            self.assertIn("6 из 5", outs[5])
            self.assertEqual(run("turn_budget", {"session_id": "s2"}, env), "")


class ReplyGateTest(unittest.TestCase):
    def setUp(self):
        self.gate = load("reply_gate")

    def transcript(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                        encoding="utf-8")
        self.addCleanup(os.unlink, f.name)
        for t in ("старый ответ", text):
            f.write(json.dumps({"type": "assistant", "message": {
                "content": [{"type": "text", "text": t}]}},
                ensure_ascii=False) + "\n")
        f.close()
        return f.name

    def test_problem(self):
        self.assertEqual(self.gate.problem("Готово.\nИТОГ: OK — тесты"), "")
        self.assertIn("ИТОГ", self.gate.problem("Готово."))
        self.assertIn("журнал", self.gate.problem(
            "Хотите, добавлю кэш?\nИТОГ: OK С ОГОВОРКАМИ"))

    def test_blocks_once(self):
        path = self.transcript("Сделал.")
        out = json.loads(run("reply_gate", {"transcript_path": path}))
        self.assertEqual(out["decision"], "block")
        self.assertEqual(run("reply_gate", {"transcript_path": path,
                                            "stop_hook_active": True}), "")
        ok = self.transcript("Сделал.\nИТОГ: OK")
        self.assertEqual(run("reply_gate", {"transcript_path": ok}), "")

    def test_block_must_follow_rule(self):
        p = self.gate.problem
        self.assertEqual(p(BLOCK % ("PASS", "PASS", "NOT_REQUIRED", "YES")), "")
        self.assertEqual(p("```\n%s\n```" % (
            BLOCK % ("PASS", "PASS", "BLOCKED", "UNKNOWN"))), "")
        self.assertIn("по правилу UNKNOWN",
                      p(BLOCK % ("PASS", "PASS", "NOT_RUN", "YES")))
        self.assertIn("по правилу NO",
                      p(BLOCK % ("PASS", "FAIL", "PASS", "UNKNOWN")))
        self.assertIn("integration: —", p("code_pr: PASS\nchecks: PASS"))
        self.assertIn("integration: SKIPPED",
                      p(BLOCK % ("PASS", "PASS", "SKIPPED", "YES")))


class ReplyGateEvidenceTest(unittest.TestCase):
    """PASS и YES в финале — только по прогону prcheck на этом же дереве."""

    def setUp(self):
        self.gate = load("reply_gate")
        self.work = make_repo(self)

    def stamp(self, **fields):
        report = dict({"code_pr": "PASS", "checks": "PASS",
                       "integration": "NOT_REQUIRED"}, **fields)
        report["tree"] = prcheck.fingerprint(self.work)
        with open(prcheck.stamp_path(self.work), "w") as fh:
            json.dump(report, fh)

    def test_code_change_needs_block(self):
        self.assertEqual(self.gate.problem("Ответ.\nИТОГ: OK", self.work), "")
        write(self.work, "b.py", "y = 2\n")
        self.assertIn("нет блока готовности",
                      self.gate.problem("Сделал.\nИТОГ: OK", self.work))

    def test_pass_needs_run_on_this_tree(self):
        write(self.work, "b.py", "y = 2\n")
        ready = BLOCK % ("PASS", "PASS", "NOT_REQUIRED", "YES")
        self.assertIn("без прогона prcheck", self.gate.problem(ready, self.work))
        unknown = BLOCK % ("NOT_RUN", "NOT_RUN", "NOT_RUN", "UNKNOWN")
        self.assertEqual(self.gate.problem(unknown, self.work), "")

        self.stamp()
        self.assertIn("не запушено", self.gate.problem(ready, self.work))
        sh(self.work, "add", "-A")
        sh(self.work, "commit", "-qm", "b")
        sh(self.work, "push", "-q", "-u", "origin", "feature")
        self.assertEqual(self.gate.problem(ready, self.work), "")
        write(self.work, "b.py", "y = 3\n")              # правка после прогона
        self.assertIn("без прогона prcheck", self.gate.problem(ready, self.work))

    def test_cannot_upgrade_prcheck(self):
        write(self.work, "b.py", "y = 2\n")
        self.stamp(checks="FAIL", integration="NOT_RUN")
        self.assertIn("checks: PASS, а prcheck дал FAIL", self.gate.problem(
            BLOCK % ("PASS", "PASS", "NOT_RUN", "UNKNOWN"), self.work))
        self.assertIn("integration: NOT_REQUIRED", self.gate.problem(
            BLOCK % ("PASS", "FAIL", "NOT_REQUIRED", "NO"), self.work))
        # интеграцию после CI feeds или прогона на сервере поднять можно
        self.assertEqual(self.gate.problem(
            BLOCK % ("PASS", "FAIL", "PASS", "NO"), self.work), "")


if __name__ == "__main__":
    unittest.main()
