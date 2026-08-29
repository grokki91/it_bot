# -*- coding: utf-8 -*-
"""Лента в RSS: та же история, но для чужой читалки.

Страница показывает ленту так, как задумано; читалка — так, как привык её
хозяин. Ни то ни другое не лучше: человек, у которого уже открыт Feedly или
NetNewsWire, не станет держать ради одного сайта отдельную вкладку. Поэтому
`/rss` отдаёт ровно то же, что видно на странице гостю, и ничего сверх того.

Что важно и чего здесь нарочно нет:

* **К модели не ходим.** Лента на странице умеет доводить английскую карточку
  до языка выпуска на лету (`newsfeed.russify`) — это стоит денег и времени.
  Читалку опрашивают роботы, раз в несколько минут и без человека по ту
  сторону; платить за перевод по каждому такому заходу нельзя. Берём то, что
  уже осело в истории, — как есть.
* **Служебного не отдаём.** RSS открыт без пароля, как и сама лента, поэтому
  сюда идут только новости: ни подписчиков, ни настроек, ни отметок владельца
  здесь нет и быть не может.
* **Ссылка ведёт к издателю** и проходит ту же проверку, что на странице
  (`safety.outward`): читалка откроет её так же, как браузер.

Раздел и поиск берутся из адреса: `/rss?section=космос` — только космос,
`/rss?q=иран` — только про Иран. Так подписываются на один раздел, не получая
остальных.
"""
from __future__ import annotations

import re
from email.utils import format_datetime
from xml.sax.saxutils import escape, quoteattr

from . import newsfeed, sections
from .config import CFG
from .feedparse import clean_title, parse_date
from .profiles import title as topic_title

#: сколько новостей отдаём читалке. Больше сотни не нужно никому: читалка
#: помнит прочитанное сама, а первый заход всё равно показывает только хвост
MAX = 50

#: символы, которых в XML не бывает вовсе. В заголовок из чужого фида
#: попадает всякое, а один такой символ делает нечитаемой всю ленту
FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")

#: имя хоста из заголовка Host. Он приходит от клиента, то есть ему нельзя
#: верить: в ленту он попадает только целиком совпав с этим образцом
HOST = re.compile(r"^[A-Za-z0-9.\-]{1,253}(:[0-9]{1,5})?$")


def clean(text) -> str:
    """Текст, пригодный для XML: без управляющих символов и с экранированием."""
    return escape(FORBIDDEN.sub("", str(text or "")))


def origin(host: str, secure=False) -> str:
    """Адрес самой страницы — для ссылки на неё из ленты.

    Хост берётся из запроса: своего внешнего адреса бот не знает, его меняют
    и туннелем, и обратным прокси. Не похоже на хост — обходимся без ссылки:
    подставить в ленту чужую строку было бы хуже, чем не подставить ничего.
    """
    host = str(host or "").strip()
    if not HOST.match(host):
        return ""
    return "%s://%s" % ("https" if secure else "http", host)


def stamp(iso: str) -> str:
    """Время новости по RFC 822 — так его ждёт RSS."""
    at = parse_date(iso)
    return format_datetime(at) if at is not None else ""


def entry(row, smap) -> str:
    """Одна новость лентой. Ссылка — на издателя, guid — наш, он не меняется."""
    topic = row["section"] or smap.get(row["source_id"], "")
    url = newsfeed.outward(newsfeed.column(row, "url"))
    title = clean_title(str(newsfeed.column(row, "headline")
                            or newsfeed.column(row, "title")))
    out = ["    <item>",
           "      <title>%s</title>" % clean(title),
           "      <description>%s</description>" % clean(newsfeed.body(row)),
           "      <guid isPermaLink=\"false\">%s</guid>" % clean(row["url_hash"])]
    if url:
        out.append("      <link>%s</link>" % clean(url))
    if topic:
        out.append("      <category>%s</category>" % clean(topic_title(topic)))
    when = stamp(newsfeed.column(row, "at") or newsfeed.column(row, "sent_at"))
    if when:
        out.append("      <pubDate>%s</pubDate>" % clean(when))
    out.append("    </item>")
    return "\n".join(out)


def feed(conn, chat_id, section="", query="", host="", secure=False,
         limit=MAX) -> str:
    """Вся лента одним документом RSS 2.0."""
    rows, _more = newsfeed.page(conn, chat_id, "news", section, query, 0, limit)
    smap = sections.source_map()

    name = "Дайджест"
    if section:
        name += " — " + topic_title(section)
    if query:
        name += " — поиск «%s»" % query
    site = origin(host, secure)

    head = ["<?xml version=\"1.0\" encoding=\"utf-8\"?>",
            "<rss version=\"2.0\" xmlns:atom=\"http://www.w3.org/2005/Atom\">",
            "  <channel>",
            "    <title>%s</title>" % clean(name),
            "    <description>%s</description>"
            % clean("Новости, отобранные ботом и уже пришедшие в выпуске"),
            "    <language>%s</language>"
            % clean("ru" if CFG["language"].startswith("рус") else "en")]
    if site:
        head.append("    <link>%s</link>" % clean(site))
        head.append("    <atom:link href=%s rel=\"self\" "
                    "type=\"application/rss+xml\"/>"
                    % quoteattr(FORBIDDEN.sub("", site + "/rss")))

    body = [entry(row, smap) for row in rows]
    return "\n".join(head + body + ["  </channel>", "</rss>", ""])
