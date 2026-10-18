#!/usr/bin/env python3
"""Stop: ход закрывается строкой ИТОГ и без предложений «ещё доработать».

Возвращает модель не больше одного раза подряд (stop_hook_active),
чтобы проверка сама не стала бесконечным циклом.
"""
import json
import re
import sys

STATUS = re.compile(r"ИТОГ:\s*(OK С ОГОВОРКАМИ|OK|НУЖНЫ ДАННЫЕ|ПРОБЛЕМА)")
UPSELL = re.compile(
    r"(хотите|желаете|могу (?:также|ещё|еще)|стоит (?:также|ещё|еще)|"
    r"можно (?:также|ещё|еще) (?:добавить|улучшить|доработать|проверить))",
    re.IGNORECASE)


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


def problem(text):
    if not text.strip():
        return ""
    tail = text.strip()[-600:]
    if not STATUS.search(tail):
        return "в конце ответа нет строки «ИТОГ: <статус>» из CLAUDE.md"
    if UPSELL.search(tail):
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
    why = problem(last_assistant_text(data.get("transcript_path", "")))
    if why:
        print(json.dumps({"decision": "block",
                          "reason": f"Перепиши только финал ответа: {why}. "
                                    "Новой работы не начинай."},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
