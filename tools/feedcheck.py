# -*- coding: utf-8 -*-
"""Отвечают ли ленты, записанные в репозитории: подборка, кандидаты, переезды.

`digest.py feeds` проверяет то, что сейчас в обходе у сервера, и ходит туда с
адреса сервера. Этот скрипт — про сам список в коде: живы ли адреса, которые в
нём записаны. Он нужен там, где список правят: из песочницы, в которой пишется
код, до большинства сайтов не достучаться, и адреса в `candidates.py` годами
оставались предположениями. Поэтому проверка идёт в CI
(`.github/workflows/feeds.yml`) — оттуда виден открытый интернет.

    python3 tools/feedcheck.py                          # всё подряд
    python3 tools/feedcheck.py --new-since origin/main  # и новые адреса обязаны ответить

На каждый адрес — код ответа, сколько записей в ленте, сколько из них свежих и
дата самой новой. Лента, где самой новой записи полгода, формально жива, а по
сути нет: это видно по дате, а не по коду ответа.

С `--new-since REF` код возврата 1, если адрес, которого в REF не было, ответил
определённо плохо: 404 и 410, домена нет, ответ — не лента или лента пуста.
403 и 429 — защита от роботов: CI и сервер она видит по-разному, поэтому такой
ответ печатается предупреждением и проверку не валит. Так же — таймаут и
двухсотка с пустым телом: это тот же отказ роботу, только вежливый.
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT if (_ROOT / "newsdigest").is_dir() else Path.cwd()))

from newsdigest import candidates                  # noqa: E402
from newsdigest.config import CFG                  # noqa: E402
from newsdigest.feedparse import parse_feed        # noqa: E402
from newsdigest.profiles import BUILTIN            # noqa: E402
from newsdigest.sources import GUARDED, download   # noqa: E402

#: где в репозитории записаны адреса лент — по ним ищется, что появилось нового
LISTS = ("newsdigest/profiles.py", "newsdigest/candidates.py")
URL = re.compile(r"https?://[^\s\"'<>)]+")

#: ответы, после которых адрес мёртв откуда ни смотри
DEAD = (404, 410)
#: сколько дней без единой записи — «лента давно молчит»
STALE_DAYS = 120


def rows() -> list:
    """(что это, раздел, имя, адрес) по всему, что записано в коде."""
    out, topic_of = [], {}
    for topic, body in BUILTIN.items():
        for feed in body["feeds"]:
            out.append(("подборка", topic, feed[0], feed[1]))
            topic_of[feed[0]] = topic
    for topic, source_id, url, _tier, _cat, _why in candidates.all_candidates():
        out.append(("кандидат", topic, source_id, url))
        topic_of.setdefault(source_id, topic)
    for source_id, moves in candidates.REPLACEMENTS.items():
        for url, _why in moves:
            out.append(("переезд", topic_of.get(source_id, ""), source_id, url))
    seen, unique = set(), []
    for row in out:
        if row[3] not in seen:
            seen.add(row[3])
            unique.append(row)
    return unique


def known_at(ref: str) -> set:
    """Адреса, которые были записаны в коде на момент REF."""
    urls = set()
    for path in LISTS:
        try:
            text = subprocess.run(
                ["git", "show", "%s:%s" % (ref, path)], cwd=str(_ROOT),
                capture_output=True, check=True, text=True).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            sys.exit("Не прочитал %s в %s: %s" % (path, ref, exc))
        urls.update(URL.findall(text))
    return urls


def resolves(url: str) -> bool:
    try:
        socket.getaddrinfo(urllib.parse.urlparse(url).hostname or "", 443)
    except (socket.gaierror, UnicodeError):
        return False
    return True


def probe(row) -> dict:
    """Один адрес: тот же путь, что у `sources.fetch_source`, плюс подробности."""
    what, topic, source_id, url = row
    status, raw = download(url)                 # как в сборе: со вторым User-Agent
    out = {"what": what, "topic": topic, "id": source_id, "url": url,
           "status": status, "total": 0, "fresh": 0, "newest": "", "note": ""}
    if status != 200:
        if status == 0:
            out["note"] = ("нет домена" if not resolves(url)
                           else "не достучались: таймаут или обрыв")
        elif status in GUARDED or status == 401:
            out["note"] = "защита от роботов"
        return out
    if not raw.strip():
        # двухсотка без тела — тот же отказ роботу, только вежливый: откуда
        # смотреть, так и ответят. Мёртвой такую ленту назвать нельзя
        out["note"] = "пустой ответ: отказ роботу"
        return out
    try:
        entries = parse_feed(raw)
    except Exception as exc:  # noqa: BLE001 — нам нужен диагноз, а не падение
        # начало ответа отличает страницу сайта («<!DOCTYPE html») от ленты,
        # которую не осилил разборщик: это чинится по-разному
        start = raw.lstrip()[:40].decode("utf-8", "replace")
        out["note"] = "не лента: %s (%s) %r" % (
            type(exc).__name__, str(exc)[:60], re.sub(r"\s+", " ", start))
        return out
    window = datetime.now(timezone.utc) - timedelta(hours=CFG["window_hours"])
    dates = [e["published"] for e in entries if e["published"]]
    out["total"] = len(entries)
    out["fresh"] = sum(1 for e in entries
                       if not e["published"] or e["published"] >= window)
    if dates:
        newest = max(dates)
        out["newest"] = newest.date().isoformat()
        if datetime.now(timezone.utc) - newest > timedelta(days=STALE_DAYS):
            out["note"] = "давно молчит"
    if not entries:
        out["note"] = "лента пуста"
    return out


def verdict(res) -> str:
    """ok · warn · dead. Мёртвое — только то, что мертво откуда ни смотри."""
    if res["status"] == 200 and res["total"]:
        return "warn" if res["note"] else "ok"
    if res["status"] in DEAD or res["note"] in ("нет домена", "лента пуста") \
            or res["note"].startswith("не лента"):
        return "dead"
    return "warn"


def report(results, new) -> list:
    lines = []
    mark = {"ok": " ok ", "warn": "WARN", "dead": "DEAD"}
    for res in results:
        lines.append("[%s] %s%-9s %-10s %-22s %3s %4d %3d %-10s %s%s" % (
            mark[verdict(res)], "+" if res["url"] in new else " ",
            res["what"], res["topic"][:10], res["id"][:22], res["status"],
            res["total"], res["fresh"], res["newest"] or "-", res["url"],
            ("  — " + res["note"]) if res["note"] else ""))
    return lines


def summary_md(results, new) -> str:
    """Таблица для страницы прогона в GitHub Actions."""
    bad = [r for r in results if verdict(r) != "ok"]
    head = ("### Ленты: %d адресов, отвечают %d, под вопросом %d, мертвы %d\n\n"
            % (len(results), sum(verdict(r) == "ok" for r in results),
               sum(verdict(r) == "warn" for r in results),
               sum(verdict(r) == "dead" for r in results)))
    if new:
        head += "Новых адресов в этой правке: %d (помечены ➕).\n\n" % len(new)
    body = ["| | что | раздел | имя | код | записей | свежих | новейшая | заметка |",
            "|---|---|---|---|---|---|---|---|---|"]
    for res in sorted(bad, key=lambda r: (r["url"] not in new, r["what"], r["id"])):
        body.append("| %s%s | %s | %s | [%s](%s) | %s | %d | %d | %s | %s |" % (
            {"warn": "⚠️", "dead": "❌"}[verdict(res)],
            " ➕" if res["url"] in new else "", res["what"], res["topic"],
            res["id"], res["url"], res["status"], res["total"], res["fresh"],
            res["newest"] or "—", res["note"] or "—"))
    return head + ("\n".join(body) if bad else "Все адреса отвечают.") + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--new-since", metavar="REF",
                        help="адреса, которых не было в REF, обязаны ответить")
    parser.add_argument("--only-new", action="store_true",
                        help="с --new-since: проверять только новые адреса")
    args = parser.parse_args(argv)

    todo = rows()
    new = set()
    if args.new_since:
        old = known_at(args.new_since)
        new = {row[3] for row in todo if row[3] not in old}
        if args.only_new:
            todo = [row for row in todo if row[3] in new]
    print("Проверяю %d адресов%s...\n" % (
        len(todo), (", новых: %d" % len(new)) if args.new_since else ""))
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(probe, todo))
    order = {"подборка": 0, "кандидат": 1, "переезд": 2}
    results.sort(key=lambda r: (order[r["what"]], r["topic"], r["id"]))

    print("       что       раздел     имя                   код записей свежих новейшая")
    print("\n".join(report(results, new)))
    counts = {k: sum(verdict(r) == k for r in results) for k in ("ok", "warn", "dead")}
    print("\nОтвечают: %(ok)d, под вопросом: %(warn)d, мертвы: %(dead)d" % counts)

    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write(summary_md(results, new))

    dead_new = [r for r in results if r["url"] in new and verdict(r) == "dead"]
    if dead_new:
        print("\nНовые адреса, которые не отвечают:")
        for res in dead_new:
            print("  %-22s %s — %s" % (res["id"], res["url"],
                                        res["note"] or "HTTP %s" % res["status"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
