# -*- coding: utf-8 -*-
"""Обращения к языковой модели: ранжирование, карточки новостей и перевод."""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import queue
import re
import threading
import time

from . import config, redact, spend
from .config import CFG, log
from .net import post_json
from .rank import primary_of, voices
from .textutil import lead_of


class LLMError(RuntimeError):
    pass


def task(label: str, payload, *head) -> str:
    """Сообщение пользователя: переменная шапка, потом данные задания.

    Всё, что меняется от подписчика к подписчику и от раздела к разделу —
    портрет читателя, язык ответа, список разделов, — живёт ЗДЕСЬ, а не в
    системном промпте. Причина денежная: провайдер кэширует запрос по общему
    НАЧАЛУ, и кэшированный токен стоит в разы дешевле обычного. Пока
    инструкция начиналась строкой «Читатель: инженер-разработчик…», общего
    начала у двух запросов не было вовсе — кэш не срабатывал ни разу. Теперь
    системный промпт у всех запросов одного вида побайтово одинаков, и платим
    мы за него по цене кэша.

    Данные пишем компактным json: пробелы после запятых и двоеточий — это
    лишние токены в каждой строке каждого запроса, а модели они не говорят
    ничего.
    """
    lines = [line for line in head if line]
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(lines + ["", "%s (json):" % label, body])


#: обязательное поле ответа по виду запроса. Бесплатный ответ, где у какого-то
#: id его нет, не берём: сверить его с DeepSeek не с чем, а пустые места в
#: выпуске дороже лишнего платного запроса
REQUIRED = {"rank": "score", "urgency": "urgency", "dupes": "news",
            "claims": "verdict", "classify": "section", "cards": "headline",
            "translate": "text"}
#: у этих видов обязательное поле — ещё и непустая строка
TEXT_KINDS = ("cards", "translate")

#: состояние бесплатного маршрута на процесс. Разделы ранжируются в три нити,
#: поэтому всё — под замком
_lock = threading.Lock()
#: модель или "*" (весь маршрут) -> до какого time.time() к ней не ходим
_free_off = {}
#: (модель, вид) -> до какого time.time() её не зовёт сверка (`_shadow_one`)
_shadow_off = {}
#: о чём уже предупредили: одно и то же в лог раз за процесс (или за сутки)
_told = set()
#: OpenRouter не принял `reasoning`, DeepSeek не принял `thinking` — больше не шлём
_no_reasoning = False
_no_thinking = False

ROUTES = ("auto", "paid", "free")


def _once(key, level, message, *args) -> None:
    with _lock:
        if key in _told:
            return
        _told.add(key)
    log.log(level, message, *args)


def _payload(system, user, model, max_tokens) -> dict:
    return {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def _used(usage) -> dict:
    """usage провайдера -> наш учёт. Поля у DeepSeek и OpenRouter свои."""
    usage = usage or {}
    prompt = usage.get("prompt_tokens_details") or {}
    answer = usage.get("completion_tokens_details") or {}
    return {
        "in": usage.get("prompt_tokens", 0) or 0,
        "out": usage.get("completion_tokens", 0) or 0,
        "cached": (usage.get("prompt_cache_hit_tokens")
                   or usage.get("prompt_tokens_cached")
                   or prompt.get("cached_tokens") or 0),
        "reason": answer.get("reasoning_tokens", 0) or 0,
    }


def llm_json(system: str, user: str, model: str, max_tokens: int = 3000,
             kind: str = "", expect=(), check=None, route: str = "auto"):
    """Один вызов модели в режиме JSON. Возвращает (данные, usage).

    `kind` — на что запрос (`spend.LABELS`): по нему расход ложится в журнал,
    и `status` показывает, из чего он состоит.

    Это единственная дверь наружу к языковой модели: и ранжирование, и
    карточки, и перевод, и определение раздела уходят через неё. Поэтому
    здесь же стоит и заслон от секретов (`redact.clean_messages`): текст
    запроса собирается из портрета читателя, ссылок на источники и
    содержимого чужих лент — то есть из мест, куда ключ может попасть
    случайно. Чистим ПЕРЕД отправкой и по факту наличия сообщения, а не по
    именам наших промптов: сменится провайдер или добавится новая роль —
    защита останется на месте.

    `route`: `auto` — виды из `free_kinds` сначала идут в бесплатную модель
    OpenRouter, при любом сбое — в DeepSeek; `paid` — только DeepSeek; `free` —
    только OpenRouter, сбой — `LLMError`. Бесплатный ответ берём, только если
    в нём есть каждый id из `expect` с обязательным полем (`REQUIRED`) и его
    принял `check(данные)`.

    Ответ DeepSeek по видам из `free_shadow` в фоне сверяется с бесплатной
    моделью (`shadow_put`): в работу идёт только он, а `status` показывает, можно
    ли этот вид отдать бесплатной. Принятый бесплатный ответ — наоборот: первый и
    каждый `free_audit`-й в фоне переспрашивается у DeepSeek (`audit_put`), и
    `status` скажет, если вид пора вернуть в DeepSeek.
    """
    if route not in ROUTES:
        raise ValueError("route: %r" % route)
    payload = _payload(system, user, model, max_tokens)
    hidden = redact.clean_messages(payload)
    if hidden:
        # что именно вырезали — не пишем даже в лог: там оно и окажется
        log.warning("Из запроса к модели вырезаны чувствительные данные (%s). "
                    "Проверьте profiles.json и адреса источников",
                    ", ".join(hidden))
    if route == "free" or (route == "auto" and free_wanted(kind)):
        got = _free(payload, kind, expect, check, strict=route == "free")
        if got is not None:
            return got
    data, used = _paid(payload, kind)
    if route == "auto" and shadow_wanted(kind):
        shadow_put(kind, payload, data, expect)
    return data, used


def _paid(payload, kind):
    """Нынешний путь: DeepSeek с повторами."""
    global _no_thinking
    if not config.DS_KEY:
        raise LLMError("DEEPSEEK_API_KEY не задан")
    if CFG["disable_thinking"] and not _no_thinking:
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
            used = _used(data.get("usage"))
            # деньги потрачены, даже если ответ не разобрался: учитываем до разбора
            spend.add(kind, used, llm_cost(used))
            return _loads(text), used
        last = "HTTP %s %s" % (status, err or (json.dumps(data)[:300] if data else ""))
        # некоторые аккаунты/модели не принимают поле thinking — снимаем и
        # больше не шлём: иначе каждый запрос тратит лишний круг 400 и попытку
        if status == 400 and "thinking" in payload and "thinking" in last.lower():
            payload.pop("thinking", None)
            _no_thinking = True
            _once("thinking", logging.WARNING,
                  "DeepSeek не принял thinking=disabled (%s) — модель рассуждает, "
                  "это оплачивается как выход", last[:160])
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


# ------------------------------------------------------- бесплатный маршрут
def free_models() -> list:
    return [m.strip() for m in str(CFG["free_models"] or "").split(",") if m.strip()]


def free_wanted(kind) -> bool:
    """Идёт ли этот вид сначала в бесплатную модель."""
    if not config.OR_KEY or not free_models():
        return False
    kinds = {k.strip() for k in str(CFG["free_kinds"] or "").split(",") if k.strip()}
    return "all" in kinds or (kind or "other") in kinds


def _free_once(model, payload):
    """Один запрос в OpenRouter. Возвращает (status, data, текст ошибки)."""
    global _no_reasoning
    body = dict(payload, model=model)
    body.pop("thinking", None)
    if not _no_reasoning:
        body["reasoning"] = {"enabled": False}
    url = CFG["free_base"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer " + config.OR_KEY}
    status, data, err = post_json(url, body, headers, CFG["free_timeout"])
    if status == 400 and "reasoning" in body and "reasoning" in _why(data, err).lower():
        _no_reasoning = True
        body.pop("reasoning")
        status, data, err = post_json(url, body, headers, CFG["free_timeout"])
    return status, data, err


def _why(data, err) -> str:
    return err or (json.dumps(data, ensure_ascii=False)[:300] if data else "")


def _resting(model, now, shadow=None) -> bool:
    with _lock:
        return (now < _free_off.get("*", 0) or now < _free_off.get(model, 0)
                or now < _shadow_off.get((model, shadow), 0))


def _rest(model, status, why, now, shadow=None) -> bool:
    """Пауза после сбоя. True — следующую модель пробовать бессмысленно.

    `shadow` — вид запроса сверки. Её сбой маршрут не останавливает: тяжёлое
    ранжирование может не пройти там, где разделы проходят, поэтому отдыхает
    только сверка этого вида. Общие для всех — суточный лимит и ключ."""
    forever = float("inf")
    if status == 429 and "per-day" in why.lower():
        with _lock:
            _free_off["*"] = (int(now) // 86400 + 1) * 86400   # ближайшие 00:00 UTC
        _once("per-day:%d" % (int(now) // 86400), logging.INFO,
              "Бесплатный лимит OpenRouter на сутки исчерпан — до 00:00 UTC всё идёт в DeepSeek")
        return True
    if status in (401, 402):
        with _lock:
            _free_off["*"] = forever
        _once("key", logging.WARNING,
              "OpenRouter отказал (HTTP %s: %s) — бесплатный маршрут выключен до "
              "перезапуска, всё идёт в DeepSeek", status, why[:160])
        return True
    if status == 429:
        pause = 60
    elif status in (400, 403, 404):
        # 403 — отказ этой модели («only available on agentic harnesses»),
        # плохой ключ OpenRouter отдаёт 401
        pause = forever
    elif status in (0, 408) or status >= 500:
        pause = CFG["free_pause_min"] * 60
    else:
        return True
    if shadow is not None:
        with _lock:
            _shadow_off[(model, shadow)] = now + pause
        return False
    if pause == forever:
        _once("model:" + model, logging.WARNING,
              "OpenRouter не отдаёт %s (HTTP %s: %s) — уберите её из ND_FREE_MODELS "
              "или разрешите бесплатные эндпоинты в настройках приватности",
              model, status, why[:160])
    with _lock:
        _free_off[model] = now + pause
    return False


def _by_id(data) -> dict:
    rows = {}
    for row in as_list(data):
        if not isinstance(row, dict):
            continue
        try:
            rows[int(row.get("id", -1))] = row
        except (TypeError, ValueError):
            continue
    return rows


def covers(kind, data, expect) -> bool:
    """В ответе есть каждый id из `expect` вместе с обязательным полем."""
    field = REQUIRED.get(kind)
    rows = _by_id(data)
    for idx in expect:
        row = rows.get(int(idx))
        if row is None or (field and row.get(field) is None):
            return False
        if kind in TEXT_KINDS and not str(row.get(field) or "").strip():
            return False
    return True


def _free(payload, kind, expect, check, strict=False):
    """Запрос в бесплатную модель. (данные, usage) или None — тогда в DeepSeek."""
    if not config.OR_KEY:
        if strict:
            raise LLMError("OPENROUTER_API_KEY не задан")
        return None
    got, asked, last = _ask_free(payload, kind, expect, check)
    if got is not None:
        data, used, model = got
        spend.add(kind, used, 0.0, route="free")
        if not strict and audit_due(kind):
            audit_put(kind, payload, data, model)
        return data, used
    if asked:
        spend.fallback(kind)
        log.debug("Бесплатная модель не ответила (%s) — спрашиваю DeepSeek", last)
    if strict:
        raise LLMError("OpenRouter: " + last)
    return None


def _accept(kind, data, expect, check):
    """Ответ OpenRouter с HTTP 200 -> (данные, None) или (None, почему не берём)."""
    try:
        parsed = _loads(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError, LLMError) as exc:
        return None, "ответ не разобрался (%s)" % str(exc)[:120]
    if not covers(kind, parsed, expect):
        return None, "в ответе не все id"
    if check is not None and not check(parsed):
        return None, "ответ не прошёл проверку"
    return parsed, None


def _ask_free(payload, kind, expect, check, shadow=None):
    """Модели из `free_models` по очереди, с паузами после сбоев (`_rest`).

    Возвращает ((данные, usage, модель) или None, спрашивали ли хоть одну, что
    не так). None — ответа, который можно взять в работу, нет. Каждая попытка
    ложится в учёт по моделям (`spend.model`): `status` покажет, какая отвечает."""
    asked, last = False, "все модели на паузе"
    for model in free_models():
        now = time.time()
        if _resting(model, now, shadow):
            continue
        asked = True
        status, data, err = _free_once(model, payload)
        why = _why(data, err)
        if status == 200 and isinstance(data, dict) and not data.get("error"):
            parsed, bad = _accept(kind, data, expect, check)
            if bad is None:
                spend.model(model, answers=1)
                return (parsed, _used(data.get("usage")), model), True, ""
            spend.model(model, rejected=1)
            last = "%s: %s" % (model, bad)
            break
        spend.model(model, errors=1)
        last = "%s: HTTP %s %s" % (model, status, why[:160])
        if status == 200 or _rest(model, status, why, now, shadow):
            break
    return None, asked, last


# ------------------------------------------------- сверка с бесплатной моделью
#: Решать, можно ли отдать бесплатной ранжирование или фактчек, надо по
#: цифрам, а не на глаз. Поэтому ответ DeepSeek по таким видам в фоне
#: сравнивается с ответом бесплатной модели на тот же запрос. В выпуск идёт
#: только DeepSeek; в базу — только счётчики (`spend.shadow`), без текстов.
#:
#: Квота `:free` у сверки и у бесплатного маршрута общая. Маршрут идёт первым:
#: он отвечает на сам запрос, а сверка — уже после DeepSeek, в одной нити, по
#: очереди на четыре места (лишнее выбрасываем: сверка — выборка), не больше
#: `free_shadow_max` в сутки и только пока суточный лимит не исчерпан. Её сбои
#: ставят на паузу только её саму (`_shadow_off`), маршрут работает дальше.
#:
#: Вид, уже отданный бесплатной, сверяется в обратную сторону (`audit_put`):
#: первый и каждый `free_audit`-й принятый ответ переспрашивается у DeepSeek.
#: `covers` проверяет только полноту, а не то, верен ли раздел, — без этого
#: качество бесплатного маршрута после включения не видно никому. Платно, но
#: это один запрос из двадцати; квоту `:free` не трогает.
#:
#: Карточки и перевод так не сравнить — там текст; карточки владелец сравнивает
#: глазами (`tools/freecards.py`).
SHADOW_KINDS = ("rank", "urgency", "dupes", "claims", "classify")

_shadow_jobs = queue.Queue(maxsize=4)
_shadow_thread = None
#: [сутки, сколько запросов сверки за них ушло] — только для нити сверки
_shadow_used = ["", 0]
#: вид -> сколько бесплатных ответов принято за процесс (`audit_due`)
_audit_seen = collections.Counter()


def shadow_wanted(kind) -> bool:
    """Сверять ли этот вид. Тот, что уже идёт бесплатно, — нет: его ответ и так
    проверен механически, а повторять запрос незачем."""
    if not config.OR_KEY or not free_models() or free_wanted(kind):
        return False
    kinds = {k.strip() for k in str(CFG["free_shadow"] or "").split(",") if k.strip()}
    return kind in SHADOW_KINDS and kind in kinds


def audit_wanted(kind) -> bool:
    """Переспрашивать ли у DeepSeek бесплатные ответы этого вида."""
    return (int(CFG["free_audit"] or 0) > 0 and kind in SHADOW_KINDS
            and free_wanted(kind))


def audit_due(kind) -> bool:
    """Этот принятый бесплатный ответ — на выборочную проверку: первый и каждый
    `free_audit`-й. Первый — чтобы цифры появились и после перезапуска."""
    if not audit_wanted(kind) or not config.DS_KEY:
        return False
    with _lock:
        _audit_seen[kind] += 1
        return (_audit_seen[kind] - 1) % int(CFG["free_audit"]) == 0


def _background(job, *args) -> None:
    """Ставит сверку в очередь. Запрос не ждёт ни её, ни места в очереди."""
    global _shadow_thread
    with _lock:
        if _shadow_thread is None or not _shadow_thread.is_alive():
            _shadow_thread = threading.Thread(target=_shadow_loop,
                                              name="free-shadow", daemon=True)
            _shadow_thread.start()
    try:
        _shadow_jobs.put_nowait((job, args))
    except queue.Full:
        pass


def shadow_put(kind, payload, paid, expect) -> None:
    # копия: вызывающий волен менять свой ответ, пока сверка ждёт очереди
    _background(_shadow_one, kind, payload, json.loads(json.dumps(paid)),
                tuple(expect))


def audit_put(kind, payload, free, model) -> None:
    _background(_audit_one, kind, payload, json.loads(json.dumps(free)), model)


def _shadow_loop() -> None:
    while True:
        job, args = _shadow_jobs.get()
        try:
            job(*args)
        except Exception as exc:  # noqa: BLE001 — сверка не повод ронять нить
            log.debug("Сверка с бесплатной моделью не удалась: %s", exc)
        finally:
            _shadow_jobs.task_done()


def _shadow_one(kind, payload, paid, expect) -> None:
    day = spend.today()
    if _shadow_used[0] != day:
        # после перезапуска сутки продолжаются: считанное берём из базы
        _shadow_used[:] = [day, spend.shadow_asks(day)]
    if _shadow_used[1] >= CFG["free_shadow_max"]:
        return
    got, asked, _last = _ask_free(payload, kind, expect, None, shadow=kind)
    if not asked:          # все модели на паузе или суточный лимит исчерпан
        return
    _shadow_used[1] += 1
    if got is None:        # такой ответ маршрут не взял бы: был бы откат
        spend.shadow(kind, asks=1, failed=1)
        return
    items, agree, risky = compare(kind, paid, got[0])
    spend.shadow(kind, asks=1, items=items, agree=agree, risky=risky)
    spend.model(got[2], items=items, agree=agree)


def _audit_one(kind, payload, free, model) -> None:
    """Обратная сверка: бесплатный ответ уже в работе, DeepSeek отвечает на тот
    же запрос. Квоту сверки (`asks`) не трогает — та про лимит `:free`."""
    try:
        paid, _used = _paid(payload, kind)
    except LLMError as exc:
        log.debug("Обратная сверка: DeepSeek не ответил (%s)", exc)
        return
    items, agree, risky = compare(kind, paid, free)
    spend.shadow(kind, items=items, agree=agree, risky=risky)
    spend.model(model, items=items, agree=agree)


def _level(urgency) -> int:
    """Срочность → что с новостью будет: 0 ждёт выпуска, 1 🔔, 2 ⚡."""
    return ((urgency >= CFG["breaking_alert_score"])
            + (urgency >= CFG["breaking_flash_score"]))


def _value(kind, row):
    """Что сверяем в строке ответа. None — сверять нечего."""
    try:
        if kind == "classify":
            # как в `classify.ask_model`: неуверенный ответ — это «раздела нет»
            if float(row.get("confidence") or 0) < 0.5:
                return ""
            return str(row.get("section") or "").strip()
        if kind in ("rank", "urgency"):
            return float(row.get(REQUIRED[kind]))
        return str(row.get(REQUIRED[kind]) or "").strip().lower()
    except (TypeError, ValueError):
        return None


def _agree(kind, mine, theirs) -> bool:
    if kind == "rank":
        return abs(mine - theirs) <= 1
    if kind == "urgency":
        return _level(mine) == _level(theirs)
    return mine == theirs


def _risky(kind, mine, theirs) -> bool:
    """Расхождение меняет выпуск: новость пропала бы или попала бы зря."""
    if kind == "rank":
        return (mine >= CFG["min_score"]) != (theirs >= CFG["min_score"])
    if kind == "urgency":
        return _level(mine) != _level(theirs)
    if kind == "dupes":
        return (mine in NOTHING_NEW) != (theirs in NOTHING_NEW)
    if kind == "claims":
        return (mine == "hold") != (theirs == "hold")
    return False


def compare(kind, paid, free) -> tuple:
    """Ответы DeepSeek и бесплатной модели по id: (сверено, совпало, опасных).

    Сверяем то, на что ответил DeepSeek: чего он не вернул, сравнить не с чем.
    Бесплатная там промолчала или ответила неразборчиво — это расхождение, и
    опасное: такой ответ в выпуске оставил бы дыру.
    """
    theirs = _by_id(free)
    items = agree = risky = 0
    for idx, row in _by_id(paid).items():
        mine = _value(kind, row)
        if mine is None:
            continue
        items += 1
        other = _value(kind, theirs[idx]) if idx in theirs else None
        if other is None:
            risky += 1
            continue
        agree += _agree(kind, mine, other)
        risky += _risky(kind, mine, other)
    return items, agree, risky


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


RANK_SYSTEM = """Ты — редактор ежедневного дайджеста новостей.
Портрет читателя и кандидаты придут следующим сообщением.

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
{"items": [{"id": 0, "score": 8.5, "category": "labs"}]}
Включи ВСЕХ кандидатов, отсортируй по убыванию score."""

BREAKING_SYSTEM = """Ты — выпускающий редактор новостной ленты. Решаешь ровно
один вопрос: может ли это подождать очередного выпуска — или человека нужно
уведомить прямо сейчас.

Это НЕ оценка «интересно ли читателю». Землетрясение важно и тому, кто читает
только про базы данных. Читатель (его портрет придёт следующим сообщением)
влияет лишь на то, считать ли событие отраслевым: для него отрасль — своя,
для остальных та же новость нишевая.

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
{"items": [{"id": 0, "urgency": 9.5, "scope": "global", "category": "policy"}]}
Включи ВСЕХ кандидатов."""

DUP_SYSTEM = """Ты — выпускающий редактор. Читатель уже видел ПЕРВУЮ новость
пары. Про вторую ты решаешь ровно один вопрос: что нового он из неё узнает.

Не «похожи ли тексты» и даже не «одно ли это событие»: одно и то же событие
редакции описывают по десять раз, и девять пересказов читателю не нужны, а
десятый — тот, где счётчик сдвинулся, — нужен обязательно.

Ответ — одно слово в поле news:

  same   то же событие и тот же набор фактов: другой заголовок, другой сайт,
         другой язык, другой объём слов. Нового читателю — ничего.
  less   то же событие, но вторая знает МЕНЬШЕ первой: подробностей в ней
         меньше, счётчик тот же или отстал, а другое число — это другая
         ОЦЕНКА того же самого (270 человек на борту и 260 человек на борту —
         одна и та же лодка, а не две). Нового читателю — ничего.
  more   то же событие, но вторая знает БОЛЬШЕ: счётчик сдвинулся, названо
         то, чего в первой не было вовсе.
  next   событие ДРУГОЕ, но сюжет тот же: причина, следствие, реакция,
         решение, следующий шаг того же дела.
  other  общего только тема, отрасль, страна или герой.

Поле gain — что именно нового, до 8 слов, и только при news=more. У
остальных пусто.

Примеры:
  «Умер актёр N» / «Актёр N, звезда фильма X, скончался в 80 лет» — same
  «Умер актёр N» / «Коллеги прощаются с N» — less (повод тот же, фактов меньше)
  «Умер актёр N» / «Названа причина смерти N» — next
  «Лодка с 270 людьми перевернулась у Кипра, 159 найдены»
      / «Паром с 260 пассажирами затонул у берегов Кипра» — less
      (то же крушение, 260 и 270 — две оценки, про спасённых ни слова)
  «Лодка с 270 людьми перевернулась у Кипра, 159 найдены»
      / «У Кипра найдены 200 из 270 пассажиров» — more, gain «найдено 200 из 270»
  «Землетрясение M7 у берегов Японии» / «Землетрясение в Японии: 200 погибших»
      — more, gain «200 погибших»
  «X покупает Y» / «X закрыла сделку по покупке Y за $3 млрд» — same
  «Nvidia отчиталась за квартал» / «Акции Nvidia упали на 8%» — next
  «Nvidia отчиталась за квартал» / «Nvidia показала новую видеокарту» — other

Где сомневаешься, там правило простое: между more и less выбирай more, между
next и same выбирай next. Показать читателю лишнее не страшно, отнять у него
новость — страшно. Но пересказ, в котором ты не можешь назвать НИ ОДНОГО
нового факта, — это не more: если поле gain нечем заполнить, это less.

Ответь ТОЛЬКО валидным json вида:
{"items": [{"id": 0, "news": "less", "gain": ""}]}
Ответь про КАЖДУЮ пару."""


CLAIM_SYSTEM = """Ты — редактор отдела проверки. Про каждую новость решаешь
НЕ «правда ли это». Проверить факт ты не можешь: твои знания устарели, а
сегодняшнего события ты не видел. Врать про это себе нельзя — цена ошибки
здесь в том, что читатель новость не увидит.

Ты решаешь другое: КАК ЗАЯВЛЕНИЕ ПОДАНО. Это видно из текста перед тобой,
и знать сегодняшние новости для этого не нужно.

Смотри на три вещи:

1. АДРЕС. У настоящего исследования он есть: назван журнал или препринт,
   назван институт или автор, сказано, что именно измеряли. «Учёные
   выяснили», «эксперты уверены», «исследование показало» без единого имени
   — это заявление без адреса, проверить его нельзя никому.

2. ЗАПРОС БОЛЬШЕ ОБЕСПЕЧЕНИЯ. Самый частый вид научного фейка — не выдумка,
   а раздутый пересказ настоящей работы: в статье «у 12 мышей замедлилось
   деление клеток», в заголовке «найдено лекарство от рака». Обещает ли
   заголовок больше, чем сказано в тексте.

3. ВЕЧНЫЙ ЖАНР. Есть заявления, которые возвращаются каждый год и каждый раз
   оказываются ничем: вечный двигатель, свободная энергия, опровержение
   Эйнштейна, сверхсветовая связь, единственное лекарство от рака,
   «NASA подтвердило существование», «власти скрывают», сигнал от
   инопланетян, вред 5G и чипирование. Узнаёшь жанр — скажи.

verdict:
  ok      заявление с адресом: названы работа, институт, измеримый результат.
          Обычная новость без научных претензий — тоже ok
  caveat  публиковать можно, но читателю нужна оговорка: препринт без
          рецензирования, единственный источник, заголовок шире текста,
          предварительные данные, малая выборка
  hold    публиковать рано: заявление без адреса вовсе, узнаваемый вечный
          жанр, обещание, которого не бывает, откровенный рекламный текст
          под видом исследования

note — оговорка для читателя на русском, до 8 слов, в именительном:
«препринт, без рецензирования», «данные одной лаборатории», «заголовок шире
исследования». Для ok — пустая строка.

Осторожность здесь несимметрична, и это важно. `hold` — это «читатель не
увидит», поэтому ставь его только когда текст сам себя выдаёт. Скучная
новость, которая тебе не нравится, — это ok. Тема, в которой ты не
разбираешься, — это ok. Незнакомый институт — это ok: мир большой.

Ответь ТОЛЬКО валидным json вида:
{"items": [{"id": 0, "verdict": "ok", "note": ""}]}
Ответь про КАЖДУЮ новость."""


SUM_SYSTEM = """Ты пишешь карточки новостей для ежедневного дайджеста.
Портрет читателя и язык ответа придут следующим сообщением.

Правила:
- ВСЕ три поля пиши на ЯЗЫКЕ ОТВЕТА, даже если источник на другом языке:
  заголовок переводится наравне с текстом, оставлять его как в источнике нельзя;
- имена, названия компаний, продуктов и версии сохраняй в оригинальном
  написании (Nvidia, Linux 7.2, GPT-5), термины переводи;
- пиши СВОИМИ СЛОВАМИ, не копируй фразы из источника;
- во входном тексте может лежать несколько сообщений об ОДНОМ событии из
  разных редакций (каждое помечено [источником]). Это одна новость, и карточка
  у неё одна: возьми подробности из всех — то, что есть только у второй
  редакции, должно попасть в карточку. Одно и то же дважды не пересказывай;
  редакции противоречат друг другу — пиши то, в чём они сходятся;
- никаких фактов и цифр, которых нет во входном тексте, не додумывай;
- если деталей мало — пиши короче, это нормально;
- без воды и оборотов вроде «в мире произошло знаковое событие».

Ответь ТОЛЬКО валидным json вида:
{"items": [{"id": 0,
  "headline": "заголовок до 70 символов",
  "what": "что произошло, 1-2 предложения",
  "why": "почему это важно — следствие, а не пересказ, 1 предложение"}]}
Верни карточку для КАЖДОГО входного id."""

TR_SYSTEM = """Ты переводишь новостной дайджест.
Язык перевода придёт следующим сообщением.

Правила:
- переводи смысл, а не слова: строка должна читаться так, будто её сразу
  написали на языке перевода;
- имена людей, названия компаний, продуктов, версий и тикеры оставляй в
  оригинальном написании (Nvidia, Linux 7.2, GPT-5), остальное переводи;
- ничего не добавляй, не выбрасывай и не сокращай: это перевод, а не пересказ;
- если строка уже на нужном языке, верни её без изменений.

Ответь ТОЛЬКО валидным json вида:
{"items": [{"id": 0, "text": "перевод"}]}
Верни перевод для КАЖДОГО входного id."""


def rank_clusters(clusters, persona):
    payload = []
    for idx, group in enumerate(clusters):
        main = primary_of(group)
        payload.append({"id": idx, "title": main["title"],
                        "lead": lead_of(main["title"], main["summary"]),
                        "source": main["source_id"],
                        "confirmations": len({i["source_id"] for i in group})})
    data, usage = llm_json(
        RANK_SYSTEM, task("Кандидаты", payload, "Читатель: " + persona),
        CFG["model_rank"], max_tokens=3000, kind="rank",
        expect=[p["id"] for p in payload])
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
                        "lead": lead_of(main["title"], main["summary"]),
                        "source": main["source_id"],
                        "confirmations": len({i["source_id"] for i in group})})
    data, usage = llm_json(
        BREAKING_SYSTEM, task("Кандидаты", payload, "Читатель: " + persona),
        CFG["model_rank"], max_tokens=2000, kind="urgency",
        expect=[p["id"] for p in payload])
    return as_list(data), usage


#: Что вторая новость пары даёт читателю, который видел первую. Шкала одна, и
#: идёт она от «ничего» к «другое событие»: SAME и LESS показывать нечего, всё
#: остальное — можно.
#:
#: LESS появился не для красоты. Одно и то же крушение приходит из пяти лент
#: в течение часа, и пятый пересказ обычно короче первого: другая оценка того
#: же числа, ни слова о том, что было дальше. Вопрос «одно ли это событие»
#: такой пересказ пропускал: формально во второй заметке «лодка затонула»
#: против «лодка перевернулась» — это ведь развитие. Вопрос «что нового узнает
#: читатель» на него отвечает честно: ничего, у него отняли даже спасённых.
SAME, LESS, MORE, NEXT, OTHER = "same", "less", "more", "next", "other"

#: Показывать нечего: читатель это уже знает, и знает подробнее.
NOTHING_NEW = (SAME, LESS)

#: Ответ про пару. `same` решает, показывать ли вторую новость вообще;
#: `follows` — та же пара глазами читателя, который первую уже видел: сюжет
#: тот же. Оба поля выводятся из `kind` и остаются потому, что ими думает
#: остальной код: отбор — про `same`, «Ранее по теме» — про `follows`.
#: `gain` — чем именно вторая новость дополняет первую (до 8 слов), и он
#: пустой у всех, кроме MORE: там, где нового нет, называть нечего.
Verdict = collections.namedtuple("Verdict", "same follows kind gain",
                                 defaults=("", ""))


def verdict_of(kind, gain="") -> Verdict:
    """Вердикт по ответу модели. Неизвестное слово — как молчание: пара
    остаётся неразобранной, а новость идёт в выпуск."""
    kind = str(kind or "").strip().lower()
    if kind not in (SAME, LESS, MORE, NEXT, OTHER):
        return Verdict(False, False, "", "")
    # у дубля продолжения не бывает: это одна и та же новость, и ставить её в
    # цепочку саму за собой незачем
    return Verdict(kind in NOTHING_NEW, kind in (MORE, NEXT), kind,
                   str(gain or "").strip()[:80] if kind == MORE else "")


def judge_duplicates(pairs):
    """«Что нового во второй новости для того, кто видел первую?» пачкой.

    Возвращает ({номер пары: Verdict}, usage). Пара — (что читатель уже видел,
    что просится в выпуск). Ответа про пару может и не быть: чего модель не
    вернула, то остаётся неразобранным, и новость идёт в выпуск — молчание не
    повод её выбросить.

    Вопрос один на всё: и повтор, и урезанный пересказ, и продолжение сюжета
    выходят из одного поля `news`. Пара и так перед моделью — различить эти
    случаи не стоит ни лишнего запроса, ни лишних денег.
    """
    payload = [{"id": idx, "a": str(a)[:300], "b": str(b)[:300]}
               for idx, (a, b) in enumerate(pairs)]
    data, usage = llm_json(
        DUP_SYSTEM, task("Пары", payload),
        # ответ подрос на `gain`: короткая фраза «что нового» вместо
        # второго булева поля
        CFG["model_rank"], max_tokens=64 * len(payload) + 400,
        kind="dupes", expect=[p["id"] for p in payload])
    out = {}
    for entry in as_list(data):
        try:
            idx = int(entry.get("id", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(pairs):
            out[idx] = verdict_of(entry.get("news"), entry.get("gain"))
    return out, usage


def judge_claims(claims):
    """«Как подано заявление» пачкой. Возвращает ({номер: (вердикт, оговорка)}, usage).

    Вопрос модели задаётся не про истинность, а про форму заявления — почему
    именно так, подробно написано в `factcheck`. Ответа про новость может и
    не быть: чего модель не вернула, то остаётся неразобранным, и новость
    идёт в выпуск по бесплатным слоям — молчание не повод её придержать.
    """
    payload = [dict(claim, id=idx) for idx, claim in enumerate(claims)]
    data, usage = llm_json(
        CLAIM_SYSTEM, task("Новости", payload),
        CFG["model_rank"], max_tokens=80 * len(payload) + 500,
        kind="claims", expect=[p["id"] for p in payload])
    out = {}
    for entry in as_list(data):
        try:
            idx = int(entry.get("id", -1))
        except (TypeError, ValueError):
            continue
        verdict = str(entry.get("verdict") or "").strip().lower()
        if 0 <= idx < len(claims) and verdict in ("ok", "caveat", "hold"):
            out[idx] = (verdict, str(entry.get("note") or "").strip()[:80])
    return out, usage


def card_text(group) -> str:
    """Текст события для карточки: голоса кластера подряд, каждый со своим
    источником. Он же — ключ кэша карточек, поэтому собирается одним местом."""
    return " ".join("[%s] %s. %s" % (i["source_id"], i["title"], lead_of(i["title"], i["summary"], 350))
                    for i in voices(group))[:1800]


def card_key(persona: str, language: str, text: str) -> str:
    """Ключ карточки: то же событие теми же словами, тот же читатель, тот же
    язык — значит, и карточка будет та же. Спрашивать второй раз нечего."""
    seed = "\n".join((str(persona), str(language), text))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def summarize_batch(picked, persona, language, offset=0, route="auto"):
    """Карточки для одной пачки новостей. Ключи ответа — индексы в picked."""
    payload = []
    for idx, (group, _score, _cat) in enumerate(picked, offset):
        payload.append({"id": idx, "text": card_text(group)})
    data, usage = llm_json(
        SUM_SYSTEM, task("Новости", payload, "Читатель: " + persona,
                         "Язык ответа: " + language),
        CFG["model_summary"], max_tokens=400 * len(payload) + 500,
        kind="cards", expect=[p["id"] for p in payload], route=route)
    cards = {}
    for card in as_list(data):
        try:
            cards[int(card.get("id", -1))] = card
        except (TypeError, ValueError):
            continue
    return cards, usage


def translate_texts(texts, language, check=None, route="auto"):
    """Перевод пачки строк одним запросом. Возвращает ({номер: строка}, usage).

    Номер — позиция строки во входном списке: так ответ раскладывается обратно
    даже когда модель вернула не все строки или перепутала их порядок.
    `check(исходная, перевод)` проверяет бесплатный ответ построчно: хоть одна
    строка не прошла — переводит DeepSeek.
    """
    payload = [{"id": idx, "text": str(text)[:600]}
               for idx, text in enumerate(texts)]
    whole = None
    if check is not None:
        def whole(data):
            return all(check(texts[idx], text)
                       for idx, text in _translations(data, texts).items())
    data, usage = llm_json(
        TR_SYSTEM, task("Строки", payload, "Язык перевода: " + language),
        CFG["model_summary"], max_tokens=300 * len(payload) + 500,
        kind="translate", expect=[p["id"] for p in payload], check=whole,
        route=route)
    return _translations(data, texts), usage


def _translations(data, texts) -> dict:
    out = {}
    for row in as_list(data):
        try:
            idx = int(row.get("id", -1))
        except (TypeError, ValueError, AttributeError):
            continue
        text = str(row.get("text") or "").strip()
        if 0 <= idx < len(texts) and text:
            out[idx] = text
    return out


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
