# -*- coding: utf-8 -*-
"""Реакции читателя и то, как они влияют на следующий выпуск.

Кнопки под карточками — единственный дешёвый сигнал о вкусах. Он идёт в дело
двумя путями:

    1) прескоринг — источники и категории, которые нравятся, поднимаются
       в шорт-листе и чаще доезжают до модели;
    2) промпт — модели показывают несколько недавних «зашло/не зашло»,
       чтобы она калибровала оценку под конкретного читателя.

Оба влияния намеренно мягкие: важность события всё равно должна побеждать
привычки. Поэтому вклад ограничен CFG["feedback_weight"], а сглаживание
Лапласа не даёт одной случайной реакции перевернуть картину.
"""
from __future__ import annotations

from .config import CFG, now_iso

UP, DOWN = "up", "down"


def record(conn, chat_id, url_hash, verdict, facts=None) -> None:
    """Ставит (или меняет) оценку. Повторное нажатие той же кнопки — не ошибка."""
    facts = facts or {}
    conn.execute(
        "INSERT INTO feedback(chat_id,url_hash,verdict,source_id,category,title,at) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(chat_id,url_hash) DO UPDATE SET "
        "verdict=excluded.verdict, at=excluded.at",
        (str(chat_id), url_hash, verdict, facts.get("source_id", ""),
         facts.get("category", "other"), (facts.get("title") or "")[:200], now_iso()))
    conn.commit()


def save_bookmark(conn, chat_id, url_hash, facts=None) -> bool:
    """Кладёт в закладки. False — уже было, значит нажали второй раз (убираем)."""
    facts = facts or {}
    existing = conn.execute("SELECT 1 FROM saved WHERE chat_id=? AND url_hash=?",
                            (str(chat_id), url_hash)).fetchone()
    if existing:
        conn.execute("DELETE FROM saved WHERE chat_id=? AND url_hash=?",
                     (str(chat_id), url_hash))
        conn.commit()
        return False
    conn.execute(
        "INSERT INTO saved(chat_id,url_hash,title,url,source_id,at) VALUES (?,?,?,?,?,?)",
        (str(chat_id), url_hash, (facts.get("title") or "")[:200],
         facts.get("url", ""), facts.get("source_id", ""), now_iso()))
    conn.commit()
    return True


def bookmarks(conn, chat_id, limit=15):
    return list(conn.execute(
        "SELECT title, url, source_id, at FROM saved WHERE chat_id=? "
        "ORDER BY at DESC LIMIT ?", (str(chat_id), limit)))


def _scores(conn, chat_id, column) -> dict:
    """(нравится - не нравится) со сглаживанием: -1..1, у редких значений ~0."""
    rows = conn.execute(
        "SELECT %s AS key, "
        "SUM(CASE WHEN verdict='up' THEN 1 ELSE 0 END) AS up, "
        "SUM(CASE WHEN verdict='down' THEN 1 ELSE 0 END) AS down "
        "FROM feedback WHERE chat_id=? AND %s != '' GROUP BY %s"
        % (column, column, column), (str(chat_id),))
    out = {}
    for row in rows:
        up, down = row["up"] or 0, row["down"] or 0
        out[row["key"]] = (up - down) / float(up + down + 2)
    return out


class Affinity:
    """Насколько читателю обычно заходит этот источник и эта категория."""

    def __init__(self, sources=None, categories=None):
        self.sources = sources or {}
        self.categories = categories or {}

    def __bool__(self):
        return bool(self.sources or self.categories)

    @classmethod
    def load(cls, conn, chat_id):
        return cls(_scores(conn, chat_id, "source_id"),
                   _scores(conn, chat_id, "category"))

    def bonus(self, main) -> float:
        """Прибавка к прескору: источник весит больше категории."""
        return (0.6 * self.sources.get(main["source_id"], 0.0)
                + 0.4 * self.categories.get(main["category"], 0.0))

    def top(self, count=5):
        """(понравившиеся, разонравившиеся) источники — для команды /taste."""
        ranked = sorted(self.sources.items(), key=lambda kv: -kv[1])
        liked = [(k, v) for k, v in ranked if v > 0.05][:count]
        disliked = [(k, v) for k, v in reversed(ranked) if v < -0.05][:count]
        return liked, disliked


EMPTY = Affinity()


def persona_hint(conn, chat_id, limit=5) -> str:
    """Дописка к portrait читателя: что ему недавно зашло, а что нет."""
    rows = list(conn.execute(
        "SELECT title, verdict FROM feedback WHERE chat_id=? AND title != '' "
        "ORDER BY at DESC LIMIT 40", (str(chat_id),)))
    liked = [r["title"] for r in rows if r["verdict"] == UP][:limit]
    disliked = [r["title"] for r in rows if r["verdict"] == DOWN][:limit]
    if not liked and not disliked:
        return ""
    parts = ["", "Обратная связь этого читателя по прошлым выпускам:"]
    if liked:
        parts.append("отметил как полезное: " + "; ".join('«%s»' % t for t in liked))
    if disliked:
        parts.append("отметил как ненужное: " + "; ".join('«%s»' % t for t in disliked))
    parts.append("Считай это сигналом о вкусах, а не жёстким правилом: "
                 "по-настоящему важное событие публикуй в любом случае.")
    return "\n".join(parts)


def weighted_prescore(conn, chat_id):
    """Готовая функция сортировки кластеров с учётом вкусов читателя."""
    from .rank import prescore, primary_of

    aff = Affinity.load(conn, chat_id)
    if not aff or not CFG["feedback_weight"]:
        return prescore
    return lambda group: (prescore(group)
                          + CFG["feedback_weight"] * aff.bonus(primary_of(group)))
