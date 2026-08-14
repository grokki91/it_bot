# -*- coding: utf-8 -*-
"""Веб-страница: то же, что бот в Telegram, только в браузере.

Смысл простой — читать выпуск и гонять команды, не открывая Telegram.
Страница не повторяет логику бота: она зовёт те же обработчики из `bot.py`
и показывает копии сообщений, которые транспорт кладёт в таблицу `outbox`.
Что видно в чате, то видно и здесь — включая кнопки 👍/👎/🔖 и заявки на
подписку.

Сервер — из стандартной библиотеки, поднимается нитью внутри демона.
Страница закрыта паролем: она слушает IP VPS, а за ней и баланс модели,
и ваша переписка. Пароль (`ND_WEB_TOKEN`) создаётся сам и лежит в env.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import bot, config, feedback, sections, subscribers
from .config import CFG, ENV_FILE, log, to_local, tz_label, write_env
from .feedparse import parse_date
from .profiles import label, profile
from .render import esc
from .storage import db, item_facts, outbox_page, save_outbox
from .telegram import tg_send
from .webpage import PAGE

#: имя cookie с признаком «пароль уже вводили»
COOKIE = "nd_web"
#: сколько живёт вход в браузере
COOKIE_MAX_AGE = 30 * 24 * 3600
#: тело запроса больше этого не читаем — страница шлёт крохи
MAX_BODY = 64 * 1024

#: стили и скрипт у страницы свои, снаружи не грузится ничего. Второй рубеж
#: после санитайзера: даже если в текст просочится чужой тег, ходить ему некуда
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


# ------------------------------------------------------------------ безопасный HTML
#: теги, которые допускает Telegram, — их же понимает и страница
ALLOWED = {"b", "strong", "i", "em", "u", "s", "code", "pre", "br"}

_TAG = re.compile(r"<(/?)\s*([a-zA-Z]+)([^>]*)>")
_HREF = re.compile(r"""href\s*=\s*(['"])(.*?)\1""", re.S)


def safe_html(text: str) -> str:
    """Телеграмная разметка → HTML, которому можно доверять.

    Текст внутри сообщений уже экранирован (`render.esc`), поэтому чистим
    только сами теги: незнакомый тег превращается в текст, у ссылки
    остаётся один href, и только http(s).
    """
    out, pos = [], 0
    for match in _TAG.finditer(text or ""):
        out.append(text[pos:match.start()])
        pos = match.end()
        closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3)
        if name in ALLOWED:
            out.append("<%s%s>" % (closing, name))
        elif name == "a":
            out.append("</a>" if closing else _link(attrs))
        else:
            out.append(esc(match.group(0)))
    out.append(text[pos:] if text else "")
    return "".join(out)


def _link(attrs: str) -> str:
    """Из атрибутов ссылки оставляем только href, и только http(s).

    Кавычки экранируем сами: `render.esc` их не трогает (в тексте они не
    мешают), а вот внутри href кавычка вырвалась бы из атрибута.
    """
    href = _HREF.search(attrs or "")
    url = href.group(2) if href else ""
    if not url.lower().startswith(("http://", "https://")):
        return "<a>"
    return ('<a href="%s" target="_blank" rel="noopener noreferrer">'
            % esc(url).replace('"', "&quot;").replace("'", "&#39;"))


# --------------------------------------------------------------------- данные
def chat_id() -> str:
    """От чьего имени работает страница. Обычно — владелец бота."""
    return str(config.TG_CHAT or "web")


def state(worker) -> dict:
    conn = db()
    try:
        subscribers.ensure_owner(conn)
        sub = subscribers.get(conn, chat_id())
    finally:
        conn.close()
    mine = sections.plan(sub)
    return {
        "sections": [{"id": topic, "label": label(topic)} for topic in mine],
        "each": sections.per_section(sub),
        "feeds": len({f[0] for topic in mine for f in profile(topic)["feeds"]}),
        "next": subscribers.next_send_human(sub),
        "tz": tz_label(),
        "paused": bool(sub["paused"]) if sub is not None else False,
        "busy": worker.busy() if worker is not None else "",
        "owner": bool(config.TG_CHAT),
        "chat": chat_id(),
    }


def commands() -> list:
    """Команды для панели быстрых кнопок — тот же список, что в /help."""
    owner = bot.is_owner(chat_id())
    out = []
    for name, cmd in sorted(bot.HANDLERS.items()):
        if cmd.hidden or not cmd.help or (cmd.owner and not owner):
            continue
        out.append({"name": name, "help": cmd.help, "heavy": cmd.heavy})
    return out


def press_state(conn, chat):
    verdicts = {r["url_hash"]: r["verdict"] for r in conn.execute(
        "SELECT url_hash, verdict FROM feedback WHERE chat_id=?", (chat,))}
    saved = {r["url_hash"] for r in conn.execute(
        "SELECT url_hash FROM saved WHERE chat_id=?", (chat,))}
    return verdicts, saved


def is_pressed(data, verdicts, saved) -> bool:
    parts = str(data or "").split(":")
    if len(parts) < 3 or parts[0] != "fb":
        return False
    kind, url_hash = parts[1], parts[2]
    if kind == "save":
        return url_hash in saved
    return verdicts.get(url_hash) == kind


def buttons(raw, verdicts, saved) -> list:
    try:
        keyboard = json.loads(raw) if raw else []
    except ValueError:
        return []
    rows = []
    for line in keyboard:
        row = [{"text": str(b.get("text") or "").replace("✓", ""),
                "data": str(b.get("callback_data") or ""),
                "pressed": is_pressed(b.get("callback_data"), verdicts, saved)}
               for b in (line or []) if isinstance(b, dict)]
        if row:
            rows.append(row)
    return rows


def when(iso: str) -> str:
    at = parse_date(iso)
    return to_local(at).strftime("%d.%m %H:%M") if at else ""


def feed(after=None, worker=None, toast="") -> dict:
    """Лента сообщений: хвост при первом заходе, дальше — только новое."""
    chat = chat_id()
    conn = db()
    try:
        rows = outbox_page(conn, chat, after)
        verdicts, saved = press_state(conn, chat)
    finally:
        conn.close()
    messages = [{"id": r["id"], "kind": r["kind"], "at": when(r["at"]),
                 "html": safe_html(r["text"]),
                 "buttons": buttons(r["keyboard"], verdicts, saved)} for r in rows]
    last = messages[-1]["id"] if messages else (int(after or 0))
    return {"messages": messages, "last": last, "toast": toast,
            "state": state(worker), "commands": commands()}


# ------------------------------------------------------------------- действия
def note(chat, text, keyboard=None, kind="bot") -> None:
    """Сообщение только для страницы: в Telegram его дублировать незачем."""
    conn = db()
    try:
        save_outbox(conn, chat, text, keyboard, kind)
    finally:
        conn.close()


def run_command(text, worker) -> None:
    """Выполняет команду от имени владельца — тем же кодом, что и в чате."""
    chat = chat_id()
    raw = (text or "").strip()
    if not raw:
        return
    if not raw.startswith("/"):
        raw = "/" + raw                 # с телефона слэш набирать неудобно
    note(chat, esc(raw), kind="me")

    name, args = bot.parse_command(raw)
    cmd = bot.HANDLERS.get(name) if name else None
    if cmd is None:
        note(chat, "Не знаю команду /%s. Список: /help" % esc(name or ""))
        return
    if cmd.owner and not bot.is_owner(chat):
        note(chat, "Команда /%s только для владельца. Задайте TELEGRAM_CHAT_ID "
                   "(<code>digest.py chatid</code>) — страница работает от его "
                   "имени." % esc(name))
        return
    if cmd.heavy and worker is None:
        note(chat, "Фоновые задачи недоступны: страница поднята без демона.")
        return

    conn = db()
    try:
        reply = cmd.fn(bot.Ctx(chat, args, conn, worker, user="web"))
    except Exception as exc:  # noqa: BLE001 — кривая команда не роняет сервер
        log.exception("Команда %s со страницы упала: %s", raw, exc)
        reply = "⚠️ Не получилось: %s" % esc(str(exc)[:300])
    finally:
        conn.close()
    if reply:
        note(chat, reply)


def press(data: str) -> dict:
    """Нажатие кнопки под сообщением. Возвращает подсказку и новое состояние."""
    chat = chat_id()
    data = str(data or "")
    if data.startswith("sub:") and data.count(":") >= 2:
        return {"toast": signup(data, chat)}
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
            toast = ("🔖 В закладках, /saved"
                     if feedback.save_bookmark(conn, chat, url_hash, facts)
                     else "Убрал из закладок")
        else:
            return {"toast": ""}
        verdicts, saved = press_state(conn, chat)
    finally:
        conn.close()
    return {"toast": toast, "hash": url_hash,
            "pressed": {"up": verdicts.get(url_hash) == feedback.UP,
                        "down": verdicts.get(url_hash) == feedback.DOWN,
                        "save": url_hash in saved}}


def signup(data, chat) -> str:
    """Кнопки «Пустить»/«Нет» под заявкой нового чата — они же и на странице."""
    if not bot.is_owner(chat):
        return "Только владелец решает, кого пускать."
    _, verdict, applicant = data.split(":", 2)
    conn = db()
    try:
        if verdict == "ok":
            subscribers.add(conn, applicant, role="member")
            tg_send(applicant, "✅ Владелец одобрил подписку. Справка: /help")
            return "Пустил %s" % applicant
        subscribers.remove(conn, applicant)
        return "Отказал %s" % applicant
    except ValueError as exc:
        return str(exc)
    except RuntimeError as exc:                 # Telegram не ответил — не беда
        log.warning("Одобрение %s: %s", applicant, exc)
        return "Подписал, но уведомить не вышло: %s" % exc
    finally:
        conn.close()


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

    def _after(self, query):
        raw = (query.get("after") or [""])[0]
        try:
            return int(raw)
        except ValueError:
            return None

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
            if not self._authed():
                self._json({"error": "нужен пароль"}, 401)
                return
            if path == "/api/feed":
                self._json(feed(self._after(query), self.server.worker))
                return
            self._json({"error": "нет такой страницы"}, 404)
        except Exception as exc:  # noqa: BLE001 — сервер не должен падать
            log.exception("Запрос %s не обработан: %s", self.path, exc)
            self._json({"error": "внутренняя ошибка"}, 500)

    def do_POST(self):                           # noqa: N802
        try:
            path, query = self._split()
            data = self._body()
            if path == "/api/login":
                self._login(data)
                return
            if not self._authed():
                self._json({"error": "нужен пароль"}, 401)
                return
            if path == "/api/logout":
                self._json({"ok": True}, headers=[(
                    "Set-Cookie",
                    "%s=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax" % COOKIE)])
                return

            after = self._after(query)
            if after is None:
                try:
                    after = int(data.get("after"))
                except (TypeError, ValueError):
                    after = None
            worker = self.server.worker

            if path == "/api/command":
                run_command(data.get("text", ""), worker)
                self._json(feed(after, worker))
                return
            if path == "/api/react":
                result = press(data.get("data", ""))
                payload = feed(after, worker, toast=result.get("toast", ""))
                payload["press"] = result
                self._json(payload)
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
    log.info("Страница открыта: http://%s:%d/ — пароль в %s (ND_WEB_TOKEN)",
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
