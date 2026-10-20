# -*- coding: utf-8 -*-
"""Можно ли мержить: короткий отчёт о рабочем дереве против main.

    python3 tools/prcheck.py              # в конце задачи, ~2 минуты
    python3 tools/prcheck.py --base REF   # с чем сравнивать, по умолчанию origin/main

    code_pr: PASS
    checks: PASS
    integration: NOT_REQUIRED
    merge_ready: YES

code_pr      изменённые .py собираются, маркеров конфликта нет, тесты зелёные;
checks       обязательное из CI (`checks.yml`): все тесты и заслон для секретов —
             по всем файлам, которые уйдут в коммит, включая ещё не добавленные;
integration  NOT_REQUIRED — в правке нет того, чего тесты не видят;
             `tools/itest_*.py` в правке — запускается здесь же: без живых
             данных он BLOCKED, а не PASS (см. `tools/itest.py`);
             списки лент — их проверяет CI `feeds`: из песочницы сайтов не
             видно, поэтому отсюда NOT_RUN;
merge_ready  YES — всё PASS, интеграция PASS или не нужна; NO — что-то FAIL;
             UNKNOWN — чего-то не проверить. Код возврата 0 / 1 / 2.

Итог пишется в `.git/nd-prcheck.json` вместе с хешем проверенного дерева: по
нему хук `.claude/hooks/reply_gate.py` не даёт объявить PASS дереву, которое
не проверялось.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "origin/main"
STAMP = "nd-prcheck.json"
#: правка этих файлов проверяется на живых лентах — это делает CI `feeds`
FEEDS = ("newsdigest/profiles.py", "newsdigest/candidates.py",
         "tools/feedcheck.py", ".github/workflows/feeds.yml")
ITEST = re.compile(r"^tools/itest_\w+\.py$")
#: код возврата скрипта интеграции «нечем проверить» (`tools/itest.py`)
BLOCKED_CODE = 3
TESTS_TIMEOUT = 900
ITEST_TIMEOUT = 1800
#: допустимые значения полей отчёта
VALUES = {"code_pr": ("PASS", "FAIL", "NOT_RUN"),
          "checks": ("PASS", "FAIL", "NOT_RUN"),
          "integration": ("PASS", "FAIL", "NOT_REQUIRED", "NOT_RUN", "BLOCKED"),
          "merge_ready": ("YES", "NO", "UNKNOWN")}
EXIT = {"YES": 0, "NO": 1, "UNKNOWN": 2}


def git(*args, root=ROOT, env=None) -> str:
    return subprocess.run(["git"] + list(args), cwd=root, env=env, check=True,
                          capture_output=True, text=True).stdout


def verdict(code_pr, checks, integration) -> str:
    """Решение о мерже — только из фактов: FAIL → NO, всё подтверждено → YES."""
    if "FAIL" in (code_pr, checks, integration):
        return "NO"
    if code_pr == checks == "PASS" and integration in ("PASS", "NOT_REQUIRED"):
        return "YES"
    return "UNKNOWN"


def fingerprint(root=ROOT) -> str:
    """Хеш дерева, каким его закоммитит `git add -A`; настоящий индекс не трогаем."""
    index = os.path.join(root, git("rev-parse", "--git-path", "index",
                                   root=root).strip())
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        if os.path.exists(index):
            shutil.copyfile(index, env["GIT_INDEX_FILE"])
        git("add", "-A", root=root, env=env)
        return git("write-tree", root=root, env=env).strip()


def own_changes(tree, base=BASE, root=ROOT) -> bool:
    """Есть ли в дереве что-то сверх общей с base истории."""
    try:
        start = git("merge-base", base, "HEAD", root=root).strip()
        return tree != git("rev-parse", start + "^{tree}", root=root).strip()
    except subprocess.CalledProcessError:
        return False


def pushed(root=ROOT) -> bool:
    """Дерево чистое и совпадает с upstream — проверенное лежит в PR."""
    head, *rest = git("status", "--porcelain", "--branch", root=root).splitlines()
    return not rest and "..." in head and "[" not in head


def stamp_path(root=ROOT) -> str:
    return os.path.join(root, git("rev-parse", "--git-path", STAMP,
                                  root=root).strip())


def load_stamp(root=ROOT) -> dict:
    try:
        with open(stamp_path(root), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return {}


def changed(base=BASE, root=ROOT):
    """Что правка меняет против base: коммиты ветки, дерево, новые файлы."""
    try:
        start = git("merge-base", base, "HEAD", root=root).strip()
    except subprocess.CalledProcessError:
        return None
    names = git("diff", "--name-only", start, root=root).splitlines()
    names += git("ls-files", "--others", "--exclude-standard", root=root).splitlines()
    return sorted(set(n for n in names if n))


def static(files, root=ROOT) -> list:
    """Изменённые файлы: .py собирается, маркеров конфликта не осталось."""
    bad = []
    for name in files:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue                            # удалён правкой
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            continue                            # не текст
        if name.endswith(".py"):
            try:
                compile(text, name, "exec")
            except SyntaxError as exc:
                bad.append("%s:%s: %s" % (name, exc.lineno, exc.msg))
        for number, line in enumerate(text.splitlines(), 1):
            if line.startswith(("<" * 7 + " ", ">" * 7 + " ")):
                bad.append("%s:%d: маркер конфликта" % (name, number))
    return bad


def tests(root=ROOT, timeout=TESTS_TIMEOUT):
    """Все тесты, как в CI. PASS — только если они правда шли и не упали."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "FAIL", "тесты: таймаут %d с" % timeout
    ran = re.search(r"^Ran (\d+) tests? ", proc.stderr, re.M)
    if proc.returncode == 0 and ran and int(ran.group(1)):
        return "PASS", ""
    failed = re.findall(r"^(?:FAIL|ERROR): (.+)$", proc.stderr, re.M)
    if failed:
        return "FAIL", "тесты: %d упали: %s" % (len(failed), "; ".join(failed[:3]))
    tail = (proc.stderr.strip().splitlines() or ["тестов не нашлось"])[-1]
    return "FAIL", "тесты: " + tail[:160]


def secrets(root=ROOT):
    """Заслон из CI по всему, что уйдёт в коммит, включая ещё не добавленное."""
    files = [os.path.join(root, f) for f in git(
        "ls-files", "-z", "--cached", "--others", "--exclude-standard",
        root=root).split("\0") if f]
    files = [f for f in files if os.path.isfile(f)]
    proc = subprocess.run([sys.executable, "-m", "newsdigest.redact", "--check"]
                          + files, cwd=ROOT, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    if proc.returncode == 0:
        return "PASS", ""
    hits = [os.path.relpath(line.split(": ", 1)[0], root)
            for line in proc.stdout.splitlines() if "похоже на секрет" in line]
    if not hits:
        return "FAIL", "секреты: проверка упала"
    return "FAIL", "секреты: %d строк: %s" % (len(hits), ", ".join(hits[:3]))


def run_itest(name, root=ROOT, timeout=ITEST_TIMEOUT):
    """Скрипт интеграции → (статус, строки показателей, причина)."""
    try:
        proc = subprocess.run([sys.executable, name], cwd=root, timeout=timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return "FAIL", [], "%s: таймаут %d с" % (name, timeout)
    lines = proc.stdout.splitlines()
    said = [ln.split(":", 1)[1].strip() for ln in lines
            if ln.startswith("integration:")]
    rest = [ln for ln in lines if ln.strip() and not ln.startswith("integration:")]
    status = said[-1] if said else ""
    # статус засчитывается, только если код возврата с ним согласен
    if (status, proc.returncode) in (("PASS", 0), ("BLOCKED", BLOCKED_CODE)):
        return status, rest, ""
    if status == "FAIL":
        return "FAIL", rest, ""
    tail = (proc.stderr.strip().splitlines() or [""])[-1]
    return "FAIL", rest, "%s: код %d %s" % (name, proc.returncode, tail[:160])


def integration(files, root=ROOT, timeout=ITEST_TIMEOUT):
    """Нужна ли реальная интеграция и чем кончилась → (статус, строки, причины)."""
    if files is None:
        return "NOT_RUN", [], ["нет базы сравнения — git fetch origin main"]
    itests = [f for f in files if ITEST.match(f)
              and os.path.isfile(os.path.join(root, f))]
    statuses, lines, reasons = [], [], []
    for name in itests:
        status, rest, why = run_itest(name, root, timeout)
        statuses.append(status)
        lines += [("%s: %s" % (os.path.basename(name), ln)) if len(itests) > 1
                  else ln for ln in rest]
        reasons += [why] if why else []
    if any(f in FEEDS for f in files):
        statuses.append("NOT_RUN")
        reasons.append("ленты: проверит CI feeds после пуша")
    if not statuses:
        return "NOT_REQUIRED", [], []
    for worst in ("FAIL", "BLOCKED", "NOT_RUN", "PASS"):
        if worst in statuses:
            return worst, lines, reasons


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default=BASE, help="с чем сравнивать правку")
    args = parser.parse_args(argv)

    tree = fingerprint()
    files = changed(args.base)
    reasons = static(files or [])
    tests_status, why = tests()
    reasons += [why] if why else []
    code_pr = "FAIL" if reasons else "PASS"
    secrets_status, why = secrets()
    reasons += [why] if why else []
    checks = "PASS" if tests_status == secrets_status == "PASS" else "FAIL"
    integ, extra, why = integration(files)
    reasons += why
    if fingerprint() != tree:
        # проверялось не одно дерево, а смесь — такому PASS верить нельзя
        code_pr = checks = "NOT_RUN"
        tree = None
        reasons.append("дерево менялось во время проверки — перезапустить")
    ready = verdict(code_pr, checks, integ)

    report = {"code_pr": code_pr, "checks": checks, "integration": integ,
              "merge_ready": ready}
    sys.path.insert(0, ROOT)
    from newsdigest.redact import scrub         # вывод скрипта — тоже наружу
    print("code_pr: %s\nchecks: %s\nintegration: %s" % (code_pr, checks, integ))
    for line in extra + ["reason: " + r for r in reasons]:
        print(scrub(line))
    print("merge_ready: " + ready)
    with open(stamp_path(), "w", encoding="utf-8") as fh:
        json.dump(dict(report, tree=tree), fh)
    return EXIT[ready]


if __name__ == "__main__":
    sys.exit(main())
