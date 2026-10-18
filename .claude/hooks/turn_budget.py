#!/usr/bin/env python3
"""UserPromptSubmit: считает ходы сессии и напоминает о бюджете.

Бюджет из CLAUDE.md — «не больше 5 ответов, цель — 1». Модель свои ходы
не считает, поэтому считает хук: на предпоследнем ходу просит довести
или вынести остаток в журнал, после бюджета — только вынести и закрыть.
"""
import json
import os
import re
import sys
import tempfile

BUDGET = int(os.environ.get("ND_TURN_BUDGET", "5"))


def state_dir():
    return os.environ.get("ND_HOOK_STATE") or os.path.join(
        tempfile.gettempdir(), "claude-turn-budget")


def bump(session_id):
    os.makedirs(state_dir(), exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default")
    path = os.path.join(state_dir(), safe)
    try:
        with open(path) as f:
            n = int(f.read().strip() or 0)
    except (OSError, ValueError):
        n = 0
    n += 1
    with open(path, "w") as f:
        f.write(str(n))
    return n


def message(n, budget=BUDGET):
    if n < budget - 1:
        return ""
    if n < budget:
        return (f"[Бюджет ходов: {n} из {budget}] Предпоследний ход. Доведи "
                "задачу в этом ходе; что не укладывается — вынеси в журнал "
                "(скил /defer, GitHub Issue с меткой deferred) и закрой ход "
                "строкой ИТОГ.")
    return (f"[Бюджет ходов исчерпан: {n} из {budget}] Новых доработок и "
            "проверок не начинай. Ответь на вопрос пользователя, если он "
            "прямой; нерешённое вынеси в журнал через /defer, дай ссылку на "
            "issue и закрой ход строкой ИТОГ.")


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    text = message(bump(data.get("session_id", "")))
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
