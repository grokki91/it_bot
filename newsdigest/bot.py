# -*- coding: utf-8 -*-
"""Команды, нажатия кнопок и фоновые задачи.

В Telegram бот только рассылает: команд там нет, он принимает лишь нажатия
кнопок под выпуском (👍/👎/🔖) и решения владельца по заявкам новых чатов.
На любую команду из чата приходит одна и та же справка о расписании — вся
переписка с ботом сводится к ней (ND_CHAT_REPLY=off убирает и её). Страница в
браузере команд тоже не выполняет: боту командуют на самом VPS, через
`digest.py` и env. Обработчики из HANDLERS остаются здесь — на них держатся
разбор команды и проверки доступа.

Тяжёлое (сбор фидов, запросы к модели) уходит в отдельную нить: пока идёт
прогон, бот продолжает отвечать.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import (config, feedback, sections, settings, subscribers, translate,
               userprofiles)
from .config import CFG, log, tz_label
from .pipeline import build_and_send, build_section
from .profiles import label, profile, title
from .render import MONTHS, collapse, esc, expand, mark_pressed
from .sources import all_feeds, collect, fetch_source
from .storage import (db, item_facts, meta_get, meta_set, outbox_keyboard,
                      take_leftover)
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
    def __init__(self, name, fn, help_text, heavy, hidden, owner):
        self.name, self.fn = name, fn
        self.help, self.heavy, self.hidden = help_text, heavy, hidden
        self.owner = owner


HANDLERS = {}


def command(name, help_text="", heavy=False, hidden=False, owner=False, aliases=()):
    def deco(fn):
        HANDLERS[name] = Command(name, fn, help_text, heavy, hidden, owner)
        for alias in aliases:
            HANDLERS[alias] = Command(alias, fn, "", heavy, True, owner)
        return fn
    return deco


class Ctx:
    """Всё, что нужно обработчику команды."""

    def __init__(self, chat_id, args, conn, worker, user=""):
        self.chat_id, self.args, self.conn = str(chat_id), args, conn
        self.worker, self.user = worker, user
        self.sub = subscribers.get(conn, chat_id)

    @property
    def owner(self) -> bool:
        return is_owner(self.chat_id)

    def arg(self, index, default=""):
        return self.args[index] if index < len(self.args) else default

    def next_send(self) -> str:
        return subscribers.next_send_human(self.sub)

    def sections(self) -> list:
        return sections.plan(self.sub)


# --------------------------------------------------------------------- доступ
_REFUSED = set()


def is_owner(chat_id) -> bool:
    return bool(config.TG_CHAT) and str(chat_id) == str(config.TG_CHAT)


def allowed_chats() -> set:
    """Кому бот подчиняется: владелец и одобренные подписчики."""
    conn = db()
    try:
        subscribers.ensure_owner(conn)
        chats = {s["chat_id"] for s in subscribers.all_rows(conn)}
    finally:
        conn.close()
    if config.TG_CHAT:
        chats.add(str(config.TG_CHAT))
    return chats


def is_allowed(chat_id) -> bool:
    return str(chat_id) in allowed_chats()


def greet_stranger(chat_id, title, kind) -> None:
    """Что делать с новым чатом — решает настройка signup.

    По умолчанию «ask»: чат ждёт одобрения, владелец получает кнопки. Так
    посторонний не начнёт тратить ваш баланс модели, но и отшивать вручную
    каждого знакомого не приходится.
    """
    mode = str(CFG["signup"]).lower()
    if mode == "open":
        conn = db()
        try:
            subscribers.add(conn, chat_id, role="member", title=title, kind=kind)
        finally:
            conn.close()
        tg_send(chat_id, "👋 Подписал вас на дайджест. Выпуски будут приходить "
                         "сами: %s." % esc(subscribers.schedule_human()))
        log.info("Новый подписчик %s (%s) — открытая подписка", chat_id, title)
        return

    if mode != "ask" or not config.TG_CHAT:
        if chat_id not in _REFUSED:
            _REFUSED.add(chat_id)
            tg_send(chat_id, "Это личный бот-дайджест. "
                             "Он отвечает только своему владельцу.")
        return

    conn = db()
    try:
        existing = subscribers.get(conn, chat_id)
        subscribers.add(conn, chat_id, role="pending", title=title, kind=kind)
    finally:
        conn.close()
    if existing is not None and existing["role"] == "pending":
        return          # заявка уже висит: молчим, чтобы не превратиться в эхо
    tg_send(chat_id, "👋 Заявка отправлена владельцу. Как только он одобрит, "
                     "начну присылать выпуски.")
    tg_send(config.TG_CHAT,
            "🔔 Новый чат просится на дайджест:\n<b>%s</b> (<code>%s</code>, %s)"
            % (esc(title or "без имени"), esc(chat_id), esc(kind)),
            keyboard=[[{"text": "✅ Пустить", "callback_data": "sub:ok:%s" % chat_id},
                       {"text": "🚫 Нет", "callback_data": "sub:no:%s" % chat_id}]])


# ------------------------------------------------------------------- команды
@command("help", "эта справка", aliases=("start",))
def cmd_help(ctx):
    lines = ["🤖 <b>Дайджест новостей</b>", ""]
    for name, cmd in sorted(HANDLERS.items()):
        if cmd.hidden or not cmd.help:
            continue
        if cmd.owner and not ctx.owner:
            continue
        # подсказки живут и в чате, и на странице: угловые скобки в тексте
        # вроде «/subs rm <id>» Telegram примет за тег и отклонит разметку
        lines.append("/%s — %s" % (name, esc(cmd.help)))
    mine = ctx.sections()
    lines += ["",
              "Выпуск идёт сам: %s — по %d новости из %d разделов. "
              "Следующий %s."
              % (esc(subscribers.schedule_human(ctx.sub)),
                 sections.per_section(ctx.sub), len(mine), esc(ctx.next_send())),
              "Топ одного раздела прямо сейчас: <code>/news спорт 10</code>.",
              "Разделы и их выбор: /sections",
              "",
              "<i>Команды работают здесь, на странице. В Telegram бот только "
              "присылает выпуски и принимает кнопки 👍/👎/🔖.</i>"]
    return "\n".join(lines)


@command("digest", "собрать и прислать выпуск сейчас", heavy=True)
def cmd_digest(ctx):
    if ctx.args:                    # /digest космос 7 — то же, что /news
        return cmd_news(ctx)
    chat_id, sub = ctx.chat_id, ctx.sub
    tg_send(chat_id, "🔄 Собираю свежее по %d разделам — займёт минуту-другую."
            % len(ctx.sections()))

    def job():
        collect()
        stats = build_and_send(chat_id=chat_id, sub=sub)
        if not stats.get("sent"):
            tg_send(chat_id, "🌘 Ничего нового, что стоило бы прислать. "
                             "Так бывает: тихий день или всё уже уходило раньше.")

    if not ctx.worker.submit("digest", job, chat_id):
        return "⏳ Уже собираю выпуск, подождите."
    return None


def split_count(args):
    """'спорт 10' -> ('спорт', 10). Числа в конце — это сколько новостей."""
    args = list(args)
    count = 0
    if args and args[-1].isdigit():
        count = int(args[-1])
        args = args[:-1]
    return " ".join(args).strip(), count


@command("news", "топ раздела сейчас: /news спорт 10", heavy=True,
         aliases=("раздел", "топ"))
def cmd_news(ctx):
    name, count = split_count(ctx.args)
    topic = sections.resolve(name) if name else CFG["topic"]
    if not topic:
        return ("Не знаю раздел «%s». Список — /sections" % esc(name))
    limit = max(1, min(count or CFG["section_items"], CFG["section_max_items"]))
    chat_id, sub = ctx.chat_id, ctx.sub

    def job():
        collect([topic])            # только источники этого раздела — так быстрее
        stats = build_section(topic, limit, chat_id=chat_id, sub=sub)
        if not stats.get("sent"):
            tg_send(chat_id, "🌘 В разделе %s нового нет: либо тихо, либо всё "
                             "уже уходило вам раньше." % esc(label(topic)))

    if not ctx.worker.submit("news:%s" % topic, job, chat_id):
        return "⏳ Этот раздел уже собираю."
    return "🔎 Собираю топ-%d: %s — минуту." % (limit, esc(label(topic)))


SECTION_HELP = ("\n<i>/sections add кино · /sections rm спорт · "
                "/sections all · /sections reset</i>\n"
                "<i>Новости одного раздела: /news медицина 10</i>")


@command("sections", "разделы выпуска: список и выбор",
         aliases=("разделы", "topics"))
def cmd_sections(ctx):
    action = (ctx.arg(0) or "list").lower()
    rest = " ".join(ctx.args[1:])
    mine = ctx.sections()

    if action in ("add", "добавить", "+"):
        topics, unknown = sections.parse(rest)
        if unknown or not topics:
            return "Не знаю раздел(ы): %s. Список — /sections" % esc(
                ", ".join(unknown) or "—")
        return set_sections(ctx, mine + [t for t in topics if t not in mine])

    if action in ("rm", "remove", "убрать", "-", "del"):
        topics, unknown = sections.parse(rest)
        if unknown or not topics:
            return "Не знаю раздел(ы): %s. Список — /sections" % esc(
                ", ".join(unknown) or "—")
        left = [t for t in mine if t not in topics]
        if not left:
            return ("Так не останется ни одного раздела. "
                    "Хотите тишины — /pause.")
        return set_sections(ctx, left)

    if action in ("all", "все", "всё"):
        return set_sections(ctx, sections.known())

    if action in ("reset", "сброс", "default", "умолчание"):
        return set_sections(ctx, [])

    if action in ("only", "только", "set"):
        topics, unknown = sections.parse(rest)
        if unknown or not topics:
            return "Не знаю раздел(ы): %s. Список — /sections" % esc(
                ", ".join(unknown) or "—")
        return set_sections(ctx, topics)

    chosen = set(mine)
    lines = ["🗂 <b>Разделы</b> — в выпуске %d из %d, по %d новости в каждом"
             % (len(mine), len(sections.known()), sections.per_section(ctx.sub)),
             ""]
    for topic in sections.known():
        lines.append("%s %s — <code>%s</code>"
                     % ("✅" if topic in chosen else "▫️", esc(label(topic)),
                        esc(topic)))
    lines.append(SECTION_HELP)
    return "\n".join(lines)


def set_sections(ctx, topics):
    """Меняет список разделов: владельцу — для всех, подписчику — себе."""
    try:
        _key, shown, scope = settings.apply_for(
            ctx.conn, ctx.chat_id, ctx.owner, "sections",
            sections.store(topics) if topics else "по умолчанию")
    except settings.Invalid as exc:
        return "⚠️ %s" % esc(exc)
    ctx.sub = subscribers.get(ctx.conn, ctx.chat_id)
    where = "для всех" if scope == "global" else "лично для вас"
    return ("✅ Разделы (%s): <b>%s</b>\nВ плановом выпуске будет до %d новостей."
            % (where, esc(shown),
               len(ctx.sections()) * sections.per_section(ctx.sub)))


@command("more", "ещё новости, не попавшие в выпуск")
def cmd_more(ctx):
    try:
        count = max(1, min(int(ctx.arg(0, "5")), 15))
    except ValueError:
        count = 5
    rows = take_leftover(ctx.conn, ctx.chat_id, count)
    if not rows:
        return ("Запас пуст. Он наполняется при сборке выпуска — "
                "пришлите /digest или дождитесь планового.")
    lines = ["📎 <b>Ещё из вчерашнего отбора</b>", ""]
    for row in rows:
        lines.append('• <a href="%s">%s</a> — %s · ⭐ %.1f'
                     % (esc(row["url"]),
                        esc(translate.known(ctx.conn, row["title"])),
                        esc(row["source_id"]), row["score"]))
    return "\n".join(lines)


@command("breaking", "проверить, нет ли срочного прямо сейчас", heavy=True)
def cmd_breaking(ctx):
    from . import breaking

    chat_id, sub = ctx.chat_id, ctx.sub
    skip = breaking.why_not(ctx.conn, sub, chat_id)

    def job():
        if not breaking.check(chat_id=chat_id, sub=sub):
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
                     % (esc(row["url"]),
                        esc(translate.known(ctx.conn, row["title"]) or row["url"]),
                        esc(row["source_id"])))
    lines += ["", "<i>/saved clear — очистить</i>"]
    return "\n".join(lines)


@command("taste", "что бот выучил про ваши вкусы")
def cmd_taste(ctx):
    aff = feedback.Affinity.load(ctx.conn, ctx.chat_id)
    total = ctx.conn.execute("SELECT COUNT(*) c FROM feedback WHERE chat_id=?",
                             (ctx.chat_id,)).fetchone()["c"]
    if not total:
        return ("Пока ничего не знаю о ваших вкусах. Под выпуском есть строка "
                "«Оценить новости» — разверните и жмите 👍 или 👎, через неделю "
                "выпуск начнёт подстраиваться.")
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
    sent = conn.execute("SELECT COUNT(*) c FROM sent WHERE chat_id=? AND sent_at > ?",
                        (ctx.chat_id, week)).fetchone()["c"]
    bad = list(conn.execute("SELECT source_id FROM health WHERE fails >= ? ",
                            (CFG["mute_after_fails"],)))
    cost = 0.0
    for row in conn.execute("SELECT stats FROM runs WHERE at > ?", (week,)):
        try:
            cost += float(json.loads(row["stats"]).get("cost", 0))
        except (ValueError, TypeError):
            pass

    mine = ctx.sections()
    feeds = len({f[0] for topic in mine for f in profile(topic)["feeds"]})
    shown = ", ".join(title(topic) for topic in mine[:4])
    lines = [
        "📊 <b>Состояние</b>",
        "Разделов: <b>%d</b> (%s%s) · источников: %d"
        % (len(mine), esc(shown), " …" if len(mine) > 4 else "", feeds),
        "Выпуски: %s · следующий %s — по %d новости на раздел"
        % (esc(subscribers.schedule_human(ctx.sub)), esc(ctx.next_send()),
           sections.per_section(ctx.sub)),
        "Материалов за сутки: %d · отправлено вам за неделю: %d" % (fresh, sent),
        "Расход модели за неделю: $%.4f" % cost,
    ]
    if bad:
        lines.append("Отключённые источники: %s"
                     % esc(", ".join(r["source_id"] for r in bad[:6])))
    if ctx.sub is not None and ctx.sub["paused"]:
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


FEED_HELP = ("Источники раздела <b>%s</b>:\n"
             "/feed list — список\n"
             "/feed add &lt;ссылка&gt; [tier 1-3] [категория]\n"
             "/feed rm &lt;имя&gt;\n\n"
             "Первым словом можно назвать раздел: "
             "<code>/feed медицина add &lt;ссылка&gt;</code>.\n"
             "tier: 1 первоисточник, 2 СМИ, 3 агрегатор.\n"
             "категории: %s")

ACTIONS = ("list", "список", "add", "добавить", "rm", "remove", "del", "убрать")


def topic_args(ctx):
    """Первым словом может идти раздел: /feed космос add &lt;ссылка&gt;."""
    args = list(ctx.args)
    if args and args[0].lower() not in ACTIONS:
        topic = sections.resolve(args[0])
        if topic:
            return topic, args[1:]
    return CFG["topic"], args


@command("feed", "добавить или убрать источник", owner=True)
def cmd_feed(ctx):
    topic, args = topic_args(ctx)
    action = (args[0] if args else "list").lower()

    def arg(index, default=""):
        return args[index] if index < len(args) else default

    if action in ("list", "список"):
        feeds = profile(topic)["feeds"]
        lines = ["📚 <b>%d источников раздела «%s»</b>"
                 % (len(feeds), esc(title(topic))), ""]
        for source_id, url, tier, category in sorted(feeds):
            mark = " ✏️" if userprofiles.is_custom(topic, source_id) else ""
            lines.append("<code>%s</code>%s · t%d · %s"
                         % (esc(source_id), mark, tier, esc(category)))
        lines += ["", "<i>✏️ — добавлено вами. /feed add и /feed rm правят список.</i>"]
        return "\n".join(lines)

    if action in ("add", "добавить"):
        url = arg(1)
        if not url:
            return FEED_HELP % (esc(title(topic)), ", ".join(userprofiles.CATEGORIES))
        try:
            feed = userprofiles.add_feed(topic, url, arg(2, "2"), arg(3, "media"))
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
        return ("✅ Добавил <code>%s</code> (t%d, %s), %s.\n"
                "В разделе «%s» теперь %d источников."
                % (esc(feed[0]), feed[2], esc(feed[3]), note,
                   esc(title(topic)), len(profile(topic)["feeds"])))

    if action in ("rm", "remove", "del", "убрать"):
        source_id = arg(1)
        if not source_id:
            return "Что убрать? /feed rm &lt;имя&gt;, список — /feed list"
        if not userprofiles.remove_feed(topic, source_id):
            return "В разделе «%s» нет источника <code>%s</code>." % (
                esc(title(topic)), esc(source_id))
        return ("🗑 Убрал <code>%s</code>. Осталось %d источников.\n"
                "<i>Вернуть: /feed add со ссылкой.</i>"
                % (esc(source_id), len(profile(topic)["feeds"])))

    return FEED_HELP % (esc(title(topic)), ", ".join(userprofiles.CATEGORIES))


@command("keywords", "ключевые слова для фильтра Hacker News", owner=True)
def cmd_keywords(ctx):
    topic, args = topic_args(ctx)
    action = (args[0] if args else "list").lower()
    if action in ("add", "добавить") and len(args) > 1:
        words = userprofiles.edit_keywords(topic, add=args[1:])
        return "✅ Добавил. Сейчас %d слов:\n<code>%s</code>" % (len(words),
                                                                esc(", ".join(words)))
    if action in ("rm", "remove", "убрать") and len(args) > 1:
        words = userprofiles.edit_keywords(topic, remove=args[1:])
        return "🗑 Убрал. Осталось %d слов:\n<code>%s</code>" % (len(words),
                                                                 esc(", ".join(words)))
    words = profile(topic)["keywords"]
    return ("🔑 <b>Ключевые слова раздела «%s»</b> (%d)\n<code>%s</code>\n\n"
            "<i>Ими фильтруется Hacker News — у нетехнических разделов их нет. "
            "/keywords add слово, /keywords rm слово.</i>"
            % (esc(title(topic)), len(words), esc(", ".join(words)) or "—"))


@command("pause", "приостановить рассылку")
def cmd_pause(ctx):
    subscribers.set_field(ctx.conn, ctx.chat_id, "paused", 1)
    return ("⏸ Ваши выпуски на паузе. Сбор новостей продолжается — "
            "/resume вернёт рассылку.")


@command("resume", "вернуть рассылку")
def cmd_resume(ctx):
    subscribers.set_field(ctx.conn, ctx.chat_id, "paused", 0)
    ctx.sub = subscribers.get(ctx.conn, ctx.chat_id)
    return "▶️ Рассылка включена. Следующий выпуск: %s" % esc(ctx.next_send())


@command("stop", "отписаться от дайджеста")
def cmd_stop(ctx):
    if ctx.owner:
        return ("Вы владелец — отписаться нельзя, но можно поставить на паузу: "
                "/pause.")
    subscribers.remove(ctx.conn, ctx.chat_id)
    return "👋 Отписал. Захотите вернуться — напишите /start."


@command("subs", "подписчики: список, /subs rm <id>", owner=True)
def cmd_subs(ctx):
    action = (ctx.arg(0) or "list").lower()
    if action in ("add", "добавить") and ctx.arg(1):
        sub = subscribers.add(ctx.conn, ctx.arg(1), role="member",
                              title=" ".join(ctx.args[2:]))
        tg_send(sub["chat_id"], "👋 Вас подписали на дайджест. Выпуски будут "
                                "приходить сами: %s."
                                % esc(subscribers.schedule_human()))
        return "✅ Добавил <code>%s</code>." % esc(sub["chat_id"])
    if action in ("rm", "remove", "убрать") and ctx.arg(1):
        try:
            removed = subscribers.remove(ctx.conn, ctx.arg(1))
        except ValueError as exc:
            return "⚠️ %s" % esc(exc)
        return ("🗑 Убрал <code>%s</code>." % esc(ctx.arg(1)) if removed
                else "Такого подписчика нет.")

    rows = subscribers.all_rows(ctx.conn, roles=("owner", "member", "pending"))
    lines = ["👥 <b>Подписчики (%d)</b>" % len(rows), ""]
    for sub in rows:
        mark = {"owner": "👑", "member": "•", "pending": "⏳"}.get(sub["role"], "•")
        pause = " ⏸" if sub["paused"] else ""
        lines.append("%s <code>%s</code> %s%s\n    <i>%s</i>"
                     % (mark, esc(sub["chat_id"]), esc(sub["title"] or "без имени"),
                        pause, esc(subscribers.describe(sub))))
    lines += ["", "<i>Каждый подписчик — отдельные запросы к модели, "
                  "расход растёт пропорционально. /subs add &lt;id&gt;, "
                  "/subs rm &lt;id&gt;.</i>"]
    return "\n".join(lines)


@command("settings", "текущие настройки")
def cmd_settings(ctx):
    personal = settings.personal_view(ctx.sub)
    lines = ["⚙️ <b>Настройки</b> (часовой пояс: %s)" % esc(tz_label()), ""]
    for name, value, describe in settings.overview():
        own = " ✏️" if name in personal else ""
        shown = personal.get(name, value)
        editable = ctx.owner or name in settings.PERSONAL
        lines.append("%s<code>%s</code> = <b>%s</b>%s — %s"
                     % ("" if editable else "🔒 ", esc(name), esc(shown), own,
                        esc(describe)))
    lines += ["", "Менять: <code>/set имя значение</code>, "
                  "например <code>/set time 08:30</code>."]
    if not ctx.owner:
        lines.append("<i>✏️ — ваша личная настройка. 🔒 меняет только владелец.</i>")
    return "\n".join(lines)


@command("set", "изменить настройку: /set время 08:30")
def cmd_set(ctx):
    if not ctx.args:
        return cmd_settings(ctx)
    if len(ctx.args) < 2:
        key, setting = settings.resolve(ctx.arg(0))
        if not setting:
            return "Не знаю настройку «%s». Список — /settings" % esc(ctx.arg(0))
        current = settings.personal_view(ctx.sub).get(key, setting.current())
        return ("<code>%s</code> сейчас <b>%s</b> — %s.\nЗадать: "
                "<code>/set %s значение</code>"
                % (esc(key), esc(current), esc(setting.describe), esc(key)))
    try:
        key, shown, scope = settings.apply_for(
            ctx.conn, ctx.chat_id, ctx.owner, ctx.arg(0), " ".join(ctx.args[1:]))
    except settings.Invalid as exc:
        return "⚠️ %s" % esc(exc)

    ctx.sub = subscribers.get(ctx.conn, ctx.chat_id)
    tail = ""
    if key in ("time", "tz", "times"):
        tail = "\nВыпуски: %s. Следующий выпуск: %s" % (
            esc(subscribers.schedule_human(ctx.sub)), esc(ctx.next_send()))
    if key == "topic":
        topic = sections.resolve(shown) or shown
        tail = ("\nИсточников в разделе: %d. Это раздел для /news без имени и "
                "для срочных; плановый выпуск задаётся командой /sections."
                % len(profile(topic)["feeds"]))
    if key in ("sections", "each"):
        tail = ("\nВ плановом выпуске будет до %d новостей."
                % (len(ctx.sections()) * sections.per_section(ctx.sub)))
    where = "для всех" if scope == "global" else "лично для вас"
    return "✅ <code>%s</code> = <b>%s</b> (%s)%s" % (esc(key), esc(shown),
                                                     where, tail)


# --------------------------------------------------------------------- разбор
def parse_command(text: str):
    """'/more@my_bot 7' -> ('more', ['7']). Не команда -> (None, [])."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, []
    parts = text.split()
    name = parts[0][1:].split("@", 1)[0].lower()
    return (name or None), parts[1:]


#: когда чату в последний раз отвечали расписанием (chat_id -> monotonic).
#: Ответ один и тот же на любую команду, но не чаще раза в ANSWER_EVERY
#: секунд: бот на рассылке не должен превращаться в автоответчик
_ANSWERED = {}
ANSWER_EVERY = 30


def schedule_note(sub=None) -> str:
    """Единственное, что бот говорит в чате: когда придёт следующий выпуск.

    Запретить писать боту Telegram не даёт — окно ввода в чате убрать нельзя.
    Поэтому «закрытый чат» выглядит так: что ни пришли, в ответ одна и та же
    справка о расписании, и ничего в боте от этого не меняется.
    """
    now = subscribers.now_for(sub)
    at = subscribers.next_send_at(sub, now)
    lines = ["🤖 <b>Я только присылаю выпуски.</b> Команд в чате нет.",
             "🗓 Рассылка: %s (%s)" % (esc(subscribers.schedule_human(sub)),
                                       esc(subscribers.tz_of(sub))),
             "⏰ Следующий выпуск: %s, %d %s в %02d:%02d — через %s"
             % ("сегодня" if at.date() == now.date() else "завтра",
                at.day, MONTHS[at.month - 1], at.hour, at.minute,
                esc(subscribers.left_human(sub, now)))]
    if sub is not None and sub["paused"]:
        lines.append("⏸ Сейчас рассылка на паузе.")
    return "\n".join(lines)


def answer_schedule(chat_id) -> None:
    """Ответ на команду: расписание — и больше ничего.

    Молча проглатывать всё нельзя: человек решит, что бот умер. Отвечать на
    каждое сообщение подряд — тоже, поэтому одному чату не чаще раза в
    полминуты. Совсем без ответа — ND_CHAT_REPLY=off.
    """
    if str(CFG["chat_reply"]).lower() == "off":
        return
    now = time.monotonic()
    if now - _ANSWERED.get(chat_id, 0) < ANSWER_EVERY:
        return
    _ANSWERED[chat_id] = now
    conn = db()
    try:
        sub = subscribers.get(conn, chat_id)
    finally:
        conn.close()
    tg_send(chat_id, schedule_note(sub), silent=True)


def handle_message(msg) -> None:
    """Входящее сообщение. Команд бот не выполняет — только знакомится с
    новым чатом, а своему отвечает расписанием и на этом всё."""
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not chat_id or not text:
        return
    user = ((msg.get("from") or {}).get("username")
            or (msg.get("from") or {}).get("first_name") or "")

    if not is_allowed(chat_id):
        log.info("Чат %s (%s) вне подписки: %r", chat_id, user, text[:60])
        try:
            greet_stranger(chat_id, chat.get("title") or chat.get("username")
                           or (msg.get("from") or {}).get("first_name") or "",
                           chat.get("type", "private"))
        except Exception as exc:  # noqa: BLE001 — чужой чат не ломает бота
            log.warning("Не смог ответить чату %s: %s", chat_id, exc)
        return

    name, _args = parse_command(text)
    if name:
        log.debug("Команда /%s из чата %s — в Telegram они выключены", name, chat_id)
        answer_schedule(chat_id)


TOAST = {
    feedback.UP:   "Учёл 👍 — такое буду поднимать выше",
    feedback.DOWN: "Учёл 👎 — такого станет меньше",
}


def handle_signup_callback(cb, chat_id, data) -> None:
    """Владелец решает судьбу заявки: «Пустить» или «Нет»."""
    if not is_owner(chat_id):
        tg_answer_callback(cb.get("id"), "Только владелец решает, кого пускать.")
        return
    _, verdict, applicant = data.split(":", 2)
    conn = db()
    try:
        if verdict == "ok":
            subscribers.add(conn, applicant, role="member")
            tg_answer_callback(cb.get("id"), "Пустил")
            tg_send(applicant, "✅ Владелец одобрил подписку. Выпуски будут "
                               "приходить сами: %s."
                    % esc(subscribers.schedule_human()))
            note = "✅ <code>%s</code> подписан." % esc(applicant)
        else:
            subscribers.remove(conn, applicant)
            tg_answer_callback(cb.get("id"), "Отказал")
            note = "🚫 <code>%s</code> отклонён." % esc(applicant)
    except ValueError as exc:
        tg_answer_callback(cb.get("id"), str(exc))
        return
    finally:
        conn.close()
    message = cb.get("message") or {}
    try:
        tg_edit_markup(chat_id, message.get("message_id"), [])
    except RuntimeError:
        pass
    tg_send(chat_id, note, silent=True)


def handle_fold_callback(cb, chat_id, message, kind) -> None:
    """«Оценить новости» / «Свернуть»: разворачивает и прячет ряды реакций.

    Полная раскладка берётся из копии сообщения: в callback_data все хэши не
    влезут, а копия для веб-страницы хранится и так.
    """
    conn = db()
    try:
        full = outbox_keyboard(conn, chat_id, message.get("message_id"))
        verdicts, saved = feedback.press_state(conn, chat_id)
    finally:
        conn.close()
    if not full:
        tg_answer_callback(cb.get("id"),
                           "Кнопки этого выпуска уже не найти — он старый.")
        return

    keyboard = (expand(full, verdicts, saved) if kind == "more" else collapse(full))
    tg_answer_callback(cb.get("id"))
    try:
        tg_edit_markup(chat_id, message.get("message_id"), keyboard)
    except RuntimeError as exc:
        log.debug("Разметку свернуть/развернуть не удалось: %s", exc)


def handle_callback(cb, worker) -> None:
    """Нажатие кнопки: оценка под карточкой или решение по заявке."""
    message = cb.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    data = cb.get("data") or ""
    if data.startswith("sub:") and data.count(":") >= 2:
        handle_signup_callback(cb, chat_id, data)
        return
    if not is_allowed(chat_id):
        tg_answer_callback(cb.get("id"), "Это личный бот-дайджест.")
        return
    if not data.startswith("fb:") or data.count(":") < 2:
        tg_answer_callback(cb.get("id"))
        return

    _, kind, url_hash = data.split(":", 2)
    if kind in ("more", "less"):
        handle_fold_callback(cb, chat_id, message, kind)
        return
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


def handle_update(upd, worker=None) -> None:
    msg = upd.get("message") or upd.get("channel_post")
    if msg:
        handle_message(msg)
    elif upd.get("callback_query"):
        handle_callback(upd["callback_query"], worker)


# ----------------------------------------------------------------- long-polling
def drain_backlog(conn) -> None:
    """При первом запуске пропускаем то, что накопилось, пока бота не было.

    Иначе после суток простоя посыплются ответы на старые нажатия и заявки.
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
    """Цикл приёма нажатий и заявок. Сетевые сбои гасим нарастающей паузой."""
    backoff = 1
    while not (stop and stop.is_set()):
        try:
            poll_once(worker)
            backoff = 1
        except Exception as exc:  # noqa: BLE001
            log.warning("Приём апдейтов не удался: %s (пауза %ds)", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
