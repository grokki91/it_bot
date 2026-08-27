# -*- coding: utf-8 -*-
"""Второй слой дедупликации: одно ли это событие — решает модель.

Первый слой — слова (`textutil.sim_sets`). У пересказа одной новости общих
слов много, и порог `similarity` сводит такие заметки в один кластер. Этого
хватает, пока обе редакции пишут об одном и том же примерно одинаково.

Не хватает, как только вторая заходит с другой стороны:

    «Тим Карри, звезда «Шоу ужасов Рокки Хоррора», умер в 80 лет»
    «Коллеги, включая Кэрол Бернетт и Люка Эванса, прощаются с Тимом Карри»

Общее слово здесь ровно одно — «карри»; совпадение 0.08 при пороге 0.32. По
словам это две разные новости, и читатель получает одно и то же дважды:
вечером в выпуске, ночью — срочным.

Поэтому спорную зону — совпадение ниже `similarity`, но не ниже `dup_gray` —
разбирает модель. Вопрос ей задаётся ровно один: это одно и то же событие?
Не «похожи ли тексты», а «узнает ли читатель из второй заметки то, чего не
было в первой». Причина смерти, число жертв, реакция властей, решение суда —
это уже следующая новость, и приходить она должна.

Порог `dup_gray` низкий (0.05 — практически «есть хоть одно общее слово»),
иначе случай выше в него не попадает. Спорных пар при таком пороге много,
поэтому спрашиваем не про все:

    * проверяем только верхушку каждого раздела (`dup_candidates`) — в выпуск
      идут один-два кандидата, и платить за хвост незачем;
    * в истории смотрим назад на `dup_window_h` — дальше это уже не повтор,
      а возвращение к теме;
    * пары взвешиваем по редкости общих слов (`weigh`): «карри» и «нвидиа»
      весят много, «новый» и «компания» — почти ничего. Спрашиваем про самые
      весомые, не больше `dup_llm_max` за прогон;
    * вердикт оседает в таблице `dupes`. Ключ — пара сигнатур, поэтому ответ
      переживает пересборку выпуска и годится для всех подписчиков сразу.

Найденное применяется по-разному, и это важно:

    * совпало с ИСТОРИЕЙ — кандидат выбрасывается сразу: читатель это уже
      видел, второй раз показывать нечего;
    * совпали два КАНДИДАТА одного выпуска — никто не выбрасывается. Пара
      просто связывается, и второй отпадёт, только если первый действительно
      попадёт в выпуск. Иначе выброшенным оказался бы тот, кого никто не
      показал, — а это уже потерянная новость.

Модель недоступна или выключена (`ND_DUP_LLM=0`) — работает первый слой,
ровно как работал раньше.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone

from .config import CFG, log, now_iso
from .llm import LLMError, judge_duplicates, llm_cost
from .rank import primary_of, story
from .textutil import sim_sets


def pair_key(sig_a: str, sig_b: str) -> str:
    """Ключ пары. Порядок не важен: «А и Б» — тот же вопрос, что «Б и А»."""
    first, second = sorted([str(sig_a or ""), str(sig_b or "")])
    return hashlib.sha256(("%s\n%s" % (first, second)).encode("utf-8")).hexdigest()[:32]


def gray(score: float) -> bool:
    """Спорная зона: слова уже что-то говорят, но на приговор не тянут."""
    return float(CFG["dup_gray"]) <= score < float(CFG["similarity"])


def enabled() -> bool:
    return bool(CFG["dup_llm"]) and float(CFG["dup_gray"]) < float(CFG["similarity"])


def cutoff() -> str:
    """С какого момента повтор ещё воспринимается как повтор."""
    return (datetime.now(timezone.utc)
            - timedelta(hours=max(1, int(CFG["dup_window_h"])))).isoformat()


# ------------------------------------------------------------- вес общих слов
def idf(docs) -> dict:
    """Насколько каждое слово редкое. Слово из одной новости весит много,
    слово из каждой второй — почти ничего.

    Это не про то, дубль перед нами или нет: это про то, о чём СТОИТ
    спрашивать. Общая «карри» — веский повод, общая «компания» — нет.
    """
    total = max(1, len(docs))
    seen = {}
    for words in docs:
        for word in words:
            seen[word] = seen.get(word, 0) + 1
    return {word: math.log(total / float(count) + 1.0)
            for word, count in seen.items()}


def weigh(a: set, b: set, weights: dict) -> float:
    """Вес пары: сколько редкого у них общего. 0 — общего нет вовсе.

    Именно сумма, а не доля: доля тянет вверх короткие заголовки, у которых
    общее слово — «компания», и топит длинные, у которых общее слово —
    фамилия. Спрашивать надо про вторые.
    """
    return sum(weights.get(word, 1.0) for word in a & b)


# --------------------------------------------------------------- кэш вердиктов
def cached(conn, keys) -> dict:
    """Что уже спрашивали раньше: ключ пары -> True/False."""
    out = {}
    keys = [k for k in dict.fromkeys(keys) if k]
    for start in range(0, len(keys), 400):       # SQLite не любит длинные IN
        part = keys[start:start + 400]
        marks = ",".join("?" * len(part))
        for row in conn.execute(
                "SELECT pair, same FROM dupes WHERE pair IN (%s)" % marks, part):
            out[row["pair"]] = bool(row["same"])
    return out


def remember(conn, verdicts) -> None:
    """Кладёт ответы модели в кэш: за тот же вопрос второй раз не платим."""
    rows = [(key, 1 if same else 0, now_iso()) for key, same in verdicts]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO dupes(pair, same, at) VALUES (?,?,?) "
        "ON CONFLICT(pair) DO UPDATE SET same=excluded.same, at=excluded.at", rows)
    conn.commit()


# --------------------------------------------------------------------- вопрос
def ask(conn, questions):
    """Разбирает спорные пары. Возвращает ({ключ пары: bool}, стоимость).

    `questions` — список (ключ, текст того, что читатель видел, текст
    кандидата), самые весомые первыми. Что нашлось в кэше, о том не
    спрашиваем; на остальное тратим не больше `dup_llm_max` вопросов.
    """
    known = cached(conn, [key for key, _seen, _new in questions])
    limit = max(0, int(CFG["dup_llm_max"]))
    pending = [q for q in questions if q[0] not in known][:limit]
    if not pending:
        return known, 0.0

    try:
        answers, usage = judge_duplicates([(seen, new) for _key, seen, new in pending])
    except LLMError as exc:
        # не беда: остаётся первый слой — ровно то, что было до этой проверки
        log.warning("Проверка дублей не удалась (%s) — сужу по словам", exc)
        return known, 0.0

    fresh = {pending[idx][0]: same for idx, same in answers.items()}
    remember(conn, fresh.items())
    known.update(fresh)
    log.info("Дубли: спрошено пар %d, из них повторов %d", len(pending),
             sum(1 for same in fresh.values() if same))
    return known, llm_cost(usage)


def words_of(group) -> set:
    """Все содержательные слова кластера — по ним и взвешиваем пару."""
    out = set()
    for item in group:
        out.update(item["sig"].split())
    return out


def question(key, seen_text, group):
    """Пара для модели: что читатель видел — и что просится в выпуск."""
    main = primary_of(group)
    return key, seen_text, story(main["title"], main.get("summary"))


# --------------------------------------------------------------- точка входа
def top_of(shortlists) -> list:
    """Верхушка каждого раздела — кандидаты, у которых есть шанс попасть в
    выпуск. Списки уже отсортированы прескорингом, так что это просто срез."""
    limit = max(1, int(CFG["dup_candidates"]))
    return [group for _topic, groups in shortlists for group in groups[:limit]]


def prune(conn, index, shortlists) -> float:
    """Убирает из кандидатов выпуска то, что читатель уже видел по сути.

    Правит списки кандидатов на месте, возвращает стоимость запроса. Зовётся
    один раз на выпуск — после того, как разделы набрали кандидатов, и до
    того, как модель начала их ранжировать: платить за ранжирование повтора
    незачем.
    """
    flat = top_of(shortlists)
    if not flat:
        return 0.0

    since = cutoff()
    doubles, questions, links = set(), [], {}
    tokens = {id(group): words_of(group) for group in flat}
    weights = idf(list(tokens.values()) + index.words)

    # 1. против истории. Совпало по словам — выбрасываем без вопросов; попало
    # в спорную зону и было на днях — спросим модель
    seen_keys = {}
    for group in flat:
        score, sig, text, at = index.near(group)
        if score >= CFG["similarity"]:
            doubles.add(id(group))
        elif enabled() and gray(score) and at >= since:
            key = pair_key(sig, primary_of(group)["sig"])
            seen_keys.setdefault(key, []).append(group)
            questions.append((weigh(tokens[id(group)], set(sig.split()), weights),
                              question(key, text, group)))

    # 2. кандидаты между собой. Внутри раздела их уже развела кластеризация,
    # так что сюда доходит спорная зона — и разные разделы, где одно событие
    # приходит под двумя вывесками
    if enabled():
        rest = [group for group in flat if id(group) not in doubles]
        for at, first in enumerate(rest):
            for second in rest[at + 1:]:
                mine, theirs = tokens[id(first)], tokens[id(second)]
                if not gray(sim_sets(mine, theirs)):
                    continue
                head, tail = primary_of(first), primary_of(second)
                key = pair_key(head["sig"], tail["sig"])
                links[key] = (head["sig"], tail["sig"])
                questions.append(
                    (weigh(mine, theirs, weights),
                     question(key, story(head["title"], head.get("summary")),
                              second)))

    cost = 0.0
    if questions:
        # самые весомые спрашиваем первыми: если лимит вопросов кончится,
        # кончится он на парах со случайным общим словом, а не на однофамильцах
        questions.sort(key=lambda q: -q[0])
        verdicts, cost = ask(conn, [pair for _weight, pair in questions])
        for key, groups in seen_keys.items():
            if verdicts.get(key):
                doubles.update(id(group) for group in groups)
        for key, (sig_a, sig_b) in links.items():
            if verdicts.get(key):
                index.link(sig_a, sig_b)

    if not doubles:
        return cost
    dropped = 0
    for at, (topic, groups) in enumerate(shortlists):
        keep = [g for g in groups if id(g) not in doubles]
        dropped += len(groups) - len(keep)
        shortlists[at] = (topic, keep)
    log.info("Дедупликация: снято кандидатов %d — читатель это уже видел", dropped)
    return cost


def confirm_new(conn, index, groups):
    """То же для срочного: (кандидаты, которых читатель не видел, стоимость).

    Кандидатов здесь единицы, зато цена ошибки выше: срочное приходит
    отдельным сообщением и со звуком, и повторить им вечерний выпуск — худшее,
    что бот может сделать.
    """
    if not groups:
        return groups, 0.0

    since = cutoff()
    tokens = {id(group): words_of(group) for group in groups}
    weights = idf(list(tokens.values()) + index.words)
    fresh, questions = [], []
    for group in groups:
        score, sig, text, at = index.near(group)
        if score >= CFG["similarity"]:
            continue                        # по словам это уже уходило
        if enabled() and gray(score) and at >= since:
            key = pair_key(sig, primary_of(group)["sig"])
            questions.append((weigh(tokens[id(group)], set(sig.split()), weights),
                              question(key, text, group)))
            fresh.append((group, key))
        else:
            fresh.append((group, ""))

    cost, verdicts = 0.0, {}
    if questions:
        questions.sort(key=lambda q: -q[0])
        verdicts, cost = ask(conn, [pair for _weight, pair in questions])
    out = []
    for group, key in fresh:
        if key and verdicts.get(key):
            index.mark(primary_of(group)["sig"])
            log.info("Срочное отменено, это уже уходило: %s",
                     primary_of(group)["title"][:70])
            continue
        out.append(group)
    return out, cost
