# -*- coding: utf-8 -*-
"""Обращения к языковой модели: ранжирование, карточки новостей и перевод."""
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
    """Разбор ответа модели. Терпим к типичному браку в её JSON.

    Даже в режиме json_object модель регулярно отдаёт то кавычку внутри
    строки («фильм "Дюна"» — а это ровно `Expecting ',' delimiter`), то
    висячую запятую, то перенос строки прямо в значении, то обрыв по
    max_tokens. Раньше любая такая мелочь роняла весь раздел, поэтому
    пробуем починить текст, а в самом плохом случае — спасти начало ответа.
    """
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"[\[{].*[\]}]", cleaned, re.DOTALL)
    body = match.group(0) if match else cleaned
    repaired = _repair(body)
    for candidate in (cleaned, body, repaired, _close_truncated(repaired)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    raise LLMError("не удалось разобрать JSON: %s" % text[:200])


#: чем может начинаться значение после запятой в валидном JSON
_VALUE_START = '"{[-0123456789tfn'
#: ...либо закрывающая скобка — тогда запятая просто висячая


def _closes_string(text: str, index: int) -> bool:
    """Кавычка на позиции index закрывает строку — или это забытое экранирование?

    После настоящей закрывающей кавычки идёт `:`, `}`, `]` или запятая, а за
    запятой — начало следующего значения или ключа. Если после запятой идёт
    обычный текст («фильм "Дюна", сборы»), значит кавычка была внутри строки.
    """
    rest = text[index + 1:].lstrip()
    if not rest:
        return True
    if rest[0] in ":}]":
        return True
    if rest[0] != ",":
        return False
    after = rest[1:].lstrip()
    return not after or after[0] in _VALUE_START or after[0] in "}]"


def _repair(text: str) -> str:
    """Экранирует лишние кавычки и управляющие символы, убирает висячие запятые."""
    out = []
    in_string = False
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if in_string:
            if char == "\\" and index + 1 < length:
                out.append(text[index:index + 2])
                index += 2
                continue
            if char == '"':
                if _closes_string(text, index):
                    in_string = False
                    out.append(char)
                else:  # кавычка внутри значения — модель забыла её экранировать
                    out.append('\\"')
                index += 1
                continue
            if char in "\n\r\t":
                out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[char])
            elif ord(char) < 0x20:
                out.append("\\u%04x" % ord(char))
            else:
                out.append(char)
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
        elif char == "," and text[index + 1:].lstrip()[:1] in ("}", "]"):
            pass  # висячая запятая
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _close_truncated(text: str) -> str:
    """Обрезает оборванный ответ по последнему целому элементу и закрывает скобки.

    Ответ упёрся в max_tokens — лучше отдать те карточки, что уже пришли,
    чем потерять весь раздел.
    """
    stack, in_string = [], False
    safe_end, safe_stack = 0, []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            stack.append("]" if char == "[" else "}")
        elif char in "]}":
            if not stack:
                break
            stack.pop()
            if stack:  # закрылось вложенное значение — досюда точно целое
                safe_end, safe_stack = index + 1, list(stack)
        index += 1
    if not safe_end:
        return ""
    return text[:safe_end] + "".join(reversed(safe_stack))


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

BREAKING_SYSTEM = """Ты — выпускающий редактор новостной ленты. Решаешь ровно
один вопрос: может ли это подождать очередного выпуска — или человека нужно
уведомить прямо сейчас.

Это НЕ оценка «интересно ли читателю». Землетрясение важно и тому, кто читает
только про базы данных. Читатель ({persona}) влияет лишь на то, считать ли
событие отраслевым: для него отрасль — своя, для остальных та же новость нишевая.

Шкала urgency — как в мировых агентствах:
10   событие мирового масштаба, меняющее повестку: начало войны, смерть или
     отставка главы крупного государства, катастрофа с сотнями жертв,
     землетрясение M7+ в населённом районе, крах системообразующего банка
8-9  крупное национальное или отраслевое событие с немедленными последствиями:
     внеплановое решение центробанка, активно эксплуатируемая 0-day в массовом
     продукте, отзыв препарата с рынка, крупная авиакатастрофа, отключение
     магистральной инфраструктуры, объявление о слиянии лидеров отрасли
6-7  важное событие, которое спокойно подождёт несколько часов: заметный
     релиз, значимое исследование, отставка руководителя компании
1-5  обычная новость: анонс, отчёт, мнение, инкремент, слух

scope — насколько широк круг задетых:
  global    затрагивает мир: об этом сообщат все ленты планеты
  national  затрагивает одну страну или регион
  industry  затрагивает отрасль этого читателя
  niche     узкий круг

Будь строг. Ложное «срочно» ночью раздражает сильнее, чем десяток непойманных
событий. Анонс, отчёт, слух и пересказ чужой новости срочными не бывают
НИКОГДА, каким бы громким ни был заголовок.

Ответь ТОЛЬКО валидным json вида:
{{"items": [{{"id": 0, "urgency": 9.5, "scope": "global",
  "category": "policy", "why": "до 10 слов"}}]}}
Включи ВСЕХ кандидатов."""

DUP_SYSTEM = """Ты — выпускающий редактор. Про каждую пару новостей решаешь
ровно один вопрос: это ОДНО И ТО ЖЕ событие — или два разных.

Одно и то же — когда читателю, видевшему первую новость, вторая не сообщает
ничего нового: тот же факт, пересказанный другими словами, с другого сайта,
с другим набором подробностей. Разные заголовки, разные источники, разный
объём деталей и разный язык дубликатом быть не мешают.

Разные — когда вторая двигает сюжет дальше или смотрит на него с другой
стороны: названа причина, объявлены последствия, появилась реакция властей,
подсчитан ущерб, принято решение. Общий герой, общая компания или общая тема
сами по себе одним событием ещё не делают.

Примеры:
  «Умер актёр N» / «Актёр N, звезда фильма X, скончался в 80 лет» — одно и то же
  «Умер актёр N» / «Коллеги прощаются с N» — одно и то же (повод один: смерть)
  «Умер актёр N» / «Названа причина смерти N» — разные
  «Землетрясение M7 у берегов Японии» / «Землетрясение в Японии: 200 погибших» — разные
  «X покупает Y» / «X закрыла сделку по покупке Y за $3 млрд» — одно и то же
  «Nvidia отчиталась за квартал» / «Акции Nvidia упали на 8%» — разные

Сомневаешься — отвечай false. Показать читателю похожую новость не страшно,
потерять непохожую — страшно.

Ответь ТОЛЬКО валидным json вида:
{"items": [{"id": 0, "same": true}]}
Ответь про КАЖДУЮ пару."""


SUM_SYSTEM = """Ты пишешь карточки новостей для ежедневного дайджеста.
Читатель: {persona}
Язык ответа: {language}

Правила:
- ВСЕ три поля пиши на языке {language}, даже если источник на другом языке:
  заголовок переводится наравне с текстом, оставлять его как в источнике нельзя;
- имена, названия компаний, продуктов и версии сохраняй в оригинальном
  написании (Nvidia, Linux 7.2, GPT-5), термины переводи;
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

TR_SYSTEM = """Ты переводишь новостной дайджест. Язык перевода: {language}

Правила:
- переводи смысл, а не слова: строка должна читаться так, будто её сразу
  написали на языке {language};
- имена людей, названия компаний, продуктов, версий и тикеры оставляй в
  оригинальном написании (Nvidia, Linux 7.2, GPT-5), остальное переводи;
- ничего не добавляй, не выбрасывай и не сокращай: это перевод, а не пересказ;
- если строка уже на нужном языке, верни её без изменений.

Ответь ТОЛЬКО валидным json вида:
{{"items": [{{"id": 0, "text": "перевод"}}]}}
Верни перевод для КАЖДОГО входного id."""


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


def rate_urgency(clusters, persona):
    """Насколько событие не может ждать выпуска. Возвращает (список, usage).

    Отдельно от `rank_clusters` намеренно. Тот ранжирует кандидатов ОТНОСИТЕЛЬНО
    ДРУГ ДРУГА по интересности для читателя — и на такой шкале землетрясение у
    персоны «инженер-разработчик» получает три балла. Срочность так мерить
    нельзя: это абсолютная величина и она про масштаб события, а не про вкусы.
    """
    payload = []
    for idx, group in enumerate(clusters):
        main = primary_of(group)
        payload.append({"id": idx, "title": main["title"],
                        "lead": main["summary"][:300],
                        "source": main["source_id"],
                        "confirmations": len({i["source_id"] for i in group})})
    data, usage = llm_json(
        BREAKING_SYSTEM.format(persona=persona),
        "Кандидаты (json):\n" + json.dumps(payload, ensure_ascii=False),
        CFG["model_rank"], max_tokens=2000)
    return as_list(data), usage


def judge_duplicates(pairs):
    """«Это одно и то же событие?» пачкой. Возвращает ({номер пары: bool}, usage).

    Пара — (что читатель уже видел, что просится в выпуск). Ответа про пару
    может и не быть: чего модель не вернула, то остаётся неразобранным, и
    новость идёт в выпуск — молчание не повод её выбросить.
    """
    payload = [{"id": idx, "a": str(a)[:300], "b": str(b)[:300]}
               for idx, (a, b) in enumerate(pairs)]
    data, usage = llm_json(
        DUP_SYSTEM,
        "Пары (json):\n" + json.dumps(payload, ensure_ascii=False),
        CFG["model_rank"], max_tokens=40 * len(payload) + 400)
    out = {}
    for entry in as_list(data):
        try:
            idx = int(entry.get("id", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(pairs):
            out[idx] = bool(entry.get("same"))
    return out, usage


def summarize_batch(picked, persona, language, offset=0):
    """Карточки для одной пачки новостей. Ключи ответа — индексы в picked."""
    payload = []
    for idx, (group, _score, _cat) in enumerate(picked, offset):
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


def translate_texts(texts, language):
    """Перевод пачки строк одним запросом. Возвращает ({номер: строка}, usage).

    Номер — позиция строки во входном списке: так ответ раскладывается обратно
    даже когда модель вернула не все строки или перепутала их порядок.
    """
    payload = [{"id": idx, "text": str(text)[:600]}
               for idx, text in enumerate(texts)]
    data, usage = llm_json(
        TR_SYSTEM.format(language=language),
        "Строки (json):\n" + json.dumps(payload, ensure_ascii=False),
        CFG["model_summary"], max_tokens=300 * len(payload) + 500)
    out = {}
    for row in as_list(data):
        try:
            idx = int(row.get("id", -1))
        except (TypeError, ValueError):
            continue
        text = str(row.get("text") or "").strip()
        if 0 <= idx < len(texts) and text:
            out[idx] = text
    return out, usage


def summarize(picked, persona, language):
    """Карточки на весь выпуск. Длинный выпуск идёт пачками.

    Двадцать с лишним новостей в одном запросе упираются в потолок ответа
    модели, и обрывается тогда весь выпуск сразу. Пачками дешевле не станет,
    зато сбой стоит одной пачки: остальные разделы всё равно получат карточки.
    """
    size = max(1, int(CFG["summary_batch"]))
    cards, usage = {}, {"in": 0, "out": 0, "cached": 0}
    failed = []
    for start in range(0, len(picked), size):
        part = picked[start:start + size]
        try:
            got, used = summarize_batch(part, persona, language, start)
        except LLMError as exc:
            failed.append(exc)
            log.warning("Саммари для новостей %d-%d не написалось: %s",
                        start + 1, start + len(part), exc)
            continue
        cards.update(got)
        for key in usage:
            usage[key] += used.get(key, 0)
    if failed and not cards:
        raise LLMError(str(failed[0]))
    return cards, usage
