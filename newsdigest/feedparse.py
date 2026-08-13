# -*- coding: utf-8 -*-
"""Разбор RSS 2.0 / RSS 1.0 / Atom одним кодом и разбор дат."""
from __future__ import annotations

import html as html_mod
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def _tagname(tag) -> str:
    return tag.split("}")[-1].lower() if isinstance(tag, str) else ""


def _text(el) -> str:
    return "".join(el.itertext())


def strip_html(raw: str, limit: int = 1000) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:                                    # RFC 822: "Mon, 06 Sep 2021 12:00:00 GMT"
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    text = value.replace("Z", "+00:00")      # ISO 8601
    text = re.sub(r"\.\d+", "", text)
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed(raw: bytes) -> list:
    """RSS 2.0 / RSS 1.0 / Atom одним кодом: сравниваем локальные имена тегов."""
    start = raw.find(b"<")
    if start > 0:
        raw = raw[start:]
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)
        root = ET.fromstring(cleaned)       # если снова упадёт — обработает вызывающий

    out = []
    for el in root.iter():
        if _tagname(el.tag) not in ("item", "entry"):
            continue
        title = link = summary = ""
        published = None
        for child in el:
            name = _tagname(child.tag)
            if name == "title" and not title:
                title = strip_html(_text(child), 300)
            elif name == "link":
                href = child.get("href")
                if href:
                    rel = (child.get("rel") or "alternate").lower()
                    if rel == "alternate" and not link:
                        link = href.strip()
                elif (child.text or "").strip() and not link:
                    link = child.text.strip()
            elif name in ("guid", "id") and not link:
                if (child.text or "").strip().startswith("http"):
                    link = child.text.strip()
            elif name in ("description", "summary", "content", "encoded", "subtitle"):
                candidate = strip_html(_text(child), 1000)
                if len(candidate) > len(summary):
                    summary = candidate
            elif name in ("pubdate", "published", "updated", "date") and published is None:
                published = parse_date(child.text or "")
        if title and link:
            out.append({"title": title, "link": link,
                        "summary": summary, "published": published})
    return out
