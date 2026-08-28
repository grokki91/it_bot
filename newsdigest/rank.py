# -*- coding: utf-8 -*-
"""Кластеризация, детерминированный прескоринг и отбор новостей в выпуск."""
from __future__ import annotations

import math
import urllib.parse
from datetime import datetime, timezone

from . import trust
from .config import CFG, WEIGHTS, now_iso
from .feedparse import parse_date
from .textutil import sim_sets


def cluster(items, threshold):
    """Жадная кластеризация: одно событие — один кластер. Сравниваем с ЛУЧШИМ
    из существующих кластеров, иначе порядок обхода влияет на результат.

    Слова сигнатур разбираем один раз на материал: в выпуске по десятку
    разделов кластеризация — самое горячее место во всём прогоне.
    """
    clusters, words = [], []
    for item in items:
        tokens = set(item["sig"].split())
        best, best_score, best_at = None, threshold, -1
        for at, group in enumerate(clusters):
            score = max(sim_sets(tokens, other) for other in words[at])
            if score >= best_score:
                best, best_score, best_at = group, score, at
        if best is None:
            clusters.append([item])
            words.append([tokens])
        else:
            best.append(item)
            words[best_at].append(tokens)
    return clusters


def primary_of(group):
    """Лицо кластера: сначала tier, потом дата.

    Пресс-релиз вендора и госагентство уступают тем, кто эту новость проверял:
    ссылка в карточке должна вести на разбор, а не на анонс. Если проверять
    было некому, порядок прежний — tier, дата.
    """
    return sorted(group, key=lambda i: (trust.demoted(i, group), i["tier"],
                                        i["published_at"] or ""))[0]


def voices(group, limit=3) -> list:
    """Материалы кластера, по которым пишется карточка: лицо кластера и те,
    кто добавляет к нему что-то своё.

    Раньше здесь просто брались первые три материала подряд. Пока кластер
    собирали слова, это было почти всё равно: в него попадал пересказ одного и
    того же. После склейки дублей (`dedup.fuse`) в кластере лежат заметки об
    одном событии из РАЗНЫХ редакций, и написаны они по-разному — одна в три
    строки, другая в абзац. Порядок в списке при этом случайный: он про то,
    чей фид разобрали раньше, а не про то, кто рассказал подробнее.

    Поэтому берём лицо кластера и самые подробные из остальных, по одной
    заметке на источник: карточка тогда пишется по всему, что известно о
    событии, а не по тому, что попалось первым.
    """
    main = primary_of(group)
    out, seen = [main], {main["source_id"]}
    for item in sorted((i for i in group if i is not main),
                       key=lambda i: -len(i.get("summary") or "")):
        if len(out) >= max(1, limit):
            break
        if item["source_id"] in seen:
            continue
        seen.add(item["source_id"])
        out.append(item)
    return out


def prescore(group) -> float:
    """Детерминированный балл: дёшево, воспроизводимо, легко отлаживается."""
    main = primary_of(group)
    domains = {urllib.parse.urlparse(i["url"]).netloc for i in group}
    corroboration = min(math.log(len(domains) + 1, 2) / 2.5, 1.0)
    social = max(i["social"] for i in group)
    freshness = 0.5
    published = parse_date(main["published_at"] or "")
    if published:
        age_h = (datetime.now(timezone.utc) - published).total_seconds() / 3600
        freshness = 0.5 ** (max(age_h, 0) / 24.0)
    return (WEIGHTS["trust"] * trust.trust(main["source_id"])
            + WEIGHTS["corroboration"] * corroboration
            + WEIGHTS["social"] * social + WEIGHTS["freshness"] * freshness)


def select(ranking, shortlist, limit=None, min_score=None, min_items=None,
           per_source=None, per_category=None):
    """Отбор новостей в выпуск (или в один раздел выпуска).

    Проход 1 — с лимитами на источник и категорию (диверсификация).
    Проход 2 — если новостей меньше min_items, лимиты снимаются.
    Проход 3 — если всё ещё мало, порог важности опускается на 1.5.
    Так выпуск не оказывается пустым из-за жёстких настроек, но в обычный день
    диверсификация работает.

    Без аргументов работает по CFG — это обычный выпуск. Раздел передаёт свои
    лимиты: ему нужно ровно N новостей и, как правило, из разных источников.
    """
    limit = CFG["max_items"] if limit is None else limit
    min_score = CFG["min_score"] if min_score is None else min_score
    min_items = CFG["min_items"] if min_items is None else min_items
    per_source = CFG["max_per_source"] if per_source is None else per_source
    per_category = CFG["max_per_category"] if per_category is None else per_category

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
    hard_cap = max(per_source, int(math.ceil(limit / 3.0)))

    def sweep(threshold, use_limits):
        cap = per_source if use_limits else hard_cap
        for idx, score, category, group in entries:
            if len(picked) >= limit:
                return
            if idx in taken or score < threshold:
                continue
            source = primary_of(group)["source_id"]
            if per_src.get(source, 0) >= cap:
                continue
            if use_limits and per_cat.get(category, 0) >= per_category:
                continue
            per_cat[category] = per_cat.get(category, 0) + 1
            per_src[source] = per_src.get(source, 0) + 1
            taken.add(idx)
            picked.append((group, score, category))

    sweep(min_score, True)
    if len(picked) < min_items:
        sweep(min_score, False)
    if len(picked) < min_items:
        sweep(min_score - 1.5, False)
    return picked


def story(title, lead="") -> str:
    """Новость одной строкой — так её увидит модель, когда будет решать,
    об одном ли событии речь. Заголовка обычно мало: «Коллеги прощаются» без
    первого абзаца не говорит даже, с кем прощаются."""
    title = str(title or "").strip()
    lead = " ".join(str(lead or "").split())[:180].strip()
    return "%s. %s" % (title, lead) if lead else title


class SentIndex:
    """История отправленного этому читателю — прочитанная один раз.

    `already_sent` вызывается для каждого кластера каждого раздела, а история
    за 60 дней — это полторы тысячи строк. Читать и разбирать их заново на
    каждый кластер значило бы тратить минуты на выпуск из полутора десятков разделов.
    """

    def __init__(self, conn=None, chat_id=""):
        self.hashes = set()
        self.words = []
        self.sigs = []
        self.texts = []
        self.at = []
        # приговоры модели (newsdigest/dedup.py). `dupes` — сигнатуры, про
        # которые уже известно, что это повтор; `links` — пары «одно и то же»,
        # где ни один ещё не показан: второй отпадёт, когда возьмут первого
        self.dupes = set()
        self.links = {}
        if conn is None:
            return
        for row in conn.execute(
                "SELECT url_hash, sig, title, headline, summary, sent_at FROM sent "
                "WHERE chat_id=?", (str(chat_id),)):
            self.hashes.add(row["url_hash"])
            self._add(row["sig"] or "",
                      story(row["headline"] or row["title"], row["summary"]),
                      row["sent_at"] or "")

    def _add(self, sig, text, at="") -> None:
        self.words.append(set(sig.split()))
        self.sigs.append(sig)
        self.texts.append(text)
        self.at.append(at)

    def seen(self, group, threshold) -> bool:
        """Уходило ли это событие читателю раньше — по ссылке или по смыслу."""
        if any(item["url_hash"] in self.hashes for item in group):
            return True
        sig = primary_of(group)["sig"]
        if sig in self.dupes:
            return True
        tokens = set(sig.split())
        return any(sim_sets(tokens, other) >= threshold for other in self.words)

    def mark(self, sig) -> None:
        """«Это повтор» — приговор, вынесенный не по словам."""
        if sig:
            self.dupes.add(sig)

    def link(self, sig_a, sig_b) -> None:
        """Две новости выпуска об одном событии. Кого показывать — решит отбор:
        как только одну возьмут, вторая станет повтором (см. `remember`)."""
        if sig_a and sig_b and sig_a != sig_b:
            self.links.setdefault(sig_a, set()).add(sig_b)
            self.links.setdefault(sig_b, set()).add(sig_a)

    def near(self, group):
        """Ближайшее из истории: (совпадение 0..1, сигнатура, текст, когда ушло).

        Сравнивает не только «лицо» кластера, как `seen`, а все его материалы:
        заметка, попавшая в кластер вторым источником, знает о событии не
        меньше первой, а слова у неё другие. Так дороже — поэтому и зовётся
        не на каждый кластер, а на те немногие, что дожили до отбора.
        """
        tokens = [set(item["sig"].split()) for item in group]
        best, at = 0.0, -1
        for index, other in enumerate(self.words):
            score = max(sim_sets(one, other) for one in tokens)
            if score > best:
                best, at = score, index
        if at < 0:
            return 0.0, "", "", ""
        return best, self.sigs[at], self.texts[at], self.at[at]

    def remember(self, group) -> None:
        """Отмечает кластер как использованный — чтобы соседний раздел
        не выдал ту же новость под другим соусом."""
        for item in group:
            self.hashes.add(item["url_hash"])
        main = primary_of(group)
        self._add(main["sig"], story(main["title"], main.get("summary")), now_iso())
        self.dupes.update(self.links.get(main["sig"], ()))


def already_sent(conn, group, threshold, chat_id="") -> bool:
    """Межсуточный дедуп: не повторяем то, что уже уходило ЭТОМУ читателю."""
    return SentIndex(conn, chat_id).seen(group, threshold)
