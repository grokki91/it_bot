# -*- coding: utf-8 -*-
"""Транспорт Telegram Bot API: отправка, ретраи, определение chat_id."""
from __future__ import annotations

import html as html_mod
import re
import time

from . import config
from .config import CFG, log
from .net import post_json

TG_LIMIT = 4096


def tg_call(method, payload, attempts=4, timeout=30):
    if not config.TG_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
    url = "https://api.telegram.org/bot%s/%s" % (config.TG_TOKEN, method)
    last = ""
    for attempt in range(1, attempts + 1):
        status, data, err = post_json(url, payload, timeout=timeout)
        if data and data.get("ok"):
            return data["result"]
        code = (data or {}).get("error_code", status)
        desc = (data or {}).get("description", err)
        last = "%s: %s" % (code, desc)
        if code == 429:
            wait = float(((data or {}).get("parameters") or {}).get("retry_after", 5))
            log.warning("Telegram 429, ждём %.0fs", wait)
            time.sleep(wait + 0.5)
            continue
        if code and 400 <= int(code) < 500:
            raise RuntimeError("Telegram отклонил запрос: %s" % last)
        if attempt < attempts:
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError("Telegram недоступен: %s" % last)


def mirror(chat_id, text, keyboard=None, kind="bot"):
    """Копия сообщения в базу — из неё читает веб-страница.

    Делается ДО отправки: страница задумана как замена Telegram, и выпуск
    должен быть виден в браузере, даже если Bot API сейчас недоступен.
    Сбой записи не должен мешать отправке, поэтому глушим всё.

    Хранится всегда ПОЛНАЯ раскладка кнопок, даже если в чат ушла свёрнутая:
    из неё потом собирается и развёрнутый вид, и кнопки на странице.
    """
    try:
        from .storage import db, save_outbox
        conn = db()
        try:
            return save_outbox(conn, chat_id, text, keyboard, kind)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — зеркало не важнее отправки
        log.debug("Копия сообщения не сохранилась: %s", exc)
        return 0


def remember_message(row_id, result) -> None:
    """Связывает копию с номером сообщения в Telegram (для «развернуть»)."""
    message_id = (result or {}).get("message_id")
    if not row_id or not message_id:
        return
    try:
        from .storage import db, link_outbox
        conn = db()
        try:
            link_outbox(conn, row_id, message_id)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — связка не важнее отправки
        log.debug("Номер сообщения не сохранился: %s", exc)


def tg_send(chat_id, text, keyboard=None, silent=None):
    from .render import for_delivery      # render импортирует нас — только здесь

    row_id = mirror(chat_id, text, keyboard)
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": not CFG["link_preview"],
               "disable_notification": bool(CFG["silent"] if silent is None else silent)}
    shown = for_delivery(keyboard)
    if shown:
        payload["reply_markup"] = {"inline_keyboard": shown}
    try:
        result = tg_call("sendMessage", payload)
    except RuntimeError as exc:
        message = str(exc).lower()
        if "parse" not in message and "entit" not in message and "tag" not in message:
            raise
        log.warning("HTML не принят (%s), повторяю простым текстом", exc)
        payload.pop("parse_mode")
        payload["text"] = plain(text)[:TG_LIMIT]
        result = tg_call("sendMessage", payload)
    remember_message(row_id, result)
    return result


def plain(text: str) -> str:
    """HTML-разметка → обычный текст (для терминала и аварийной отправки)."""
    return html_mod.unescape(re.sub(r"<[^>]+>", "", text))


def tg_answer_callback(callback_id, text="", alert=False):
    """Гасит «часики» на кнопке и показывает всплывающую подсказку."""
    payload = {"callback_query_id": callback_id, "show_alert": bool(alert)}
    if text:
        payload["text"] = text[:200]
    try:
        return tg_call("answerCallbackQuery", payload, attempts=1)
    except RuntimeError as exc:
        # подтверждение живёт ~15 секунд: опоздали — не беда, ответ уже отправлен
        log.debug("answerCallbackQuery: %s", exc)
        return None


def tg_edit_markup(chat_id, message_id, keyboard):
    try:
        return tg_call("editMessageReplyMarkup",
                       {"chat_id": chat_id, "message_id": message_id,
                        "reply_markup": {"inline_keyboard": keyboard}}, attempts=2)
    except RuntimeError as exc:
        if "not modified" in str(exc).lower():
            return None
        raise


def tg_detect_chat():
    """Достаёт chat_id из последних апдейтов — чтобы не искать его вручную."""
    updates = tg_call("getUpdates", {"limit": 20, "timeout": 0})
    for upd in reversed(updates):
        for key in ("message", "channel_post", "edited_message", "my_chat_member"):
            obj = upd.get(key) or {}
            chat = obj.get("chat") or {}
            if chat.get("id"):
                title = chat.get("title") or chat.get("username") or chat.get("first_name")
                return str(chat["id"]), (title or ""), chat.get("type", "")
    return None, "", ""
