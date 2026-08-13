# -*- coding: utf-8 -*-
"""Команды терминала: setup, doctor, feeds, collect, run, daemon, status, service."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, userprofiles
from .config import (CFG, DB_FILE, ENV_FILE, HOME, LAUNCHER, PROFILES_FILE, PROG,
                     local_now, load_env, setup_logging, tz_label, write_env)
from .daemon import daemon
from .llm import llm_cost, llm_json
from .net import post_json
from .pipeline import build_and_send
from .profiles import PROFILES, profile
from .sources import all_feeds, collect, fetch_source
from .storage import db
from .telegram import tg_call, tg_detect_chat


def require_secrets():
    missing = [name for name, val in (("TELEGRAM_BOT_TOKEN", config.TG_TOKEN),
                                      ("TELEGRAM_CHAT_ID", config.TG_CHAT),
                                      ("DEEPSEEK_API_KEY", config.DS_KEY)) if not val]
    if missing:
        sys.exit("Не заданы: %s\nЗапустите: python3 %s setup"
                 % (", ".join(missing), PROG))


def ask(prompt, default="", secret=False):
    suffix = " [%s]" % default if default else ""
    while True:
        if secret:
            value = getpass.getpass("%s%s: " % (prompt, suffix)).strip()
        else:
            value = input("%s%s: " % (prompt, suffix)).strip()
        if value:
            return value
        if default != "":
            return default
        print("  нужно значение")


def cmd_setup(_args):
    print("\n=== Настройка дайджеста ===")
    print("Каталог данных: %s\n" % HOME)

    have = "уже задан, Enter — оставить" if config.TG_TOKEN else ""
    token = ask("1/6 Токен Telegram-бота (от @BotFather)", have, secret=True)
    if token != have:
        config.TG_TOKEN = token
    try:
        bot = tg_call("getMe", {})
        print("      бот: @%s\n" % bot.get("username"))
    except Exception as exc:  # noqa: BLE001
        print("      не смог проверить токен: %s\n" % exc)

    print("2/6 chat_id. СНАЧАЛА напишите боту любое сообщение в Telegram")
    print("    (для канала: добавьте бота админом и опубликуйте пост).")
    typed = input("    Enter — определить автоматически, либо введите chat_id: ").strip()
    chat = config.TG_CHAT
    if typed:
        chat = typed
        print("      принято: %s\n" % chat)
    else:
        try:
            found, title, kind = tg_detect_chat()
            if found:
                chat = found
                print("      найден: %s (%s, %s)\n" % (found, title, kind))
            else:
                print("      апдейтов нет — вы ещё не писали боту.")
                print("      Напишите ему и выполните: python3 %s chatid\n" % PROG)
        except Exception as exc:  # noqa: BLE001
            print("      ошибка: %s\n" % exc)
    config.TG_CHAT = chat

    have = "уже задан, Enter — оставить" if config.DS_KEY else ""
    key = ask("3/6 Ключ DeepSeek (platform.deepseek.com)", have, secret=True)
    if key != have:
        config.DS_KEY = key

    topics = ", ".join(PROFILES)
    topic = ask("4/6 Тема (%s)" % topics, CFG["topic"])
    if topic not in PROFILES:
        print("      неизвестная тема, оставляю %s" % CFG["topic"])
        topic = CFG["topic"]

    send_at = ask("5/6 Время отправки ЧЧ:ММ (по вашему времени)", CFG["send_at"])
    tz = ask("    Часовой пояс (например Europe/Riga, Europe/Warsaw)", CFG["tz"])
    count = ask("6/6 Сколько новостей в выпуске (5-10)", str(CFG["max_items"]))

    write_env({
        "TELEGRAM_BOT_TOKEN": config.TG_TOKEN,
        "TELEGRAM_CHAT_ID": config.TG_CHAT,
        "DEEPSEEK_API_KEY": config.DS_KEY,
        "ND_TOPIC": topic,
        "ND_SEND_AT": send_at,
        "ND_TZ": tz,
        "ND_MAX_ITEMS": count,
    })
    print("\nСохранено в %s (права 600).\n" % ENV_FILE)
    print("Дальше:")
    print("  python3 %s doctor            # проверить связь" % PROG)
    print("  python3 %s run --dry-run     # посмотреть выпуск в терминале" % PROG)
    print("  python3 %s daemon            # запустить фоном" % PROG)
    return 0


def cmd_chatid(_args):
    """Определить chat_id и дописать его в env, не трогая остальные настройки."""
    if not config.TG_TOKEN:
        sys.exit("Сначала задайте токен: python3 %s setup" % PROG)
    bot = tg_call("getMe", {})
    print("Бот: @%s" % bot.get("username"))
    found, title, kind = tg_detect_chat()
    if not found:
        print("\nchat_id не найден. Что сделать:")
        print("  1. Откройте Telegram и напишите боту @%s любое сообщение"
              % bot.get("username"))
        print("     (для канала: добавьте бота администратором и опубликуйте пост)")
        print("  2. Запустите эту команду снова: python3 %s chatid" % PROG)
        return 1
    write_env({"TELEGRAM_CHAT_ID": found})
    print("Найден и сохранён: %s  (%s, %s)" % (found, title or "без названия", kind))
    print("\nПроверьте: python3 %s doctor" % PROG)
    return 0


def cmd_doctor(_args):
    print("Каталог      :", HOME)
    print("Тема         :", CFG["topic"], "| источников:", len(profile()["feeds"]))
    print("Часовой пояс :", tz_label(), "| сейчас у вас", local_now().strftime("%H:%M"),
          "| на сервере", datetime.now(timezone.utc).strftime("%H:%M UTC"))
    print("Расписание   : отправка в %s, сбор раз в %d ч"
          % (CFG["send_at"], CFG["collect_every_h"]))
    print("Новостей     : %d-%d, порог важности %.1f"
          % (CFG["min_items"], CFG["max_items"], CFG["min_score"]))
    print("Модели       :", CFG["model_rank"], "/", CFG["model_summary"])
    print()
    good = True

    if config.TG_TOKEN:
        try:
            bot = tg_call("getMe", {})
            print("[OK]   Telegram: @%s" % bot.get("username"))
        except Exception as exc:  # noqa: BLE001
            print("[FAIL] Telegram: %s" % exc)
            good = False
    else:
        print("[FAIL] Telegram: нет TELEGRAM_BOT_TOKEN")
        good = False

    if config.TG_CHAT:
        print("[OK]   chat_id: %s" % config.TG_CHAT)
    else:
        print("[FAIL] chat_id не задан")
        good = False

    if config.DS_KEY:
        try:
            _data, usage = llm_json("Отвечай только json.",
                                    'Верни ровно {"items": [{"id": 0, "ok": true}]}',
                                    CFG["model_rank"], max_tokens=60)
            print("[OK]   DeepSeek отвечает (%s), стоимость проверки ~$%.6f"
                  % (CFG["model_rank"], llm_cost(usage)))
        except Exception as exc:  # noqa: BLE001
            print("[FAIL] DeepSeek: %s" % exc)
            good = False
        status, balance, _ = post_json(
            CFG["llm_base"].rstrip("/") + "/user/balance", {},
            {"Authorization": "Bearer " + config.DS_KEY}, 20)
        if status == 200 and balance:
            infos = balance.get("balance_infos") or []
            if infos:
                print("[OK]   Баланс DeepSeek: %s %s"
                      % (infos[0].get("total_balance"), infos[0].get("currency")))
    else:
        print("[FAIL] DeepSeek: нет DEEPSEEK_API_KEY")
        good = False

    try:
        conn = db()
        count = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        size = DB_FILE.stat().st_size / 1e6 if DB_FILE.exists() else 0
        print("[OK]   База: %d материалов, %.1f МБ" % (count, size))
        conn.close()
    except sqlite3.Error as exc:
        print("[FAIL] База: %s" % exc)
        good = False

    print("\nИТОГ:", "готово к запуску" if good else "есть проблемы, см. [FAIL] выше")
    return 0 if good else 1


def cmd_feeds(_args):
    feeds = all_feeds()
    print("Проверяю %d источников...\n" % len(feeds))
    with ThreadPoolExecutor(max_workers=CFG["concurrency"]) as pool:
        for src, items, err in pool.map(fetch_source, feeds):
            mark = "ok  " if not err else "FAIL"
            print("  [%s] %-20s %3d свежих  %s" % (mark, src[0], len(items), err[:60]))
    return 0


def cmd_collect(_args):
    collect()
    return 0


def cmd_run(args):
    if not args.dry_run:
        require_secrets()
    elif not config.DS_KEY:
        sys.exit("Для dry-run нужен хотя бы DEEPSEEK_API_KEY")
    if not args.no_collect:
        collect()
    build_and_send(dry_run=args.dry_run)
    return 0


def cmd_status(_args):
    conn = db()
    print("=== Последние прогоны ===")
    for row in conn.execute("SELECT kind, at, status, stats FROM runs "
                            "ORDER BY id DESC LIMIT 12"):
        print("  %s  %-8s %-8s %s" % (row["at"][:16], row["kind"], row["status"],
                                      row["stats"][:90]))
    day = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    fresh = conn.execute("SELECT COUNT(*) c FROM items WHERE fetched_at > ?",
                         (day,)).fetchone()["c"]
    sent = conn.execute("SELECT COUNT(*) c FROM sent WHERE sent_at > ?",
                        (week,)).fetchone()["c"]
    print("\nМатериалов за сутки: %d   отправлено за неделю: %d" % (fresh, sent))

    cost = 0.0
    for row in conn.execute("SELECT stats FROM runs WHERE at > ?", (week,)):
        try:
            cost += float(json.loads(row["stats"]).get("cost", 0))
        except (ValueError, TypeError):
            pass
    print("Расход DeepSeek за неделю: $%.4f  (≈ $%.2f/мес)" % (cost, cost * 4.3))

    print("\n=== Проблемные источники ===")
    bad = list(conn.execute("SELECT source_id, fails, err FROM health WHERE fails > 0 "
                            "ORDER BY fails DESC LIMIT 15"))
    if not bad:
        print("  нет — все источники отвечают")
    for row in bad:
        print("  %-22s сбоев подряд: %-3d %s"
              % (row["source_id"], row["fails"], (row["err"] or "")[:55]))

    size = DB_FILE.stat().st_size / 1e6 if DB_FILE.exists() else 0
    print("\nРазмер базы: %.1f МБ   каталог: %s" % (size, HOME))
    conn.close()
    return 0


SERVICE_TEMPLATE = """[Unit]
Description=News digest bot (Telegram)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={workdir}
ExecStart={python} {script} --log-file daemon
Restart=always
RestartSec=60
TimeoutStopSec=20

# Логи пишутся в {workdir}/digest.log с ротацией (3 x 2 МБ).
# В systemd journal не идут — он не растёт из-за этого сервиса.
StandardOutput=journal
StandardError=journal
# Лог событий идёт в свой файл с ротацией; в journal попадают только
# строки старта и аварий — он от этого практически не растёт.
SyslogIdentifier=newsdigest

# Лимиты: сервис физически не может помешать VPN, nginx и postgres
MemoryMax=250M
MemoryAccounting=yes
CPUQuota=35%
CPUAccounting=yes
{weights}
Nice=15
IOSchedulingClass=idle

# Изоляция
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
"""


def cgroup_v2() -> bool:
    return Path("/sys/fs/cgroup/cgroup.controllers").exists()


def cmd_service(_args):
    user = (os.environ.get("SUDO_USER") or os.environ.get("USER")
            or os.environ.get("LOGNAME") or "root")
    v2 = cgroup_v2()
    weights = ("CPUWeight=20\nIOWeight=20" if v2
               else "CPUShares=256\nBlockIOWeight=100")
    unit = SERVICE_TEMPLATE.format(
        user=user, workdir=str(HOME),
        python=shutil.which("python3") or "/usr/bin/python3",
        script=str(LAUNCHER), weights=weights)
    path = HOME / "newsdigest.service"
    HOME.mkdir(parents=True, exist_ok=True)
    path.write_text(unit, encoding="utf-8")
    print(unit)
    print("cgroup: %s — лимиты записаны в подходящем формате\n"
          % ("v2 (unified)" if v2 else "v1 (legacy)"))
    if os.geteuid() == 0:
        print("ВНИМАНИЕ: команда запущена от root, поэтому каталог данных = %s,\n"
              "  а в юните User=%s. Это рассогласование сломает запуск.\n"
              "  Запустите setup и service БЕЗ sudo, от обычного пользователя.\n"
              % (HOME, user))
    print("Файл сохранён: %s\n" % path)
    print("Установка (sudo нужен только на эти три команды):")
    print("  sudo cp %s /etc/systemd/system/newsdigest.service" % path)
    print("  sudo systemctl daemon-reload")
    print("  sudo systemctl enable --now newsdigest")
    print("  systemctl status newsdigest --no-pager")
    print("\nПроверить, что лимиты применились:")
    print("  systemctl show newsdigest -p MemoryMax -p CPUQuotaPerSecUSec")
    print("\nБез root можно и так:")
    print("  nohup python3 %s daemon --log-file >/dev/null 2>&1 &" % LAUNCHER)
    return 0


def build_parser():
    # Общие флаги вынесены в родителя, чтобы работали в ЛЮБОЙ позиции:
    # и `digest.py --log-file daemon`, и `digest.py daemon --log-file`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--log-file", action="store_true",
                        help="писать лог в ~/.newsdigest/digest.log с ротацией")

    parser = argparse.ArgumentParser(
        prog=PROG, description="Ежедневный дайджест новостей в Telegram",
        parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    _add = sub.add_parser

    def add_parser(name, **kw):
        kw.setdefault("parents", [common])
        return _add(name, **kw)

    sub.add_parser = add_parser

    sub.add_parser("setup", help="мастер настройки").set_defaults(func=cmd_setup)
    sub.add_parser("chatid", help="определить chat_id и сохранить").set_defaults(
        func=cmd_chatid)
    sub.add_parser("doctor", help="проверить Telegram, DeepSeek, базу").set_defaults(
        func=cmd_doctor)
    sub.add_parser("feeds", help="проверить каждый источник").set_defaults(func=cmd_feeds)
    sub.add_parser("topics", help="темы и их источники").set_defaults(func=cmd_topics)
    sub.add_parser("collect", help="только собрать новости").set_defaults(
        func=cmd_collect)

    run = sub.add_parser("run", help="собрать и отправить дайджест сейчас")
    run.add_argument("--dry-run", action="store_true", help="показать, не отправлять")
    run.add_argument("--no-collect", action="store_true",
                     help="не собирать заново, взять из базы")
    run.set_defaults(func=cmd_run)

    sub.add_parser("daemon", help="фоновый режим по расписанию").set_defaults(
        func=lambda a: daemon())
    sub.add_parser("status", help="прогоны, расход, здоровье источников").set_defaults(
        func=cmd_status)
    sub.add_parser("service", help="напечатать unit-файл systemd").set_defaults(
        func=cmd_service)
    return parser


def cmd_topics(_args):
    print("Темы (★ — активная). ✏️ помечены источники из %s\n" % PROFILES_FILE)
    for name in sorted(PROFILES):
        prof = PROFILES[name]
        mark = "★" if name == CFG["topic"] else " "
        custom = sum(1 for f in prof["feeds"] if userprofiles.is_custom(name, f[0]))
        print("%s %-12s источников: %2d%s, ключевых слов: %d"
              % (mark, name, len(prof["feeds"]),
                 " (из них своих %d)" % custom if custom else "",
                 len(prof["keywords"])))
    print("\nСменить тему: ND_TOPIC=<имя> или /set topic <имя> в чате.")
    print("Править источники: /feed add|rm в чате или %s руками." % PROFILES_FILE)
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    load_env()
    userprofiles.apply()
    setup_logging(args.verbose, args.log_file)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
