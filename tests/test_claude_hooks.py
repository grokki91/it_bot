"""Хуки Claude Code из .claude/hooks: бюджет ходов и финал ответа."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, ".claude", "hooks")


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
        env=dict(os.environ, **(env or {})), check=True).stdout


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


if __name__ == "__main__":
    unittest.main()
