# -*- coding: utf-8 -*-
"""Веб-страница: новостной сайт поверх той же истории, что уходит в Telegram.

Страница только показывает. Управление живёт на самом VPS — там и запускают
`digest.py run`, `collect`, `status` и правят env, — поэтому ни команд, ни
строки ввода, ни истории их запусков здесь нет и быть не должно: с чужого
браузера чужими руками ничего не собирается и не тратится.

Читателей у страницы двое, и видят они разное.

Гость заходит без пароля и видит новостной сайт: лента, разделы, поиск,
популярные источники и темы. Ничего служебного ему не показывают и не
отдают — ни расписания рассылок, ни подписчиков, ни настроек приложения, ни
того, занят ли бот сейчас делом. Менять он тоже ничего не может: POST для
него закрыт весь, кроме входа.

Владелец вводит пароль и получает то же самое плюс служебное: уведомления о
рассылках, подписчиков, значения настроек — всё для чтения — и кнопки
👍/👎/🔖 под карточками: это не команда, а вкусы читателя, и они те же, что
в чате.

Сервер — из стандартной библиотеки, поднимается нитью внутри демона.
Пароль владельца (`ND_WEB_TOKEN`) создаётся сам и лежит в env.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import bot, config, feedback, newsfeed, sections, settings, subscribers
from .config import CFG, ENV_FILE, log, to_local, tz_label, write_env
from .feedparse import parse_date
from .profiles import label, profile
from .storage import db, item_facts, meta_get
from .webpage import PAGE

#: имя cookie с признаком «пароль уже вводили»
COOKIE = "nd_web"
#: сколько живёт вход в браузере
COOKIE_MAX_AGE = 30 * 24 * 3600
#: тело запроса больше этого не читаем — страница шлёт крохи
MAX_BODY = 64 * 1024

#: стили и скрипт у страницы свои, снаружи не грузится ничего. Текст новостей
#: приходит из чужих фидов, и страница вставляет его как текст, а не как
#: разметку, — CSP тут второй рубеж: даже просочившемуся тегу некуда ходить
CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
       "script-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; "
       "base-uri 'none'")

_TOKEN_LOCK = threading.Lock()
#: (сколько раз ошиблись, до какого времени заблокировано) по IP
_FAILS = {}


# --------------------------------------------------------------------- пароль
def token() -> str:
    """Пароль страницы. Если не задан — создаём и сохраняем в env."""
    with _TOKEN_LOCK:
        value = str(CFG.get("web_token") or "").strip()
        if value:
            return value
        value = secrets.token_urlsafe(15)
        CFG["web_token"] = value
        try:
            write_env({"ND_WEB_TOKEN": value})
            log.info("Пароль страницы создан: %s (сохранён в %s)", value, ENV_FILE)
        except OSError as exc:
            log.warning("Пароль страницы не сохранился (%s) — после перезапуска "
                        "он сменится. Задайте ND_WEB_TOKEN руками.", exc)
        return value


def same(given, expected) -> bool:
    """Сравнение без утечки времени. Через байты: пароль бывает и кириллицей."""
    return hmac.compare_digest(str(given).encode("utf-8"),
                               str(expected).encode("utf-8"))


def cookie_value(secret: str) -> str:
    """Значение cookie: от пароля, но сам пароль в браузере не лежит."""
    return hmac.new(secret.encode("utf-8"), b"nd-web-session",
                    hashlib.sha256).hexdigest()


# --------------------------------------------------------------------- данные
def chat_id() -> str:
    """От чьего имени работает страница. Обычно — владелец бота."""
    return str(config.TG_CHAT or "web")


def collected_at() -> str:
    """Когда последний раз читали источники — «Обновлено в 18:27» в шапке."""
    conn = db()
    try:
        return clock(meta_get(conn, "last_collect", ""))
    finally:
        conn.close()


def public_state() -> dict:
    """Состояние для гостя: свежесть ленты и ничего больше.

    Всё остальное из `state` — расписание выпусков, число источников, пауза,
    занятость бота, чат владельца — это про сервис, а не про новости, и
    гостю не отдаётся даже полем в JSON: чего нет в ответе, того не увидят
    ни на странице, ни в консоли браузера.
    """
    return {"admin": False, "collected": collected_at()}


def state(worker) -> dict:
    conn = db()
    try:
        subscribers.ensure_owner(conn)
        sub = subscribers.get(conn, chat_id())
        collected = meta_get(conn, "last_collect", "")
    finally:
        conn.close()
    mine = sections.plan(sub)
    return {
        "sections": [{"id": topic, "label": label(topic)} for topic in mine],
        "each": sections.per_section(sub),
        "feeds": len({f[0] for topic in mine for f in profile(topic)["feeds"]}),
        "next": subscribers.next_send_human(sub),
        "collected": clock(collected),
        "tz": tz_label(),
        "paused": bool(sub["paused"]) if sub is not None else False,
        "busy": worker.busy() if worker is not None else "",
        "owner": bool(config.TG_CHAT),
        "chat": chat_id(),
        "admin": True,
    }


press_state = feedback.press_state


def readers(conn) -> list:
    """Подписчики: кто получает выпуск и чем его настройки отличаются от общих."""
    rows = subscribers.all_rows(conn, roles=("owner", "member", "pending"))
    return [{"chat": row["chat_id"], "title": row["title"] or "без имени",
             "role": row["role"], "paused": bool(row["paused"]),
             "own": subscribers.describe(row)} for row in rows]


def tuning(sub) -> list:
    """Настройки приложения с текущими значениями — для показа, не для правки.

    Меняются они на VPS: `ND_*` в ~/.newsdigest/env. Страница про них просто
    рассказывает — чтобы посмотреть, чем сейчас живёт бот, не заходя на сервер.
    """
    personal = settings.personal_view(sub)
    return [{"name": name, "value": str(personal.get(name, value)),
             "about": about, "own": name in personal}
            for name, value, about in settings.overview()]


def clock(iso: str) -> str:
    """Только часы и минуты — «Обновлено в 18:27» в шапке ленты."""
    at = parse_date(iso)
    return to_local(at).strftime("%H:%M") if at else ""


def to_int(raw, default=0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def news(query, worker=None, admin=True) -> dict:
    """Лента новостей: карточки, а вместе с первой страницей — панели вокруг.

    Меню разделов, популярные источники и темы считаются только на первой
    странице: при нажатии «Показать ещё» они не меняются, а лишние запросы к
    базе на каждую подгрузку не нужны.

    Фильтры (`sections`) — это набор разделов, закреплённый читателем на
    странице: «только наука и спорт». Открытый раздел (`section`) их
    перебивает: раз уж читатель зашёл в «Космос», показываем «Космос».

    Гостю достаётся только общая лента: «Сохранённые» и «Избранное» — это
    отметки владельца, и карточки к нему приходят без них.
    """
    chat = chat_id()
    view = str((query.get("view") or ["news"])[0])
    if view not in newsfeed.VIEWS or not admin:
        view = "news"
    section = sections.resolve((query.get("section") or [""])[0])
    picked, _unknown = sections.parse((query.get("sections") or [""])[0])
    picked = picked[:sections.MAX_SECTIONS]
    search = str((query.get("q") or [""])[0])[:120]
    offset = max(to_int((query.get("offset") or ["0"])[0], 0), 0)

    conn = db()
    try:
        if admin:
            subscribers.ensure_owner(conn)
        sub = subscribers.get(conn, chat)
        rows, more = newsfeed.page(conn, chat, view, section or picked,
                                   search, offset)
        verdicts, saved = press_state(conn, chat) if admin else ({}, set())
        payload = {"view": view, "section": section, "sections": picked,
                   "q": search, "offset": offset, "more": more,
                   "items": newsfeed.cards(conn, rows, verdicts, saved, chat)}
        if not offset:
            payload["side"] = {
                "menu": newsfeed.menu(conn, chat, sections.plan(sub)),
                "sources": newsfeed.sources(conn, chat),
                "topics": newsfeed.topics(conn, chat),
            }
    finally:
        conn.close()
    if not offset:
        payload["state"] = state(worker) if admin else public_state()
    return payload


def alerts(worker=None, admin=True) -> dict:
    """Уведомления: сводка последних рассылок плюс состояние бота.

    Этим же запросом страница узнаёт при заходе, кто пришёл, и раз в
    несколько секунд — не появилось ли чего нового. Гостю рассылки не
    положены (кому и когда уходит выпуск — дело служебное), поэтому список у
    него пуст, а «не пришло ли нового» решается по самой свежей новости —
    она и так первой лежит в его ленте.
    """
    chat = chat_id()
    if not admin:
        conn = db()
        try:
            fresh = newsfeed.latest(conn, chat)
        finally:
            conn.close()
        return {"alerts": [], "last": fresh, "state": public_state()}
    conn = db()
    try:
        subscribers.ensure_owner(conn)
        mail = newsfeed.mailings(conn, chat)
    finally:
        conn.close()
    return {"alerts": mail, "last": mail[0]["id"] if mail else "",
            "state": state(worker)}


def tools(worker=None) -> dict:
    """Раздел «Настройки»: подписчики и настройки приложения, всё для чтения."""
    chat = chat_id()
    conn = db()
    try:
        subscribers.ensure_owner(conn)
        sub = subscribers.get(conn, chat)
        people = readers(conn)
    finally:
        conn.close()
    return {"readers": people, "settings": tuning(sub), "tz": tz_label(),
            "state": state(worker)}


# ------------------------------------------------------------------- действия
def press(data: str) -> dict:
    """Нажатие 👍/👎/🔖 под карточкой. Возвращает подсказку и новое состояние.

    Единственное, что страница вправе изменить: это вкусы читателя, а не
    управление ботом.
    """
    chat = chat_id()
    data = str(data or "")
    if not data.startswith("fb:") or data.count(":") < 2:
        return {"toast": ""}

    _, kind, url_hash = data.split(":", 2)
    conn = db()
    try:
        facts = item_facts(conn, url_hash)
        if kind in (feedback.UP, feedback.DOWN):
            feedback.record(conn, chat, url_hash, kind, facts)
            toast = bot.TOAST[kind]
        elif kind == "save":
            toast = ("🔖 В закладках" if feedback.save_bookmark(
                conn, chat, url_hash, facts) else "Убрал из закладок")
        else:
            return {"toast": ""}
        verdicts, saved = press_state(conn, chat)
    finally:
        conn.close()
    return {"toast": toast, "hash": url_hash,
            "pressed": {"up": verdicts.get(url_hash) == feedback.UP,
                        "down": verdicts.get(url_hash) == feedback.DOWN,
                        "save": url_hash in saved}}


# ---------------------------------------------------------------------- HTTP
class Site(BaseHTTPRequestHandler):
    server_version = "newsdigest"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- ответы
    def _reply(self, code, body, ctype="application/json; charset=utf-8",
               headers=()):
        if not isinstance(body, bytes):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200, headers=()):
        self._reply(code, json.dumps(data, ensure_ascii=False), headers=headers)

    def log_message(self, fmt, *args):          # noqa: A003 — переопределяем
        log.debug("web %s: %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------- служебное
    def _split(self):
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path.rstrip("/") or "/", urllib.parse.parse_qs(parsed.query)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not 0 < length <= MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _authed(self) -> bool:
        expected = cookie_value(token())
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and same(value, expected):
                return True
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.startswith("Bearer "):
            return same(auth[7:].strip(), token())
        return False

    # ---------------------------------------------------------------- вход
    def _login(self, data):
        ip = self.client_address[0]
        fails, until = _FAILS.get(ip, (0, 0.0))
        if time.time() < until:
            self._json({"error": "слишком много попыток, подождите минуту"}, 429)
            return
        if same(data.get("token") or "", token()):
            _FAILS.pop(ip, None)
            self._json({"ok": True}, headers=[(
                "Set-Cookie",
                "%s=%s; Max-Age=%d; Path=/; HttpOnly; SameSite=Lax"
                % (COOKIE, cookie_value(token()), COOKIE_MAX_AGE))])
            return
        time.sleep(1)          # подбор пароля со скоростью раз в секунду
        fails += 1
        _FAILS[ip] = (fails, time.time() + 60 if fails >= 5 else 0.0)
        log.warning("Неверный пароль страницы с %s (попытка %d)", ip, fails)
        self._json({"error": "неверный пароль"}, 403)

    # -------------------------------------------------------------- маршруты
    def do_GET(self):                            # noqa: N802 — имя от базы
        try:
            path, query = self._split()
            if path == "/":
                self._reply(200, PAGE, "text/html; charset=utf-8",
                            headers=[("Content-Security-Policy", CSP)])
                return
            if path == "/favicon.ico":
                self._reply(204, b"", "image/svg+xml")
                return
            admin = self._authed()
            # лента открыта всем: это и есть сайт. Служебное — только по паролю
            if path == "/api/alerts":
                self._json(alerts(self.server.worker, admin))
                return
            if path == "/api/news":
                self._json(news(query, self.server.worker, admin))
                return
            if not admin:
                self._json({"error": "нужен пароль"}, 401)
                return
            if path == "/api/tools":
                self._json(tools(self.server.worker))
                return
            self._json({"error": "нет такой страницы"}, 404)
        except Exception as exc:  # noqa: BLE001 — сервер не должен падать
            log.exception("Запрос %s не обработан: %s", self.path, exc)
            self._json({"error": "внутренняя ошибка"}, 500)

    def do_POST(self):                           # noqa: N802
        try:
            path, _query = self._split()
            data = self._body()
            if path == "/api/login":
                self._login(data)
                return
            # менять на странице что-либо вправе только владелец: гость её
            # читает, а его POST не доходит ни до базы, ни до бота
            if not self._authed():
                self._json({"error": "нужен пароль"}, 401)
                return
            if path == "/api/logout":
                self._json({"ok": True}, headers=[(
                    "Set-Cookie",
                    "%s=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax" % COOKIE)])
                return
            if path == "/api/react":
                self._json(press(data.get("data", "")))
                return
            self._json({"error": "нет такой страницы"}, 404)
        except Exception as exc:  # noqa: BLE001
            log.exception("Запрос %s не обработан: %s", self.path, exc)
            self._json({"error": "внутренняя ошибка"}, 500)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, worker=None):
        self.worker = worker
        ThreadingHTTPServer.__init__(self, address, Site)


def build(worker=None, host=None, port=None) -> Server:
    host = str(host or CFG["web_host"])
    port = int(port if port else CFG["web_port"])
    server = Server((host, port), worker)
    token()                     # создаётся заранее: пусть пароль будет в логе
    return server


def announce(server) -> None:
    host, port = server.server_address[0], server.server_address[1]
    shown = "localhost" if host in ("127.0.0.1", "::1") else "<ip-вашего-vps>"
    log.info("Страница открыта: http://%s:%d/ — новости видны всем без пароля, "
             "служебное только владельцу: пароль в %s (ND_WEB_TOKEN)",
             shown, port, ENV_FILE)


def start_background(worker=None):
    """Поднимает страницу нитью внутри демона. Не смогли — демон живёт дальше."""
    try:
        server = build(worker)
    except OSError as exc:
        log.warning("Страницу поднять не удалось (%s:%s): %s. Демон работает "
                    "без неё — освободите порт или задайте ND_WEB_PORT.",
                    CFG["web_host"], CFG["web_port"], exc)
        return None
    threading.Thread(target=server.serve_forever, name="nd-web",
                     daemon=True).start()
    announce(server)
    return server


def serve(worker=None, host=None, port=None) -> None:
    """Отдельный процесс со страницей — когда демон крутится сам по себе."""
    server = build(worker, host, port)
    announce(server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Страница остановлена по Ctrl+C")
    finally:
        server.server_close()
