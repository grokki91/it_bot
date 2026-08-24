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

from . import config, sections, subscribers, userprofiles
from .config import (CFG, DB_FILE, ENV_FILE, HOME, LAUNCHER, PROFILES_FILE, PROG,
                     local_now, load_env, setup_logging, tz_label, write_env)
from .daemon import daemon
from .llm import llm_cost, llm_json
from .net import post_json
from .pipeline import build_and_send, build_section
from .profiles import PROFILES, label, profile
from .profiles import title as topic_title       # 'title' занято чатами в setup
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
    token = ask("1/7 Токен Telegram-бота (от @BotFather)", have, secret=True)
    if token != have:
        config.TG_TOKEN = token
    try:
        bot = tg_call("getMe", {})
        print("      бот: @%s\n" % bot.get("username"))
    except Exception as exc:  # noqa: BLE001
        print("      не смог проверить токен: %s\n" % exc)

    print("2/7 chat_id. СНАЧАЛА напишите боту любое сообщение в Telegram")
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
    key = ask("3/7 Ключ DeepSeek (platform.deepseek.com)", have, secret=True)
    if key != have:
        config.DS_KEY = key

    print("4/7 Выпуск идёт по разделам: %s."
          % ", ".join(topic_title(t) for t in sections.defaults()))
    print("    Менять их потом — ND_SECTIONS в ~/.newsdigest/env.")
    topic = ask("    Раздел по умолчанию (для /news и срочных)", CFG["topic"])
    if sections.resolve(topic):
        topic = sections.resolve(topic)
    else:
        print("      неизвестный раздел, оставляю %s" % CFG["topic"])
        topic = CFG["topic"]

    send_at = ask("5/7 Время отправки ЧЧ:ММ (по вашему времени)", CFG["send_at"])
    tz = ask("    Часовой пояс (например Europe/Riga, Europe/Warsaw)", CFG["tz"])
    print("6/7 Выпусков в сутки: 1 — только в назначенное время,")
    print("    2 — ещё один через 12 часов (каждый выпуск стоит запросов к модели).")
    per_day = ask("    Сколько", str(CFG["per_day"]))
    try:
        per_day = max(1, min(int(per_day), subscribers.MAX_PER_DAY))
    except ValueError:
        print("      не понял число, оставляю %s" % CFG["per_day"])
        per_day = CFG["per_day"]
    count = ask("7/7 Сколько новостей в выпуске (5-10)", str(CFG["max_items"]))

    write_env({
        "TELEGRAM_BOT_TOKEN": config.TG_TOKEN,
        "TELEGRAM_CHAT_ID": config.TG_CHAT,
        "DEEPSEEK_API_KEY": config.DS_KEY,
        "ND_TOPIC": topic,
        "ND_SEND_AT": send_at,
        "ND_PER_DAY": str(per_day),
        "ND_TZ": tz,
        "ND_MAX_ITEMS": count,
    })
    CFG["send_at"], CFG["per_day"] = send_at, per_day
    print("\nСохранено в %s (права 600)." % ENV_FILE)
    print("Выпуски будут приходить сами: %s.\n" % subscribers.schedule_human())
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
    plan = sections.plan()
    feeds = {f[0] for topic in plan for f in profile(topic)["feeds"]}
    print("Каталог      :", HOME)
    print("Разделы      : %d (%s) | источников: %d"
          % (len(plan), ", ".join(topic_title(t) for t in plan), len(feeds)))
    print("По умолчанию :", CFG["topic"], "— раздел для срочных новостей")
    print("Часовой пояс :", tz_label(), "| сейчас у вас", local_now().strftime("%H:%M"),
          "| на сервере", datetime.now(timezone.utc).strftime("%H:%M UTC"))
    print("Расписание   : отправка в %s (%d раз(а) в сутки), сбор раз в %d ч"
          % (subscribers.schedule_human(), subscribers.per_day(),
             CFG["collect_every_h"]))
    print("Новостей     : по %d на раздел (до %d за выпуск), порог важности %.1f"
          % (CFG["per_section"], CFG["per_section"] * len(plan), CFG["min_score"]))
    print("Модели       :", CFG["model_rank"], "/", CFG["model_summary"])
    if CFG["web"]:
        print("Страница     : http://%s:%s/ (новости всем, служебное по "
              "паролю: %s, ND_WEB_TOKEN)"
              % ("<ip-вашего-vps>" if CFG["web_host"] == "0.0.0.0"
                 else CFG["web_host"], CFG["web_port"], ENV_FILE))
    else:
        print("Страница     : выключена (ND_WEB=1 включит)")
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
    topic = ""
    if args.section:
        topic = sections.resolve(args.section)
        if not topic:
            sys.exit("Неизвестный раздел %r. Список: python3 %s topics"
                     % (args.section, PROG))
    if not args.no_collect:
        collect([topic] if topic else None)
    if topic:
        print("Раздел: %s\n" % label(topic))
        build_section(topic, args.count, dry_run=args.dry_run)
    else:
        build_and_send(dry_run=args.dry_run)
    return 0


def cmd_web(args):
    """Страница отдельно от демона: удобно, когда демон уже крутится."""
    from .bot import Worker
    from .web import serve, token

    require_secrets()
    print("Пароль страницы: %s" % token())
    print("Открывать: http://<ip-вашего-vps>:%s/\n"
          % (args.port or CFG["web_port"]))
    print("Это обычный HTTP без шифрования — пускайте только себя.")
    print("Безопаснее так: ND_WEB_HOST=127.0.0.1 и ssh -L %s:localhost:%s user@vps\n"
          % (args.port or CFG["web_port"], args.port or CFG["web_port"]))
    serve(Worker().start(), args.host, args.port)
    return 0


def breaking_report(conn, since) -> None:
    """Что было со срочным за неделю.

    Без этого на вопрос «не пропускаю ли я срочное» ответить нечем: видно
    только то, что дошло, а сколько кандидатов отсеялось порогом и насколько
    близко они были — нет.
    """
    counts, best = {}, []
    for row in conn.execute(
            "SELECT status, stats FROM runs WHERE kind='breaking' AND at > ?",
            (since,)):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        try:
            score = float(json.loads(row["stats"]).get("best", 0))
        except (ValueError, TypeError):
            continue
        if score > 0:
            best.append(score)

    print("\n=== Срочные за неделю ===")
    if not counts:
        print("  проверок не было")
        return
    print("  ⚡ молний отправлено:   %d" % counts.get("ok", 0))
    print("  🔔 важного в очередь:   %d  (сводок отправлено: %d)"
          % (counts.get("queued", 0), counts.get("bulletin", 0)))
    print("  отсеяно порогом:       %d  (придержано: %d, модель не ответила: %d)"
          % (counts.get("below-threshold", 0), counts.get("held", 0),
             counts.get("llm-failed", 0)))
    if best:
        near = sum(1 for s in best if CFG["breaking_alert_score"] - 1.5 <= s
                   < CFG["breaking_alert_score"])
        print("  лучшая срочность:      %.1f   средняя: %.1f   порог: %.1f/%.1f"
              % (max(best), sum(best) / len(best), CFG["breaking_alert_score"],
                 CFG["breaking_flash_score"]))
        if near:
            print("  ...и %d раз(а) кандидат не дотянул меньше полутора баллов —"
                  " если такое повторяется, порог стоит опустить" % near)


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

    breaking_report(conn, week)

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
    sub.add_parser("topics", help="разделы и их источники").set_defaults(
        func=cmd_topics)
    sub.add_parser("sections", help="то же, что topics").set_defaults(
        func=cmd_topics)
    sub.add_parser("collect", help="только собрать новости").set_defaults(
        func=cmd_collect)

    run = sub.add_parser("run", help="собрать и отправить дайджест сейчас")
    run.add_argument("--dry-run", action="store_true", help="показать, не отправлять")
    run.add_argument("--no-collect", action="store_true",
                     help="не собирать заново, взять из базы")
    run.add_argument("--section", default="",
                     help="только один раздел, например: --section спорт")
    run.add_argument("--count", type=int, default=0,
                     help="сколько новостей в разделе (с --section)")
    run.set_defaults(func=cmd_run)

    sub.add_parser("daemon", help="фоновый режим по расписанию").set_defaults(
        func=lambda a: daemon())

    web = sub.add_parser("web", help="только страница в браузере, без демона")
    web.add_argument("--host", default="", help="по умолчанию ND_WEB_HOST")
    web.add_argument("--port", type=int, default=0, help="по умолчанию ND_WEB_PORT")
    web.set_defaults(func=cmd_web)

    sub.add_parser("status", help="прогоны, расход, здоровье источников").set_defaults(
        func=cmd_status)
    sub.add_parser("service", help="напечатать unit-файл systemd").set_defaults(
        func=cmd_service)
    return parser


def cmd_topics(_args):
    plan = set(sections.plan())
    print("Разделы (★ — в плановом выпуске). ✏️ помечены источники из %s\n"
          % PROFILES_FILE)
    for name in sections.known():
        prof = PROFILES[name]
        mark = "★" if name in plan else " "
        custom = sum(1 for f in prof["feeds"] if userprofiles.is_custom(name, f[0]))
        print("%s %-11s %-22s источников: %2d%s, ключевых слов: %d"
              % (mark, name, topic_title(name), len(prof["feeds"]),
                 " (из них своих %d)" % custom if custom else "",
                 len(prof["keywords"])))
    print("\nВыбрать разделы: ND_SECTIONS=<через,запятую> в %s." % ENV_FILE)
    print("Топ одного раздела: %s run --section спорт --count 10" % PROG)
    print("Править источники: %s руками." % PROFILES_FILE)
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
