# -*- coding: utf-8 -*-
"""Разбор входящих апдейтов: команды в чате и фоновые задачи.

Демон не просто ждёт назначенного часа — он слушает Telegram и отвечает.
Тяжёлое (сбор фидов, запросы к модели) уходит в отдельную нить: пока идёт
прогон, бот продолжает отвечать на команды.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import config, feedback, settings, userprofiles
from .config import CFG, local_now, log, tz_label
from .pipeline import build_and_send
from .profiles import profile
from .render import esc, mark_pressed
from .sources import all_feeds, collect, fetch_source
from .storage import db, item_facts, meta_get, meta_set, take_leftover
from .telegram import tg_answer_callback, tg_call, tg_edit_markup, tg_send

#: сколько секунд держим long-poll соединение открытым
POLL_TIMEOUT = 25


# ------------------------------------------------------------------ фоновые задачи
class Worker:
    """Одна фоновая нить на все долгие задачи.

    Больше одной нити не нужно: и сбор, и запросы к модели упираются в сеть
    и в лимиты API, а параллельные прогоны только мешали бы друг другу.
    """

    def __init__(self):
        self.queue = queue.Queue()
        self.current = ""
        self._lock = threading.Lock()
        self._pending = set()
        self._thread = threading.Thread(target=self._loop, name="nd-worker",
                                        daemon=True)

    def start(self):
        self._thread.start()
        return self

    def busy(self) -> str:
        with self._lock:
            return self.current

    def submit(self, name: str, fn, chat_id="") -> bool:
        """Ставит задачу в очередь. False — такая задача уже выполняется."""
        with self._lock:
            if name == self.current or name in self._pending:
                return False
            self._pending.add(name)
        self.queue.put((name, fn, str(chat_id or "")))
        return True

    def _loop(self):
        while True:
            name, fn, chat_id = self.queue.get()
            with self._lock:
                self._pending.discard(name)
                self.current = name
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — нить не должна умирать
                log.exception("Задача %s упала: %s", name, exc)
                if chat_id:
                    try:
                        tg_send(chat_id, "⚠️ %s: не получилось — %s"
                                % (esc(name), esc(str(exc)[:300])))
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                with self._lock:
                    self.current = ""
                self.queue.task_done()


# ------------------------------------------------------------------ реестр команд
class Command:
    def __init__(self, name, fn, help_text, heavy, hidden):
        self.name, self.fn = name, fn
        self.help, self.heavy, self.hidden = help_text, heavy, hidden


HANDLERS = {}


def command(name, help_text="", heavy=False, hidden=False, aliases=()):
    def deco(fn):
        cmd = Command(name, fn, help_text, heavy, hidden)
        HANDLERS[name] = cmd
        for alias in aliases:
            HANDLERS[alias] = Command(alias, fn, "", heavy, True)
        return fn
    return deco


class Ctx:
    """Всё, что нужно обработчику команды."""

    def __init__(self, chat_id, args, conn, worker, user=""):
        self.chat_id, self.args, self.conn = chat_id, args, conn
        self.worker, self.user = worker, user

    def arg(self, index, default=""):
        return self.args[index] if index < len(self.args) else default


# --------------------------------------------------------------------- доступ
_REFUSED = set()


def allowed_chats() -> set:
    """Кому бот подчиняется. Пока это владелец плюс явно добавленные чаты."""
    chats = {str(config.TG_CHAT)} if config.TG_CHAT else set()
    conn = db()
    extra = meta_get(conn, "extra_chats", "")
    conn.close()
    return chats | {c.strip() for c in extra.split(",") if c.strip()}


def is_allowed(chat_id) -> bool:
    return str(chat_id) in allowed_chats()


# ------------------------------------------------------------------- команды
@command("help", "эта справка", aliases=("start",))
def cmd_help(ctx):
    lines = ["🤖 <b>Дайджест новостей</b>", ""]
    for name, cmd in sorted(HANDLERS.items()):
        if not cmd.hidden and cmd.help:
            lines.append("/%s — %s" % (name, cmd.help))
    lines += ["", "Тема: <b>%s</b>, выпуск в %s (%s)."
              % (esc(CFG["topic"]), esc(CFG["send_at"]), esc(tz_label()))]
    return "\n".join(lines)


@command("digest", "собрать и прислать выпуск сейчас", heavy=True)
def cmd_digest(ctx):
    chat_id = ctx.chat_id
    tg_send(chat_id, "🔄 Собираю свежее — займёт минуту-другую.")

    def job():
        collect()
        stats = build_and_send(chat_id=chat_id)
        if not stats.get("sent"):
            tg_send(chat_id, "🌘 Ничего нового, что стоило бы прислать. "
                             "Так бывает: тихий день или всё уже уходило раньше.")

    if not ctx.worker.submit("digest", job, chat_id):
        return "⏳ Уже собираю выпуск, подождите."
    return None


@command("more", "ещё новости, не попавшие в выпуск")
def cmd_more(ctx):
    try:
        count = max(1, min(int(ctx.arg(0, "5")), 15))
    except ValueError:
        count = 5
    rows = take_leftover(ctx.conn, ctx.chat_id, count)
    if not rows:
        return ("Запас пуст. Он наполняется при сборке выпуска — "
                "пришлите /digest или дождитесь утреннего.")
    lines = ["📎 <b>Ещё из вчерашнего отбора</b>", ""]
    for row in rows:
        lines.append('• <a href="%s">%s</a> — %s · ⭐ %.1f'
                     % (esc(row["url"]), esc(row["title"]),
                        esc(row["source_id"]), row["score"]))
    return "\n".join(lines)


@command("breaking", "проверить, нет ли срочного прямо сейчас", heavy=True)
def cmd_breaking(ctx):
    from . import breaking

    chat_id = ctx.chat_id
    skip = breaking.why_not(ctx.conn)

    def job():
        if not breaking.check(chat_id=chat_id):
            tg_send(chat_id, "🕊 Ничего срочного: подтверждённых событий выше "
                             "порога %.1f нет." % CFG["breaking_min_score"])

    if skip:
        return "Срочные сейчас не ищу — %s." % esc(skip)
    if not ctx.worker.submit("breaking", job, chat_id):
        return "⏳ Уже проверяю."
    return "⚡ Смотрю, нет ли чего-то срочного..."


@command("saved", "закладки, отмеченные кнопкой 🔖")
def cmd_saved(ctx):
    if ctx.arg(0) in ("clear", "очистить"):
        ctx.conn.execute("DELETE FROM saved WHERE chat_id=?", (ctx.chat_id,))
        ctx.conn.commit()
        return "🔖 Закладки очищены."
    rows = feedback.bookmarks(ctx.conn, ctx.chat_id)
    if not rows:
        return ("Закладок пока нет. Кнопка 🔖 под новостью в выпуске "
                "откладывает её сюда.")
    lines = ["🔖 <b>Закладки</b>", ""]
    for row in rows:
        lines.append('• <a href="%s">%s</a> — %s'
                     % (esc(row["url"]), esc(row["title"] or row["url"]),
                        esc(row["source_id"])))
    lines += ["", "<i>/saved clear — очистить</i>"]
    return "\n".join(lines)


@command("taste", "что бот выучил про ваши вкусы")
def cmd_taste(ctx):
    aff = feedback.Affinity.load(ctx.conn, ctx.chat_id)
    total = ctx.conn.execute("SELECT COUNT(*) c FROM feedback WHERE chat_id=?",
                             (ctx.chat_id,)).fetchone()["c"]
    if not total:
        return ("Пока ничего не знаю о ваших вкусах. Жмите 👍 и 👎 под новостями — "
                "через неделю выпуск начнёт подстраиваться.")
    liked, disliked = aff.top()
    lines = ["🎯 <b>Что я о вас понял</b>",
             "оценок собрано: %d, вес в отборе: %.2f" % (total, CFG["feedback_weight"])]
    if liked:
        lines.append("нравится: " + esc(", ".join("%s %+.2f" % kv for kv in liked)))
    if disliked:
        lines.append("не заходит: " + esc(", ".join("%s %+.2f" % kv for kv in disliked)))
    if not liked and not disliked:
        lines.append("Оценок пока мало, чтобы делать выводы — продолжайте.")
    return "\n".join(lines)


@command("status", "что происходит: расписание, расход, источники")
def cmd_status(ctx):
    conn = ctx.conn
    day = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    fresh = conn.execute("SELECT COUNT(*) c FROM items WHERE fetched_at > ?",
                         (day,)).fetchone()["c"]
    sent = conn.execute("SELECT COUNT(*) c FROM sent WHERE sent_at > ?",
                        (week,)).fetchone()["c"]
    bad = list(conn.execute("SELECT source_id FROM health WHERE fails >= ? ",
                            (CFG["mute_after_fails"],)))
    cost = 0.0
    for row in conn.execute("SELECT stats FROM runs WHERE at > ?", (week,)):
        try:
            cost += float(json.loads(row["stats"]).get("cost", 0))
        except (ValueError, TypeError):
            pass

    lines = [
        "📊 <b>Состояние</b>",
        "Тема: <b>%s</b> · источников: %d" % (esc(CFG["topic"]), len(profile()["feeds"])),
        "Следующий выпуск: %s" % esc(next_send_human(conn)),
        "Материалов за сутки: %d · отправлено за неделю: %d" % (fresh, sent),
        "Расход модели за неделю: $%.4f" % cost,
    ]
    if bad:
        lines.append("Отключённые источники: %s"
                     % esc(", ".join(r["source_id"] for r in bad[:6])))
    if is_paused(conn):
        lines.append("⏸ Рассылка на паузе — /resume вернёт.")
    busy = ctx.worker.busy() if ctx.worker else ""
    if busy:
        lines.append("Сейчас выполняется: %s" % esc(busy))
    return "\n".join(lines)


@command("feeds", "проверить источники по одному", heavy=True)
def cmd_feeds(ctx):
    chat_id = ctx.chat_id
    feeds = all_feeds()

    def job():
        ok, rows = 0, []
        with ThreadPoolExecutor(max_workers=CFG["concurrency"]) as pool:
            for src, items, err in pool.map(fetch_source, feeds):
                if err:
                    rows.append("❌ %s — %s" % (esc(src[0]), esc(err[:50])))
                else:
                    ok += 1
                    rows.append("✅ %s — %d" % (esc(src[0]), len(items)))
        head = "🩺 <b>Источники: %d из %d отвечают</b>\n" % (ok, len(feeds))
        tg_send(chat_id, head + "\n".join(sorted(rows)))

    tg_send(chat_id, "🩺 Проверяю %d источников..." % len(feeds))
    if not ctx.worker.submit("feeds", job, chat_id):
        return "⏳ Проверка уже идёт."
    return None


FEED_HELP = ("Источники темы <b>%s</b>:\n"
             "/feed list — список\n"
             "/feed add &lt;ссылка&gt; [tier 1-3] [категория]\n"
             "/feed rm &lt;имя&gt;\n\n"
             "tier: 1 первоисточник, 2 СМИ, 3 агрегатор.\n"
             "категории: %s")


@command("feed", "добавить или убрать источник")
def cmd_feed(ctx):
    topic = CFG["topic"]
    action = (ctx.arg(0) or "list").lower()

    if action in ("list", "список"):
        feeds = profile()["feeds"]
        lines = ["📚 <b>%d источников темы «%s»</b>" % (len(feeds), esc(topic)), ""]
        for source_id, url, tier, category in sorted(feeds):
            mark = " ✏️" if userprofiles.is_custom(topic, source_id) else ""
            lines.append("<code>%s</code>%s · t%d · %s"
                         % (esc(source_id), mark, tier, esc(category)))
        lines += ["", "<i>✏️ — добавлено вами. /feed add и /feed rm правят список.</i>"]
        return "\n".join(lines)

    if action in ("add", "добавить"):
        url = ctx.arg(1)
        if not url:
            return FEED_HELP % (esc(topic), ", ".join(userprofiles.CATEGORIES))
        tier = ctx.arg(2, "2")
        category = ctx.arg(3, "media")
        try:
            feed = userprofiles.add_feed(topic, url, tier, category)
        except ValueError as exc:
            return "Не вышло: %s" % esc(exc)

        # проверяем сразу: молчащий фид лучше увидеть здесь, а не через сутки
        _src, items, err = fetch_source(feed)
        if err:
            userprofiles.remove_feed(topic, feed[0])
            return ("Источник не отвечает (%s) — не добавляю.\n"
                    "Проверьте, что это ссылка именно на RSS/Atom." % esc(err[:80]))
        note = ("свежих записей: %d" % len(items) if items
                else "свежего пока нет — это нормально для редких блогов")
        return ("✅ Добавил <code>%s</code> (t%d, %s), %s.\nВ теме теперь %d источников."
                % (esc(feed[0]), feed[2], esc(feed[3]), note, len(profile()["feeds"])))

    if action in ("rm", "remove", "del", "убрать"):
        source_id = ctx.arg(1)
        if not source_id:
            return "Что убрать? /feed rm &lt;имя&gt;, список — /feed list"
        if not userprofiles.remove_feed(topic, source_id):
            return "В теме «%s» нет источника <code>%s</code>." % (esc(topic),
                                                                   esc(source_id))
        return ("🗑 Убрал <code>%s</code>. Осталось %d источников.\n"
                "<i>Вернуть: /feed add со ссылкой.</i>"
                % (esc(source_id), len(profile()["feeds"])))

    return FEED_HELP % (esc(topic), ", ".join(userprofiles.CATEGORIES))


@command("keywords", "ключевые слова для фильтра Hacker News")
def cmd_keywords(ctx):
    topic = CFG["topic"]
    action = (ctx.arg(0) or "list").lower()
    if action in ("add", "добавить") and len(ctx.args) > 1:
        words = userprofiles.edit_keywords(topic, add=ctx.args[1:])
        return "✅ Добавил. Сейчас %d слов:\n<code>%s</code>" % (len(words),
                                                                esc(", ".join(words)))
    if action in ("rm", "remove", "убрать") and len(ctx.args) > 1:
        words = userprofiles.edit_keywords(topic, remove=ctx.args[1:])
        return "🗑 Убрал. Осталось %d слов:\n<code>%s</code>" % (len(words),
                                                                 esc(", ".join(words)))
    words = profile()["keywords"]
    return ("🔑 <b>Ключевые слова темы «%s»</b> (%d)\n<code>%s</code>\n\n"
            "<i>Ими фильтруется Hacker News. /keywords add слово, "
            "/keywords rm слово.</i>"
            % (esc(topic), len(words), esc(", ".join(words))))


@command("pause", "приостановить рассылку")
def cmd_pause(ctx):
    meta_set(ctx.conn, "paused", "1")
    return "⏸ Рассылка на паузе. Сбор новостей продолжается — /resume вернёт выпуски."


@command("resume", "вернуть рассылку")
def cmd_resume(ctx):
    meta_set(ctx.conn, "paused", "0")
    return "▶️ Рассылка включена. Следующий выпуск: %s" % esc(next_send_human(ctx.conn))


@command("settings", "текущие настройки")
def cmd_settings(ctx):
    lines = ["⚙️ <b>Настройки</b> (часовой пояс: %s)" % esc(tz_label()), ""]
    for name, value, describe in settings.overview():
        lines.append("<code>%s</code> = <b>%s</b> — %s"
                     % (esc(name), esc(value), esc(describe)))
    lines += ["", "Менять: <code>/set имя значение</code>, "
                  "например <code>/set time 08:30</code>."]
    return "\n".join(lines)


@command("set", "изменить настройку: /set время 08:30")
def cmd_set(ctx):
    if not ctx.args:
        return cmd_settings(ctx)
    if len(ctx.args) < 2:
        key, setting = settings.resolve(ctx.arg(0))
        if not setting:
            return "Не знаю настройку «%s». Список — /settings" % esc(ctx.arg(0))
        return ("<code>%s</code> сейчас <b>%s</b> — %s.\nЗадать: "
                "<code>/set %s значение</code>"
                % (esc(key), esc(setting.current()), esc(setting.describe), esc(key)))
    try:
        key, shown = settings.apply(ctx.arg(0), " ".join(ctx.args[1:]))
    except settings.Invalid as exc:
        return "⚠️ %s" % esc(exc)

    tail = ""
    if key in ("time", "tz"):
        tail = "\nСледующий выпуск: %s" % esc(next_send_human(ctx.conn))
    if key == "topic":
        tail = ("\nИсточников в теме: %d. Первый выпуск по новой теме соберётся "
                "после ближайшего сбора." % len(profile()["feeds"]))
    return "✅ <code>%s</code> = <b>%s</b>%s" % (esc(key), esc(shown), tail)


# ------------------------------------------------------------------ расписание
def is_paused(conn) -> bool:
    return meta_get(conn, "paused", "0") == "1"


def next_send_human(conn) -> str:
    """«сегодня в 09:00» / «завтра в 09:00» — с учётом уже отправленного."""
    try:
        hour, minute = [int(x) for x in CFG["send_at"].split(":")]
    except ValueError:
        return "время отправки задано неверно (%s)" % CFG["send_at"]
    now = local_now()
    today = now.strftime("%Y-%m-%d")
    done_today = meta_get(conn, "last_digest_date", "") == today
    due_passed = now.hour * 60 + now.minute >= hour * 60 + minute
    when = "завтра" if (done_today or due_passed) else "сегодня"
    return "%s в %02d:%02d (%s)" % (when, hour, minute, tz_label())


# --------------------------------------------------------------------- разбор
def parse_command(text: str):
    """'/more@my_bot 7' -> ('more', ['7']). Не команда -> (None, [])."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, []
    parts = text.split()
    name = parts[0][1:].split("@", 1)[0].lower()
    return (name or None), parts[1:]


def handle_message(msg, worker) -> None:
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not chat_id or not text:
        return
    user = ((msg.get("from") or {}).get("username")
            or (msg.get("from") or {}).get("first_name") or "")

    if not is_allowed(chat_id):
        log.warning("Команда от постороннего чата %s (%s): %r", chat_id, user, text[:60])
        if chat_id not in _REFUSED:
            _REFUSED.add(chat_id)
            try:
                tg_send(chat_id, "Это личный бот-дайджест. "
                                 "Он отвечает только своему владельцу.")
            except Exception:  # noqa: BLE001
                pass
        return

    name, args = parse_command(text)
    if not name:
        return
    cmd = HANDLERS.get(name)
    if not cmd:
        tg_send(chat_id, "Не знаю команду /%s. Список: /help" % esc(name))
        return
    if cmd.heavy and worker is None:
        tg_send(chat_id, "Фоновые задачи сейчас недоступны — демон не запущен.")
        return

    conn = db()
    try:
        reply = cmd.fn(Ctx(chat_id, args, conn, worker, user))
    finally:
        conn.close()
    if reply:
        tg_send(chat_id, reply, silent=True)


TOAST = {
    feedback.UP:   "Учёл 👍 — такое буду поднимать выше",
    feedback.DOWN: "Учёл 👎 — такого станет меньше",
}


def handle_callback(cb, worker) -> None:
    """Нажатие кнопки под карточкой: записать оценку и переставить галочку."""
    message = cb.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    data = cb.get("data") or ""
    if not is_allowed(chat_id):
        tg_answer_callback(cb.get("id"), "Это личный бот-дайджест.")
        return
    if not data.startswith("fb:") or data.count(":") < 2:
        tg_answer_callback(cb.get("id"))
        return

    _, kind, url_hash = data.split(":", 2)
    conn = db()
    try:
        facts = item_facts(conn, url_hash)
        if kind in (feedback.UP, feedback.DOWN):
            feedback.record(conn, chat_id, url_hash, kind, facts)
            pressed, toast = True, TOAST[kind]
        elif kind == "save":
            pressed = feedback.save_bookmark(conn, chat_id, url_hash, facts)
            toast = "🔖 В закладках, /saved" if pressed else "Убрал из закладок"
        else:
            tg_answer_callback(cb.get("id"))
            return
    finally:
        conn.close()

    tg_answer_callback(cb.get("id"), toast)
    keyboard = ((message.get("reply_markup") or {}).get("inline_keyboard") or [])
    if keyboard:
        try:
            tg_edit_markup(chat_id, message.get("message_id"),
                           mark_pressed(keyboard, data, pressed))
        except RuntimeError as exc:
            log.debug("Разметку обновить не удалось: %s", exc)


def handle_update(upd, worker) -> None:
    msg = upd.get("message") or upd.get("channel_post")
    if msg:
        handle_message(msg, worker)
    elif upd.get("callback_query"):
        handle_callback(upd["callback_query"], worker)


# ----------------------------------------------------------------- long-polling
def drain_backlog(conn) -> None:
    """При первом запуске пропускаем то, что накопилось, пока бота не было.

    Иначе после суток простоя бот выполнит всё, что ему успели написать.
    """
    if meta_get(conn, "tg_offset", ""):
        return
    try:
        updates = tg_call("getUpdates", {"offset": -1, "timeout": 0}, attempts=1)
    except RuntimeError as exc:
        log.warning("Не смог прочитать очередь апдейтов: %s", exc)
        return
    if updates:
        meta_set(conn, "tg_offset", int(updates[-1]["update_id"]) + 1)
        log.info("Пропустил %d старых апдейтов", len(updates))
    else:
        meta_set(conn, "tg_offset", 0)


def poll_once(worker, timeout=POLL_TIMEOUT) -> int:
    """Один заход long-poll. Возвращает число обработанных апдейтов."""
    conn = db()
    offset = int(meta_get(conn, "tg_offset", "0") or 0)
    conn.close()
    payload = {"timeout": timeout, "limit": 20,
               "allowed_updates": ["message", "channel_post", "callback_query"]}
    if offset:
        payload["offset"] = offset
    updates = tg_call("getUpdates", payload, attempts=1, timeout=timeout + 15)

    handled = 0
    highest = offset
    for upd in updates:
        highest = max(highest, int(upd.get("update_id", 0)) + 1)
        try:
            handle_update(upd, worker)
            handled += 1
        except Exception as exc:  # noqa: BLE001 — один кривой апдейт не ломает цикл
            log.exception("Апдейт %s не обработан: %s", upd.get("update_id"), exc)
    if highest != offset:
        conn = db()
        meta_set(conn, "tg_offset", highest)
        conn.close()
    return handled


def poll_forever(worker, stop=None) -> None:
    """Цикл приёма команд. Сетевые сбои гасим нарастающей паузой."""
    backoff = 1
    while not (stop and stop.is_set()):
        try:
            poll_once(worker)
            backoff = 1
        except Exception as exc:  # noqa: BLE001
            log.warning("Приём апдейтов не удался: %s (пауза %ds)", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
