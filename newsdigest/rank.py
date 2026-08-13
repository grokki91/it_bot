# -*- coding: utf-8 -*-
"""Кластеризация, детерминированный прескоринг и отбор новостей в выпуск."""
from __future__ import annotations

import math
import urllib.parse
from datetime import datetime, timezone

from .config import CFG, WEIGHTS
from .feedparse import parse_date
from .textutil import similarity


def cluster(items, threshold):
    """Жадная кластеризация: одно событие — один кластер. Сравниваем с ЛУЧШИМ
    из существующих кластеров, иначе порядок обхода влияет на результат."""
    clusters = []
    for item in items:
        best, best_score = None, threshold
        for group in clusters:
            score = max(similarity(item["sig"], other["sig"]) for other in group)
            if score >= best_score:
                best, best_score = group, score
        if best is None:
            clusters.append([item])
        else:
            best.append(item)
    return clusters


def primary_of(group):
    """Первоисточник важнее агрегатора: сначала tier, потом дата."""
    return sorted(group, key=lambda i: (i["tier"], i["published_at"] or ""))[0]


def prescore(group) -> float:
    """Детерминированный балл: дёшево, воспроизводимо, легко отлаживается."""
    main = primary_of(group)
    tier = {1: 1.0, 2: 0.6, 3: 0.3}.get(main["tier"], 0.3)
    domains = {urllib.parse.urlparse(i["url"]).netloc for i in group}
    corroboration = min(math.log(len(domains) + 1, 2) / 2.5, 1.0)
    social = max(i["social"] for i in group)
    freshness = 0.5
    published = parse_date(main["published_at"] or "")
    if published:
        age_h = (datetime.now(timezone.utc) - published).total_seconds() / 3600
        freshness = 0.5 ** (max(age_h, 0) / 24.0)
    return (WEIGHTS["source_tier"] * tier + WEIGHTS["corroboration"] * corroboration
            + WEIGHTS["social"] * social + WEIGHTS["freshness"] * freshness)


def select(ranking, shortlist):
    """Отбор новостей в выпуск.

    Проход 1 — с лимитами на источник и категорию (диверсификация).
    Проход 2 — если новостей меньше min_items, лимиты снимаются.
    Проход 3 — если всё ещё мало, порог важности опускается на 1.5.
    Так выпуск не оказывается пустым из-за жёстких настроек, но в обычный день
    диверсификация работает.
    """
    entries = []
    for entry in ranking:
        try:
            idx = int(entry.get("id", -1))
            score = float(entry.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(shortlist):
            group = shortlist[idx]
            entries.append((idx, score, entry.get("category")
                            or primary_of(group)["category"], group))
    entries.sort(key=lambda e: -e[1])

    picked, taken, per_cat, per_src = [], set(), {}, {}

    # Даже при ослаблении лимитов один сайт не занимает больше трети выпуска —
    # иначе тихим днём весь дайджест уезжает в Hacker News.
    hard_cap = max(CFG["max_per_source"], int(math.ceil(CFG["max_items"] / 3.0)))

    def sweep(min_score, use_limits):
        cap = CFG["max_per_source"] if use_limits else hard_cap
        for idx, score, category, group in entries:
            if len(picked) >= CFG["max_items"]:
                return
            if idx in taken or score < min_score:
                continue
            source = primary_of(group)["source_id"]
            if per_src.get(source, 0) >= cap:
                continue
            if use_limits and per_cat.get(category, 0) >= CFG["max_per_category"]:
                continue
            per_cat[category] = per_cat.get(category, 0) + 1
            per_src[source] = per_src.get(source, 0) + 1
            taken.add(idx)
            picked.append((group, score, category))

    sweep(CFG["min_score"], True)
    if len(picked) < CFG["min_items"]:
        sweep(CFG["min_score"], False)
    if len(picked) < CFG["min_items"]:
        sweep(CFG["min_score"] - 1.5, False)
    return picked


def already_sent(conn, group, threshold, chat_id="") -> bool:
    """Межсуточный дедуп: не повторяем то, что уже уходило ЭТОМУ читателю."""
    hashes = [i["url_hash"] for i in group]
    marks = ",".join("?" * len(hashes))
    if conn.execute(
            "SELECT 1 FROM sent WHERE chat_id=? AND url_hash IN (%s) LIMIT 1" % marks,
            [str(chat_id)] + hashes).fetchone():
        return True
    main = primary_of(group)
    for row in conn.execute("SELECT sig FROM sent WHERE chat_id=?", (str(chat_id),)):
        if similarity(main["sig"], row["sig"]) >= threshold:
            return True
    return False
