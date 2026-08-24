# -*- coding: utf-8 -*-
"""Числовые сигналы срочности: то, что не нужно спрашивать у модели.

У части событий важность выражена числом, и это число приходит вместе с
новостью. Магнитуда землетрясения, уровень тревоги GDACS, факт «уязвимость
эксплуатируется прямо сейчас» из каталога CISA — всё это объективные величины,
на которые мировые новостные службы и опираются. Спрашивать у языковой модели,
насколько срочна M7.4, — значит платить за угадывание того, что уже известно
точно.

Поэтому здесь считается «пол» срочности: оценка, ниже которой событие точно не
опускается. Модель может поднять её (у неё есть контекст, которого нет у
числа), но не может опустить.

Второе назначение — страховка. Если модель недоступна, срочное не отправляется:
слишком легко разбудить человека ради пересказа чужой новости. Но событие с
твёрдым числом в этом не нуждается: землетрясение M7.4 остаётся
землетрясением M7.4, что бы там ни ответил DeepSeek.

Всё бесплатно и без ключей. Магнитуда и уровень тревоги приходят прямо в
заголовке фида (USGS и GDACS их туда и кладут) — лишних запросов не нужно
вовсе. Каталог CISA KEV тянется одним запросом раз в сутки.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from .config import CFG, log, now_iso
from .feedparse import parse_date
from .net import http_get
from .storage import meta_get, meta_set

#: «M 6.5 - 100km SE of ...» — так USGS называет свои записи
MAGNITUDE = re.compile(r"\bM\s*(\d(?:\.\d)?)\b")
#: «Green/Orange/Red alert» — уровень тревоги GDACS
GDACS_LEVEL = re.compile(r"\b(red|orange)\s+alert\b", re.IGNORECASE)
#: идентификатор уязвимости
CVE = re.compile(r"\bCVE-(\d{4})-(\d{4,7})\b", re.IGNORECASE)

KEV_URL = ("https://www.cisa.gov/sites/default/files/feeds/"
           "known_exploited_vulnerabilities.json")

#: Пол срочности по магнитуде. Границы — те же, по которым геослужбы решают,
#: объявлять ли тревогу: M7 в населённом районе это международная новость,
#: M6 — национальная, M5 сама по себе новостью почти не бывает.
QUAKE = ((7.0, 9.5, "global"), (6.0, 8.0, "national"), (5.5, 7.0, "national"))

#: Уровень тревоги GDACS: красный объявляют при угрозе гуманитарной катастрофы
DISASTER = {"red": (9.5, "global"), "orange": (8.0, "national")}

#: Уязвимость из каталога CISA — это не «нашли дыру», а «её эксплуатируют».
#: Для того, кто ей пользуется, это работа на сегодня, а не новость на завтра
EXPLOITED = (8.5, "industry")


def magnitude(text: str) -> float:
    """Магнитуда из заголовка. 0 — числа нет."""
    match = MAGNITUDE.search(str(text or ""))
    if not match:
        return 0.0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0.0
    return value if 0 < value <= 10 else 0.0


def disaster_level(text: str) -> str:
    """'red' / 'orange' / '' — уровень тревоги GDACS."""
    match = GDACS_LEVEL.search(str(text or ""))
    return match.group(1).lower() if match else ""


def cve_ids(text: str) -> set:
    """Все CVE, упомянутые в тексте, в верхнем регистре."""
    return {("CVE-%s-%s" % (year, num)).upper()
            for year, num in CVE.findall(str(text or ""))}


# ------------------------------------------------------- каталог CISA KEV
def kev_ids(conn) -> set:
    """Уязвимости, которые эксплуатируются прямо сейчас (каталог CISA).

    Тянется раз в сутки и кладётся в meta: каталог меняется на единицы записей
    в неделю, а весит около мегабайта — ходить за ним на каждый сбор незачем.
    Недоступен — работаем без него, это всего лишь дополнительный сигнал.
    """
    if not CFG["use_kev"]:
        return set()
    known = set((meta_get(conn, "kev_ids", "") or "").split())
    now = datetime.now(timezone.utc)

    fetched = parse_date(meta_get(conn, "kev_at", ""))
    if fetched and now - fetched < timedelta(hours=24):
        return known
    # не получилось — пробуем через час, а не через сутки: срочное проверяется
    # каждые четверть часа, и долбиться в недоступный адрес незачем, но и
    # выключать сигнал на сутки из-за одной сетевой ошибки неправильно
    tried = parse_date(meta_get(conn, "kev_try", ""))
    if tried and now - tried < timedelta(hours=1):
        return known
    meta_set(conn, "kev_try", now_iso())

    try:
        status, raw = http_get(KEV_URL, timeout=30)
        if status != 200 or not raw:
            raise ValueError("HTTP %s" % status)
        data = json.loads(raw.decode("utf-8", "replace"))
        ids = {str(row.get("cveID") or "").upper()
               for row in (data.get("vulnerabilities") or [])}
        ids.discard("")
    except Exception as exc:  # noqa: BLE001 — сигнал необязательный
        log.warning("Каталог CISA KEV недоступен (%s) — работаю без него", exc)
        return known

    meta_set(conn, "kev_ids", " ".join(sorted(ids)))
    meta_set(conn, "kev_at", now_iso())
    log.info("Каталог CISA KEV обновлён: %d уязвимостей под атакой", len(ids))
    return ids


# ------------------------------------------------------------------ пол
def floor_for(item, kev=()) -> tuple:
    """(срочность, охват, чем обосновано) для одного материала. 0 — сигнала нет."""
    text = "%s %s" % (item.get("title") or "", item.get("summary") or "")

    level = disaster_level(text)
    if level in DISASTER:
        urgency, scope = DISASTER[level]
        return urgency, scope, "GDACS: %s alert" % level

    value = magnitude(text)
    if value:
        for threshold, urgency, scope in QUAKE:
            if value >= threshold:
                return urgency, scope, "магнитуда %.1f" % value

    hit = cve_ids(text) & set(kev)
    if hit:
        urgency, scope = EXPLOITED
        return urgency, scope, "эксплуатируется: %s" % ", ".join(sorted(hit)[:2])

    return 0.0, "", ""


def floor_of(group, kev=()) -> tuple:
    """Самый сильный числовой сигнал кластера."""
    best = (0.0, "", "")
    for item in group:
        found = floor_for(item, kev)
        if found[0] > best[0]:
            best = found
    return best


def raise_floors(rated, shortlist, kev=()) -> dict:
    """Поднимает оценки модели до того, что говорят числа.

    Только поднимает: у модели есть контекст, которого нет у магнитуды, и её
    более высокую оценку затирать нечем. А вот занизить M7.4 она не должна —
    именно это и происходило, когда срочность мерилась «интересно ли читателю».
    """
    for idx, group in enumerate(shortlist):
        urgency, scope, why = floor_of(group, kev)
        if not urgency:
            continue
        entry = rated.get(id(group))
        if entry is None:
            rated[id(group)] = {"urgency": urgency, "scope": scope,
                                "category": group[0].get("category") or "other",
                                "why": why}
        elif urgency > entry["urgency"]:
            log.info("Срочность поднята числом: %.1f -> %.1f (%s)",
                     entry["urgency"], urgency, why)
            entry.update(urgency=urgency, scope=scope or entry["scope"], why=why)
    return rated
