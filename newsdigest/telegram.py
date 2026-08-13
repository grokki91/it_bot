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


def tg_send(chat_id, text, keyboard=None, silent=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": not CFG["link_preview"],
               "disable_notification": bool(CFG["silent"] if silent is None else silent)}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        return tg_call("sendMessage", payload)
    except RuntimeError as exc:
        message = str(exc).lower()
        if "parse" not in message and "entit" not in message and "tag" not in message:
            raise
        log.warning("HTML не принят (%s), повторяю простым текстом", exc)
        payload.pop("parse_mode")
        payload["text"] = plain(text)[:TG_LIMIT]
        return tg_call("sendMessage", payload)


def plain(text: str) -> str:
    """HTML-разметка → обычный текст (для терминала и аварийной отправки)."""
    return html_mod.unescape(re.sub(r"<[^>]+>", "", text))


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
