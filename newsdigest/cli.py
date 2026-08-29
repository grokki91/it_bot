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

from . import (candidates, config, factcheck, redact, safety, sections,
               subscribers, trust, userprofiles)
from .config import (CFG, DB_FILE, ENV_FILE, HOME, LAUNCHER, PROFILES_FILE, PROG,
                     local_now, load_env, setup_logging, tz_label, write_env)
from .daemon import daemon
from .feedparse import parse_date
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


def cmd_feeds(args):
    """Проверка источников. С --url — одной ссылки, ещё не добавленной никуда."""
    if getattr(args, "url", ""):
        return check_one_feed(args.url)
    if getattr(args, "candidates", False):
        return check_candidates(getattr(args, "adopt", False))
    feeds = all_feeds(wire_only=getattr(args, "wire", False))
    print("Проверяю %d источников...\n" % len(feeds))
    silent = []
    with ThreadPoolExecutor(max_workers=CFG["concurrency"]) as pool:
        for src, items, err in pool.map(fetch_source, feeds):
            mark = "ok  " if not err else "FAIL"
            if not err and not items:
                mark, _ = "ПУСТО", silent.append(src[0])
            print("  [%-4s] %-20s %3d свежих  %s"
                  % (mark, src[0], len(items), err[:60]))
    if silent:
        # 200 и ноль записей — не ошибка, но и не работа: так выглядит лента,
        # у которой сменился адрес или сломался поисковый синтаксис
        print("\nОтвечают, но ничего не отдают: %s" % ", ".join(silent))
        print("Возможно, у ленты просто нет свежего за %d ч — но если это"
              " повторяется, проверьте адрес." % CFG["window_hours"])
    return 0


def check_candidates(adopt=False):
    """Проверяет источники-кандидаты и, если попросили, добавляет живых.

    Список фидов стареет сам: ленты переезжают, издания закрываются, у части
    сайтов пропадает публичный RSS. Поэтому кандидат сначала должен ответить —
    и только потом попадает в подборку.
    """
    rows = candidates.all_candidates()
    known = {feed[0] for feed in all_feeds(topics=list(PROFILES))}
    todo = [row for row in rows if row[1] not in known]
    print("Кандидатов: %d (уже добавлено раньше: %d)\n"
          % (len(todo), len(rows) - len(todo)))

    def check(row):
        topic, source_id, url, tier, category, why = row
        _src, items, err = fetch_source((source_id, url, tier, category))
        return row, items, err

    alive, dead = [], []
    with ThreadPoolExecutor(max_workers=CFG["concurrency"]) as pool:
        for row, items, err in pool.map(check, todo):
            topic, source_id, url, _tier, _category, why = row
            if err or not items:
                dead.append((row, err or "ответил, но записей нет"))
                print("  [FAIL] %-11s %-20s %s" % (topic, source_id,
                                                   (err or "пусто")[:44]))
            else:
                alive.append(row)
                print("  [ ok ] %-11s %-20s %3d свежих — %s"
                      % (topic, source_id, len(items), why[:44]))

    print("\nОтветили: %d, не ответили: %d" % (len(alive), len(dead)))
    if not alive:
        return 1
    if not adopt:
        print("Добавить ответивших: %s feeds --candidates --adopt" % PROG)
        return 0

    added = 0
    for topic, source_id, url, tier, category, _why in alive:
        try:
            userprofiles.add_feed(topic, url, tier=tier, category=category,
                                  source_id=source_id)
            added += 1
        except ValueError as exc:
            print("  пропускаю %s: %s" % (source_id, exc))
    print("\nДобавлено в %s: %d источник(ов)." % (PROFILES_FILE, added))
    print("Перезапустите демон, чтобы он их увидел.")
    return 0


def check_one_feed(url):
    """Годится ли эта ссылка в источники. Печатает первые заголовки."""
    src = ("проверка", url, 2, "media")
    _src, items, err = fetch_source(src)
    # адрес печатаем без пароля и ключей: этот вывод копируют в issue
    shown = redact.safe_url(url)
    if err:
        print("FAIL  %s\n  %s" % (shown, err))
        return 1
    print("ok    %s\n  свежих за %d ч: %d" % (shown, CFG["window_hours"], len(items)))
    if not items:
        print("  Записей нет. Либо лента давно не обновлялась, либо адрес не тот.")
        return 1
    for row in items[:5]:
        print("  · %s" % row["title"][:90])
    print("\nДобавить: %s руками — раздел, tier и категорию выбираете вы."
          % PROFILES_FILE)
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


def bar(share, width=24) -> str:
    """Полоска для доли 0..1 — глазами быстрее, чем по цифрам."""
    filled = int(round(max(0.0, min(share, 1.0)) * width))
    return "█" * filled + "·" * (width - filled)


def cmd_report(args):
    """Что бот делал за период: разделы, источники, вкусы, срочное.

    `status` отвечает на вопрос «работает ли оно», а этот отчёт — на вопрос
    «стало лучше или хуже». Без него любая правка порогов и весов остаётся
    гаданием: в `runs` цифры копятся, но никто их не сводит.
    """
    days = max(1, int(getattr(args, "days", 7) or 7))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = db()
    try:
        print("=== Отчёт за %d дн. ===\n" % days)
        report_sections(conn, since)
        report_sources(conn, since)
        report_routing(conn)
        report_taste(conn, since)
        breaking_report(conn, since)
    finally:
        conn.close()
    return 0


def report_sections(conn, since) -> None:
    """Сколько новостей каждый раздел дал и как их оценивала модель."""
    rows = list(conn.execute(
        "SELECT section, COUNT(*) AS n, AVG(score) AS avg_score FROM sent "
        "WHERE sent_at > ? GROUP BY section ORDER BY n DESC", (since,)))
    total = sum(row["n"] for row in rows)
    print("--- Разделы (всего новостей: %d) ---" % total)
    if not total:
        print("  выпусков не было\n")
        return
    for row in rows:
        name = topic_title(row["section"]) if row["section"] else "без раздела"
        print("  %-22s %4d  %s  ср. оценка %.1f"
              % (name[:22], row["n"], bar(row["n"] / float(total)),
                 row["avg_score"] or 0))

    # раздел, который есть в подборке, но новостей не дал, — это либо сухие
    # источники, либо слишком узкая маршрутизация. И то и другое надо видеть
    seen = {row["section"] for row in rows}
    silent = [t for t in sections.plan() if t not in seen]
    if silent:
        print("  Ни одной новости за период: %s"
              % ", ".join(topic_title(t) for t in silent))
    print()


def report_sources(conn, since) -> None:
    """Кто наполняет выпуск. Перекос в одного издателя виден сразу."""
    rows = list(conn.execute(
        "SELECT source_id, COUNT(*) AS n FROM sent WHERE sent_at > ? "
        "GROUP BY source_id ORDER BY n DESC LIMIT 12", (since,)))
    total = conn.execute("SELECT COUNT(*) c FROM sent WHERE sent_at > ?",
                         (since,)).fetchone()["c"]
    if not total:
        return
    print("--- Источники в выпуске ---")
    for row in rows:
        print("  %-22s %4d  %s  доверие %.2f"
              % (row["source_id"][:22], row["n"], bar(row["n"] / float(total)),
                 trust.trust(row["source_id"])))
    heavy = rows[0] if rows else None
    if heavy and heavy["n"] > total * 0.25:
        print("  %s занимает больше четверти выпуска — стоит понизить его вес"
              " или добавить конкурентов в раздел" % heavy["source_id"])
    print()


def report_routing(conn) -> None:
    """Насколько маршрутизация справляется: доля материалов со своим разделом."""
    row = conn.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN section != '' THEN 1 ELSE 0 END) "
        "AS routed FROM items").fetchone()
    total = row["n"] or 0
    print("--- Маршрутизация по разделам ---")
    if not total:
        print("  материалов в базе нет\n")
        return
    routed = row["routed"] or 0
    print("  раздел определён: %d из %d  %s  %.0f%%"
          % (routed, total, bar(routed / float(total)),
             100.0 * routed / total))
    if routed < total * 0.8:
        print("  Больше пятой части материалов раскладывается по источнику, а не"
              " по смыслу.")
        print("  Пополните словарь в newsdigest/classify.py или поднимите"
              " classify_max.")
    print()


def report_taste(conn, since) -> None:
    """👍/👎 — единственный сигнал о вкусах. Молчание тоже сигнал."""
    row = conn.execute(
        "SELECT SUM(CASE WHEN verdict='up' THEN 1 ELSE 0 END) AS up, "
        "SUM(CASE WHEN verdict='down' THEN 1 ELSE 0 END) AS down "
        "FROM feedback WHERE at > ?", (since,)).fetchone()
    up, down = row["up"] or 0, row["down"] or 0
    print("--- Обратная связь ---")
    if not up and not down:
        print("  реакций нет: отбор идёт без поправки на вкусы\n")
        return
    print("  👍 %d   👎 %d   доля полезного: %.0f%%"
          % (up, down, 100.0 * up / (up + down)))
    print()


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

    # Фид, который отвечает 200 и отдаёт ноль записей, ошибкой не считается —
    # и раньше выпадал из выпуска молча. Такой источник надо увидеть глазами
    quiet = list(conn.execute(
        "SELECT source_id, empty, empty_at FROM health "
        "WHERE empty >= ? ORDER BY empty DESC LIMIT 15", (CFG["quiet_after_empty"],)))
    if quiet:
        print("\n=== Молчащие источники (отвечают, но ничего не отдают) ===")
        for row in quiet:
            since = parse_date(row["empty_at"] or "")
            days = ((datetime.now(timezone.utc) - since).days
                    if since else None)
            when = "%d дн." % days if days is not None else "?"
            print("  %-22s пустых обходов подряд: %-4d молчит: %s"
                  % (row["source_id"], row["empty"], when))
        print("  Проверьте адрес: %s feeds --url <ссылка>" % PROG)

    safety_report(conn, week)

    size = DB_FILE.stat().st_size / 1e6 if DB_FILE.exists() else 0
    print("\nРазмер базы: %.1f МБ   каталог: %s" % (size, HOME))
    conn.close()
    return 0


def safety_report(conn, week) -> None:
    """Что отсеяли ссылки и фактчек. Молчит, когда отсеивать было нечего.

    Обе проверки работают тихо, и увидеть их работу иначе негде: небезопасная
    ссылка просто уступает место другой, а придержанное событие просто не
    приходит. Раз в неделю на это стоит посмотреть глазами — хотя бы чтобы
    заметить, что проверка забраковала лишнего.
    """
    bad = list(conn.execute(
        "SELECT url, source_id, safe_why FROM items WHERE safe = ? "
        "AND fetched_at > ? ORDER BY fetched_at DESC LIMIT 10",
        (safety.UNSAFE, week)))
    if bad:
        print("\n=== Отбракованные ссылки ===")
        for row in bad:
            print("  %-22s %s" % (row["source_id"], row["safe_why"]))
            print("    %s" % redact.safe_url(row["url"])[:96])

    held = list(conn.execute(
        "SELECT note, at FROM claims WHERE verdict = ? AND at > ? "
        "ORDER BY at DESC LIMIT 10", (factcheck.HOLD, week)))
    if held:
        print("\n=== Придержано до подтверждения ===")
        for row in held:
            print("  %s  %s" % (row["at"][:16], row["note"] or "без объяснения"))
        print("  Карантин снимается сам: подтвердят за %d ч — уйдёт в выпуск."
              % CFG["fact_hold_h"])


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


UPDATE_SERVICE_TEMPLATE = """[Unit]
Description=News digest: подтянуть код из git и перезапустить демона
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=ND_BRANCH={branch}
Environment=ND_SERVICE={service}
ExecStart={script}
TimeoutStartSec=300
SyslogIdentifier=newsdigest-update
"""

UPDATE_TIMER_TEMPLATE = """[Unit]
Description=Проверять обновления News digest каждые {minutes} мин.

[Timer]
OnBootSec=2min
OnUnitActiveSec={minutes}min
AccuracySec=30s
Unit=newsdigest-update.service

[Install]
WantedBy=timers.target
"""


def cmd_autoupdate(args):
    """Юниты для автодеплоя: git pull по таймеру + перезапуск демона."""
    repo = LAUNCHER.parent
    script = repo / "deploy" / "autoupdate.sh"
    minutes = max(1, args.minutes)
    unit = UPDATE_SERVICE_TEMPLATE.format(script=script, branch=args.branch,
                                          service=args.service)
    timer = UPDATE_TIMER_TEMPLATE.format(minutes=minutes)
    HOME.mkdir(parents=True, exist_ok=True)
    unit_path = HOME / "newsdigest-update.service"
    timer_path = HOME / "newsdigest-update.timer"
    unit_path.write_text(unit, encoding="utf-8")
    timer_path.write_text(timer, encoding="utf-8")
    print(unit)
    print(timer)
    if not script.exists():
        print("ВНИМАНИЕ: не найден %s — обновите код из репозитория.\n" % script)
    if not (repo / ".git").exists():
        print("ВНИМАНИЕ: %s — не git-репозиторий. Автообновление работает только\n"
              "  когда код на сервере получен через git clone.\n" % repo)
    print("Файлы сохранены: %s, %s\n" % (unit_path, timer_path))
    print("Установка (sudo нужен только на эти команды):")
    print("  sudo cp %s %s /etc/systemd/system/" % (unit_path, timer_path))
    print("  sudo systemctl daemon-reload")
    print("  sudo systemctl enable --now newsdigest-update.timer")
    print("\nПроверить:")
    print("  systemctl list-timers newsdigest-update --no-pager")
    print("  sudo systemctl start newsdigest-update   # прогнать прямо сейчас")
    print("  journalctl -u newsdigest-update -n 20 --no-pager")
    print("\nТеперь после мержа в %s сервер сам подтянет код и перезапустит %s."
          % (args.branch, args.service))
    print("Прогонять тесты перед перезапуском (и откатываться, если упали):")
    print("  добавьте в юнит строку Environment=ND_SELFTEST=1")
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
    feeds = sub.add_parser("feeds", help="проверить каждый источник")
    feeds.add_argument("--url", default="",
                       help="проверить одну ссылку, ещё никуда не добавленную")
    feeds.add_argument("--wire", action="store_true",
                       help="только быструю полосу: агентства и службы оповещения")
    feeds.add_argument("--candidates", action="store_true",
                       help="проверить источники-кандидаты, которых ещё нет")
    feeds.add_argument("--adopt", action="store_true",
                       help="с --candidates: добавить в профили тех, кто ответил")
    feeds.set_defaults(func=cmd_feeds)
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

    report = sub.add_parser("report", help="что бот делал за период: разделы, "
                                          "источники, вкусы, срочное")
    report.add_argument("--days", type=int, default=7, help="за сколько дней (7)")
    report.set_defaults(func=cmd_report)
    sub.add_parser("status", help="прогоны, расход, здоровье источников").set_defaults(
        func=cmd_status)
    sub.add_parser("service", help="напечатать unit-файл systemd").set_defaults(
        func=cmd_service)

    scrub = sub.add_parser("scrub", help="вычистить секреты из файла или stdin "
                                        "(перед вставкой в issue или PR)")
    scrub.add_argument("files", nargs="*",
                       help="файлы; без аргументов читает stdin")
    scrub.add_argument("--check", action="store_true",
                       help="не печатать текст, а только проверить: код 1, "
                            "если нашлось похожее на секрет")
    scrub.set_defaults(func=cmd_scrub)

    auto = sub.add_parser("autoupdate",
                          help="таймер systemd: сам git pull и перезапуск демона")
    auto.add_argument("--branch", default="main", help="какую ветку тянуть (main)")
    auto.add_argument("--minutes", type=int, default=5,
                      help="как часто проверять обновления, минут (5)")
    auto.add_argument("--service", default="newsdigest",
                      help="имя юнита демона (newsdigest)")
    auto.set_defaults(func=cmd_autoupdate)
    return parser


def cmd_scrub(args):
    """Вычищает секреты из файла или stdin — перед вставкой в issue или PR.

    Лог и вывод команд бот чистит сам, но в issue попадает и то, что человек
    собрал руками: кусок `env`, адрес ленты, вывод чужой утилиты. Эта команда
    — последний рубеж: `digest.py scrub ~/.newsdigest/digest.log > safe.txt`.
    С `--check` ничего не печатает, а только говорит, есть ли в файле похожее
    на секрет (тем же занимается проверка в CI).
    """
    paths = list(args.files or ["-"])
    return redact.main((["--check"] if args.check else []) + paths)


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
