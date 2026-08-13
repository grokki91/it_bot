# -*- coding: utf-8 -*-
"""Обращения к языковой модели: ранжирование кандидатов и карточки новостей."""
from __future__ import annotations

import json
import re
import time

from . import config
from .config import CFG, log
from .net import post_json
from .rank import primary_of


class LLMError(RuntimeError):
    pass


def llm_json(system: str, user: str, model: str, max_tokens: int = 3000):
    """Один вызов DeepSeek в режиме JSON. Возвращает (данные, usage)."""
    if not config.DS_KEY:
        raise LLMError("DEEPSEEK_API_KEY не задан")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    if CFG["disable_thinking"]:
        payload["thinking"] = {"type": "disabled"}

    url = CFG["llm_base"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer " + config.DS_KEY}
    last = ""
    for attempt in range(1, CFG["llm_retries"] + 1):
        status, data, err = post_json(url, payload, headers, CFG["llm_timeout"])
        if status == 200 and data:
            try:
                text = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError("неожиданный формат ответа: %s" % exc)
            usage = data.get("usage") or {}
            return _loads(text), {
                "in": usage.get("prompt_tokens", 0),
                "out": usage.get("completion_tokens", 0),
                "cached": (usage.get("prompt_cache_hit_tokens")
                           or usage.get("prompt_tokens_cached") or 0),
            }
        last = "HTTP %s %s" % (status, err or (json.dumps(data)[:300] if data else ""))
        # некоторые аккаунты/модели не принимают поле thinking — снимаем и пробуем ещё
        if status == 400 and "thinking" in payload and "thinking" in last.lower():
            payload.pop("thinking", None)
            continue
        if status not in (0, 408, 429, 500, 502, 503, 504) and status != 200:
            raise LLMError(last)
        if attempt < CFG["llm_retries"]:
            wait = min(2 ** attempt + attempt, 30)
            log.warning("DeepSeek попытка %d/%d не удалась (%s), пауза %ds",
                        attempt, CFG["llm_retries"], last[:160], wait)
            time.sleep(wait)
    raise LLMError("DeepSeek недоступен после %d попыток: %s"
                   % (CFG["llm_retries"], last))


def _loads(text: str):
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except ValueError:
        match = re.search(r"[\[{].*[\]}]", cleaned, re.DOTALL)
        if not match:
            raise LLMError("не удалось разобрать JSON: %s" % text[:200])
        return json.loads(match.group(0))


def as_list(data, key="items"):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for candidate in (key, "результат", "news", "data", "list"):
            if isinstance(data.get(candidate), list):
                return data[candidate]
        for value in data.values():
            if isinstance(value, list):
                return value
    return []


def llm_cost(usage) -> float:
    fresh = max(usage.get("in", 0) - usage.get("cached", 0), 0)
    return (fresh / 1e6 * CFG["price_in"]
            + usage.get("cached", 0) / 1e6 * CFG["price_in"] * 0.02
            + usage.get("out", 0) / 1e6 * CFG["price_out"])


RANK_SYSTEM = """Ты — редактор ежедневного дайджеста новостей. Читатель: {persona}

Отранжируй кандидатов ОТНОСИТЕЛЬНО ДРУГ ДРУГА. Критерии по убыванию важности:
1. Значимость события для отрасли
2. Реальная новизна: принципиально новое, а не инкремент и не пересказ известного
3. Практическая ценность именно для этого читателя
4. Достоверность: подтверждённый факт важнее слуха или анонса без деталей

Калибровка балла:
9-10 — прорыв, крупная сделка, смена правил игры
6-8  — заметный релиз, значимое исследование, важный open-source
3-5  — инкрементальное обновление, отраслевой отчёт, мнение
1-2  — маркетинг, рерайт чужой новости, спекуляция, «5 способов...»

Ответь ТОЛЬКО валидным json вида:
{{"items": [{{"id": 0, "score": 8.5, "category": "labs", "why": "до 10 слов"}}]}}
Включи ВСЕХ кандидатов, отсортируй по убыванию score."""

SUM_SYSTEM = """Ты пишешь карточки новостей для ежедневного дайджеста.
Читатель: {persona}
Язык ответа: {language}

Правила:
- пиши СВОИМИ СЛОВАМИ, не копируй фразы из источника;
- никаких фактов и цифр, которых нет во входном тексте, не додумывай;
- если деталей мало — пиши короче, это нормально;
- без воды и оборотов вроде «в мире произошло знаковое событие».

Ответь ТОЛЬКО валидным json вида:
{{"items": [{{"id": 0,
  "headline": "заголовок до 70 символов",
  "what": "что произошло, 1-2 предложения",
  "why": "почему это важно — следствие, а не пересказ, 1 предложение"}}]}}
Верни карточку для КАЖДОГО входного id."""


def rank_clusters(clusters, persona):
    payload = []
    for idx, group in enumerate(clusters):
        main = primary_of(group)
        payload.append({"id": idx, "title": main["title"],
                        "lead": main["summary"][:300],
                        "source": main["source_id"],
                        "confirmations": len({i["source_id"] for i in group})})
    data, usage = llm_json(
        RANK_SYSTEM.format(persona=persona),
        "Кандидаты (json):\n" + json.dumps(payload, ensure_ascii=False),
        CFG["model_rank"], max_tokens=3000)
    return as_list(data), usage


def summarize(picked, persona, language):
    payload = []
    for idx, (group, _score, _cat) in enumerate(picked):
        main = primary_of(group)
        body = " ".join("[%s] %s. %s" % (i["source_id"], i["title"], i["summary"][:350])
                        for i in group[:3])
        payload.append({"id": idx, "url": main["url"], "text": body[:1500]})
    data, usage = llm_json(
        SUM_SYSTEM.format(persona=persona, language=language),
        "Новости (json):\n" + json.dumps(payload, ensure_ascii=False),
        CFG["model_summary"], max_tokens=400 * len(payload) + 500)
    cards = {}
    for card in as_list(data):
        try:
            cards[int(card.get("id", -1))] = card
        except (TypeError, ValueError):
            continue
    return cards, usage
