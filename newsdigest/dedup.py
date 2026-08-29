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
    * пары выстраиваем в очередь и спрашиваем не больше `dup_llm_max` за
      прогон, пачками по `dup_batch` (сотня пар одним куском упирается в
      потолок ответа, и обрыв стоил бы всех вердиктов сразу). Сначала те
      пары, что дадут дубль прямо сейчас (`NOW`), внутри очереди — по
      редкости общих слов (`weigh`): «карри» и «нвидиа» весят много,
      «новый» и «компания» — почти ничего;
    * вердикт оседает в таблице `dupes`. Ключ — пара сигнатур, поэтому ответ
      переживает пересборку выпуска и годится для всех подписчиков сразу.

Найденное применяется по-разному, и это важно:

    * совпало с ИСТОРИЕЙ — кандидат выбрасывается сразу: читатель это уже
      видел, второй раз показывать нечего;
    * совпали два кандидата ОДНОГО РАЗДЕЛА — они склеиваются в один кластер
      (`fuse`). Ничего не выбрасывается: карточку пишет модель по материалам
      кластера, и после склейки в неё идут обе заметки сразу. Читатель получает
      один блок, но подробностей в нём больше, чем было в каждой заметке
      по отдельности;
    * совпали кандидаты РАЗНЫХ РАЗДЕЛОВ — они только связываются, и второй
      отпадёт, лишь если первый действительно попадёт в выпуск. Склеить их
      значило бы решить за отбор, в каком разделе новости жить; не попади она
      в первый — из второго её уже никто бы не показал.

Склейка появилась не от хорошей жизни. Связывание работает только между
разделами: внутри одного список кандидатов фильтруется один раз, ДО отбора, —
и связанная пара доезжала до выпуска целиком. Так две заметки об усилении
Эль-Ниньо на Галапагосах пришли в «Климат» одним выпуском, в 9:03, одна за
другой. Причём выбросить одну из них было бы жалко: у той, что подробнее,
ниже оценка, а у той, что с оценкой, — три строки текста. Склейка снимает
и то, и другое разом.

Модель недоступна или выключена (`ND_DUP_LLM=0`) — работает первый слой,
ровно как работал раньше.
"""
from __future__ import annotations

import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .config import CFG, log, now_iso
from .llm import LLMError, Verdict, judge_duplicates, llm_cost
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
#: Ответа про пару не было — ни в кэше, ни от модели. Ведём себя как до всей
#: проверки: событие считаем новым, сюжета не знаем.
UNKNOWN = Verdict(False, False)


def cached(conn, keys) -> dict:
    """Что уже спрашивали раньше: ключ пары -> Verdict."""
    out = {}
    keys = [k for k in dict.fromkeys(keys) if k]
    for start in range(0, len(keys), 400):       # SQLite не любит длинные IN
        part = keys[start:start + 400]
        marks = ",".join("?" * len(part))
        for row in conn.execute(
                "SELECT pair, same, follows FROM dupes WHERE pair IN (%s)" % marks,
                part):
            out[row["pair"]] = Verdict(bool(row["same"]), bool(row["follows"]))
    return out


def remember(conn, verdicts) -> None:
    """Кладёт ответы модели в кэш: за тот же вопрос второй раз не платим."""
    rows = [(key, 1 if v.same else 0, 1 if v.follows else 0, now_iso())
            for key, v in verdicts]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO dupes(pair, same, follows, at) VALUES (?,?,?,?) "
        "ON CONFLICT(pair) DO UPDATE SET same=excluded.same, "
        "follows=excluded.follows, at=excluded.at", rows)
    conn.commit()


# --------------------------------------------------------------------- вопрос
def ask(conn, questions):
    """Разбирает спорные пары. Возвращает ({ключ пары: Verdict}, стоимость).

    `questions` — список (ключ, текст того, что читатель видел, текст
    кандидата), самые весомые первыми. Что нашлось в кэше, о том не
    спрашиваем; на остальное тратим не больше `dup_llm_max` вопросов.
    """
    known = cached(conn, [key for key, _seen, _new in questions])
    limit = max(0, int(CFG["dup_llm_max"]))
    pending = [q for q in questions if q[0] not in known][:limit]
    if not pending:
        return known, 0.0

    size = max(1, int(CFG["dup_batch"]))
    parts = [pending[at:at + size] for at in range(0, len(pending), size)]

    def one(part):
        try:
            return part, judge_duplicates([(seen, new) for _key, seen, new in part])
        except LLMError as exc:
            # не беда: по этим парам остаётся первый слой — ровно то, что было
            # до всей проверки. Остальные пачки от этого не страдают
            log.warning("Проверка дублей не удалась (%s) — %d пар сужу по словам",
                        exc, len(part))
            return part, None

    if len(parts) == 1:
        results = [one(parts[0])]
    else:
        # пачки — это ожидание сети, и последовательно они складываются в
        # полминуты к сборке выпуска
        with ThreadPoolExecutor(max_workers=min(len(parts), 4)) as pool:
            results = list(pool.map(one, parts))

    fresh, cost, lost = {}, 0.0, 0
    for part, answer in results:
        if answer is None:
            lost += len(part)
            continue
        answers, usage = answer
        cost += llm_cost(usage)
        fresh.update({part[idx][0]: verdict for idx, verdict in answers.items()})
    if not fresh:
        return known, cost

    remember(conn, fresh.items())
    known.update(fresh)
    log.info("Дубли: спрошено пар %d, из них повторов %d, продолжений %d%s",
             len(pending) - lost,
             sum(1 for v in fresh.values() if v.same),
             sum(1 for v in fresh.values() if v.follows),
             "" if not lost else ", про %d ответа не пришло" % lost)
    return known, cost


#: очередь вопроса. Спорных пар в выпуске из полутора десятков разделов
#: набирается под сотню, а `dup_llm_max` разрешает спросить про три десятка —
#: значит, порядок вопросов решает, какие дубли мы вообще увидим.
#:
#: Одного веса общих слов тут мало: он говорит, насколько пара подозрительная,
#: но ничего не говорит о том, чем обернётся ошибка. А оборачивается она
#: по-разному.
#:
#: NOW — пара, которая прямо сейчас даст читателю два одинаковых блока: повтор
#: вчерашнего выпуска и два кандидата ОДНОГО раздела (их склеивают на месте).
#: LATER — кандидаты разных разделов: их не склеивают, а связывают, и второй
#: отпадёт, только если первый попадёт в выпуск, — до дубля дело доходит редко.
#:
#: Таких пар при этом на порядок больше: в выпуске из шестнадцати разделов на
#: восемь пар внутри разделов приходится под сотню межразделных. По одному весу
#: они вытесняли из оплаченных вопросов ровно то, ради чего вопрос задаётся.
NOW, LATER = 0, 1


# ------------------------------------------------------------------- склейка
def fuse(host, guest) -> None:
    """Вливает один кластер в другой: событие остаётся одно, материалов у него
    становится больше.

    Дальше кластер живёт как обычный: `rank.primary_of` выбирает ему лицо (а
    значит, и ссылку в карточке), `rank.voices` — по каким заметкам писать
    текст, `llm.summarize_batch` собирает из них одну карточку. Поэтому склейка
    и не теряет ничего: то, что было только у второй редакции, попадает в тот
    же абзац, что и остальное.
    """
    known = {item["url_hash"] for item in host}
    host.extend(item for item in guest if item["url_hash"] not in known)


class Fusion:
    """Кто в кого влит.

    Склейки идут цепочкой: сначала выясняется, что А и Б — одно событие, потом
    что Б и В — тоже. Вливать В надо уже в А, иначе кластер Б, которого в
    выпуске больше нет, унесёт материал с собой.
    """

    def __init__(self):
        self.host = {}                      # id(кластера) -> кластер-приёмник

    def root(self, group):
        """Кластер, в котором этот материал теперь лежит."""
        while id(group) in self.host:
            group = self.host[id(group)]
        return group

    def join(self, first, second):
        """Склеивает два кластера. Возвращает влитый — тот, кого из выпуска
        теперь убирают. None — эти двое уже были одним кластером.

        Влитый — не всегда `second`: цепочка А—Б, потом Б—В сводит в один
        кластер все три, и убрать из выпуска надо того, в ком материалы уже
        не лежат, а не того, кого назвали в паре.
        """
        host, guest = self.root(first), self.root(second)
        if host is guest:
            return None
        fuse(host, guest)
        self.host[id(guest)] = host
        return guest


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
    """Верхушка каждого раздела: пары (номер раздела, кандидат).

    Это те кандидаты, у которых есть шанс попасть в выпуск; списки уже
    отсортированы прескорингом, так что это просто срез. Номер раздела нужен
    дальше: одинаковые кандидаты одного раздела склеиваются, разных —
    связываются (см. `prune`).
    """
    limit = max(1, int(CFG["dup_candidates"]))
    return [(at, group)
            for at, (_topic, groups) in enumerate(shortlists)
            for group in groups[:limit]]


def prune(conn, index, shortlists) -> float:
    """Разбирает кандидатов выпуска: виденное убирает, одинаковое склеивает.

    Правит списки кандидатов на месте, возвращает стоимость запроса. Зовётся
    один раз на выпуск — после того, как разделы набрали кандидатов, и до
    того, как модель начала их ранжировать: платить за ранжирование повтора
    незачем, а склеенная пара и карточку получит одну на двоих.
    """
    pairs = top_of(shortlists)
    if not pairs:
        return 0.0

    flat = [group for _at, group in pairs]
    home = {id(group): at for at, group in pairs}   # кандидат -> его раздел

    since = cutoff()
    doubles, questions, couples = set(), [], []
    tokens = {id(group): words_of(group) for group in flat}
    weights = idf(list(tokens.values()) + index.words)

    # 1. против истории. Совпало по словам — выбрасываем без вопросов; попало
    # в спорную зону и было на днях — спросим модель
    seen_keys, seen_sig = {}, {}
    for group in flat:
        score, sig, text, at = index.near(group)
        if score >= CFG["similarity"]:
            doubles.add(id(group))
        elif enabled() and gray(score) and at >= since:
            key = pair_key(sig, primary_of(group)["sig"])
            seen_keys.setdefault(key, []).append(group)
            seen_sig[key] = sig
            questions.append((NOW, weigh(tokens[id(group)], set(sig.split()), weights),
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
                head = primary_of(first)
                key = pair_key(head["sig"], primary_of(second)["sig"])
                couples.append((key, first, second))
                questions.append(
                    (NOW if home[id(first)] == home[id(second)] else LATER,
                     weigh(mine, theirs, weights),
                     question(key, story(head["title"], head.get("summary")),
                              second)))

    cost, fused = 0.0, set()
    if questions:
        # сначала то, что даст дубль прямо сейчас, и внутри очереди — самые
        # весомые: лимит вопросов кончится на парах со случайным общим словом,
        # а не на однофамильцах
        questions.sort(key=lambda q: (q[0], -q[1]))
        verdicts, cost = ask(conn, [pair for _rush, _weight, pair in questions])
        for key, groups in seen_keys.items():
            verdict = verdicts.get(key, UNKNOWN)
            if verdict.same:
                doubles.update(id(group) for group in groups)
            elif verdict.follows:
                # событие другое, а сюжет тот же: читателю это не повтор, а
                # продолжение — и знать, с чего оно началось, ему полезно
                prior = index.hash_of(seen_sig.get(key, ""))
                for group in groups:
                    index.follow(group, prior)

        # сначала склейки внутри разделов: после них у кластера может смениться
        # лицо, а связывать разделы надо уже по новому
        fusion = Fusion()
        for key, first, second in couples:
            if (not verdicts.get(key, UNKNOWN).same
                    or home[id(first)] != home[id(second)]):
                continue
            if id(first) in doubles or id(second) in doubles:
                continue        # одного из двоих и так убираем — вливать некуда
            guest = fusion.join(first, second)
            if guest is not None:
                fused.add(id(guest))
        for key, first, second in couples:
            if verdicts.get(key, UNKNOWN).same and home[id(first)] != home[id(second)]:
                index.link(primary_of(fusion.root(first))["sig"],
                           primary_of(fusion.root(second))["sig"])

    gone = doubles | fused
    if not gone:
        return cost
    for at, (topic, groups) in enumerate(shortlists):
        shortlists[at] = (topic, [g for g in groups if id(g) not in gone])
    if doubles:
        log.info("Дедупликация: снято кандидатов %d — читатель это уже видел",
                 len(doubles))
    if fused:
        log.info("Дедупликация: склеено кандидатов %d — событие одно, "
                 "карточка будет одна и подробнее", len(fused))
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
    fresh, questions, seen_sig = [], [], {}
    for group in groups:
        score, sig, text, at = index.near(group)
        if score >= CFG["similarity"]:
            continue                        # по словам это уже уходило
        if enabled() and gray(score) and at >= since:
            key = pair_key(sig, primary_of(group)["sig"])
            seen_sig[key] = sig
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
        verdict = verdicts.get(key, UNKNOWN) if key else UNKNOWN
        if verdict.same:
            index.mark(primary_of(group)["sig"])
            log.info("Срочное отменено, это уже уходило: %s",
                     primary_of(group)["title"][:70])
            continue
        if verdict.follows:
            index.follow(group, index.hash_of(seen_sig.get(key, "")))
        out.append(group)
    return out, cost
