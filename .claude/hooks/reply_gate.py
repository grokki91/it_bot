#!/usr/bin/env python3
"""Stop: финал ответа — по CLAUDE.md, и без предложений «ещё доработать».

Финал — строка ИТОГ, а если в дереве есть правка кода — блок готовности к
мержу из `tools/prcheck.py`. Блок проверяется по фактам: merge_ready следует
из полей по тому же правилу, PASS подтверждён прогоном prcheck на этом самом
дереве, YES — проверенное закоммичено и запушено.

Возвращает модель не больше одного раза подряд (stop_hook_active),
чтобы проверка сама не стала бесконечным циклом.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATUS = re.compile(r"ИТОГ:\s*(OK С ОГОВОРКАМИ|OK|НУЖНЫ ДАННЫЕ|ПРОБЛЕМА)")
FIELD = re.compile(r"^[\s`]*(code_pr|checks|integration|merge_ready):\s*([A-Z_]+)",
                   re.M)
UPSELL = re.compile(
    r"(хотите|желаете|могу (?:также|ещё|еще)|стоит (?:также|ещё|еще)|"
    r"можно (?:также|ещё|еще) (?:добавить|улучшить|доработать|проверить))",
    re.IGNORECASE)
RERUN = ("запусти `python3 tools/prcheck.py` и перепиши финал по его выводу "
         "или поставь непроверенному NOT_RUN / UNKNOWN")


def prcheck():
    spec = importlib.util.spec_from_file_location(
        "prcheck", os.path.join(ROOT, "tools", "prcheck.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def last_assistant_text(transcript_path):
    text = ""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") != "assistant":
                    continue
                content = (ev.get("message") or {}).get("content") or []
                parts = [c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text"]
                if parts:
                    text = "\n".join(parts)
    except OSError:
        pass
    return text


def readiness(fields, root=None):
    """'' — блок готовности честный; иначе — что в нём не так."""
    pc = prcheck()
    for key, allowed in pc.VALUES.items():
        if fields.get(key) not in allowed:
            return "в блоке готовности %s: %s, а нужно одно из %s" % (
                key, fields.get(key, "—"), " / ".join(allowed))
    want = pc.verdict(fields["code_pr"], fields["checks"], fields["integration"])
    if fields["merge_ready"] != want:
        return "merge_ready: %s не следует из полей — по правилу %s" % (
            fields["merge_ready"], want)
    if root is None or not (want == "YES" or "PASS" in fields.values()
                            or fields["integration"] == "NOT_REQUIRED"):
        return ""
    try:
        tree = pc.fingerprint(root)
    except (OSError, subprocess.CalledProcessError):
        return ""                               # не git — сверять не с чем
    stamp = pc.load_stamp(root)
    if stamp.get("tree") != tree:
        return "PASS без прогона prcheck на текущем дереве: " + RERUN
    for key in ("code_pr", "checks"):
        if fields[key] == "PASS" and stamp.get(key) != "PASS":
            return "%s: PASS, а prcheck дал %s: %s" % (key, stamp.get(key), RERUN)
    if (stamp.get("integration") == "FAIL" and fields["integration"] != "FAIL") \
            or (stamp.get("integration") != "NOT_REQUIRED"
                and fields["integration"] == "NOT_REQUIRED"):
        return "integration: %s, а prcheck дал %s" % (
            fields["integration"], stamp.get("integration"))
    if want == "YES" and not pc.pushed(root):
        return ("merge_ready: YES, а проверенное не закоммичено или не "
                "запушено: запушь или поставь UNKNOWN")
    return ""


def code_changed(root):
    pc = prcheck()
    try:
        return pc.own_changes(pc.fingerprint(root), root=root)
    except (OSError, subprocess.CalledProcessError):
        return False


def problem(text, root=None):
    if not text.strip():
        return ""
    tail = text.strip()[-1500:]
    fields = dict(FIELD.findall(tail))
    if fields:
        why = readiness(fields, root)
        if why:
            return why
    elif root and code_changed(root):
        return ("в дереве правка кода, а в финале нет блока готовности "
                "(code_pr / checks / integration / merge_ready): " + RERUN)
    elif not STATUS.search(tail[-600:]):
        return "в конце ответа нет строки «ИТОГ: <статус>» из CLAUDE.md"
    if UPSELL.search(tail[-600:]):
        return ("в конце ответа предложение доработать — по CLAUDE.md "
                "такие пункты выносятся в журнал через /defer, а не в чат")
    return ""


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if data.get("stop_hook_active"):
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or ROOT
    why = problem(last_assistant_text(data.get("transcript_path", "")), root)
    if why:
        print(json.dumps({"decision": "block",
                          "reason": f"Финал ответа не принят: {why}. "
                                    "Другой работы не начинай."},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
