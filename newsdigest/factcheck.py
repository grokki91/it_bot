# -*- coding: utf-8 -*-
"""Не вброс ли это. Особенно в науке, медицине и здоровье.

Начать надо с того, чего здесь НЕТ, потому что напрашивается оно первым.

Здесь не спрашивают у языковой модели «правда ли это». Модель не может
проверить факт: её знания кончились в позапрошлом году, а на вопрос о
вчерашнем событии она уверенно ответит что угодно. «Учёные получили
сверхпроводник при комнатной температуре» — на такой вопрос она скажет и
«да», и «нет», в зависимости от формулировки, и оба раза с одинаковой
уверенностью. Фактчекинг из модели — это не фактчекинг, это генератор
приговоров, а приговор здесь означает, что новость не увидят.

Проверяет факты не модель, а вот что:

    1. КТО ГОВОРИТ (`witnesses`). Открытие, о котором написал ровно один
       агрегатор и больше никто, — это форма вброса. Открытие, о котором за
       день написали Reuters, Nature News и профильное издание, вбросом не
       бывает. Считается по ИЗДАТЕЛЯМ (`trust.publishers`) и по их классу:
       два пересказа одного пресс-релиза — это один голос, а не два. Бесплатно
       и работает всегда, даже когда модель недоступна.

    2. НА ЧТО ССЫЛАЮТСЯ (`paper_of`, `crossref`). Настоящая научная новость
       называет свою статью: DOI, arXiv, журнал, институт. И DOI можно ПРОВЕРИТЬ
       — Crossref отдаёт по нему заголовок статьи бесплатно и без ключа. Это
       единственная проверка во всём модуле, которая не оценивает, а знает:
       либо такая статья есть, либо её нет.

       Отсюда же берётся честная оговорка про препринт. arXiv, bioRxiv и
       medRxiv — это настоящие работы, но не прошедшие рецензирование, и
       половина громких «учёные доказали» родом именно оттуда. Препринт не
       повод прятать новость, но повод сказать читателю, что это препринт.

    3. КАК ЭТО НАПИСАНО (`markers`). «Учёные скрывают», «сенсация»,
       «переворачивает представления», «британские учёные» — словарь,
       устроенный как `classify.ROUTE`. Слабый сигнал: настоящую работу тоже
       умеют пересказать жёлто. Поэтому маркеры только поднимают подозрение,
       а решают не они.

    4. КАРАНТИН (`held`). Главная идея модуля и единственная, у которой нет
       цены ошибки. Сомнительное не выбрасывается — оно ЖДЁТ. Если за
       `fact_hold_h` часов событие подтвердит независимый сильный издатель,
       карантин снимается сам и новость уходит в следующий выпуск. Не
       подтвердил — тихо истекает. Так работает нормальная редакция: Reuters
       не публикует со ссылкой на телеграм-канал, а ждёт второго источника.
       Выпуск и так приходит дважды в день, поэтому несколько часов
       ожидания читатель не замечает вовсе.

    5. МОДЕЛЬ — но с другим вопросом (`llm.judge_claims`). Её спрашивают не
       «правда ли», а «как подано»: названы ли институт и журнал или сказано
       «учёные выяснили»; обещает ли заголовок больше, чем есть в тексте;
       не узнаётся ли вечный жанр (вечный двигатель, лекарство от рака,
       «NASA подтвердило», «опровергли Эйнштейна»). Это суждение о ТЕКСТЕ,
       который у неё перед глазами, — ровно то, что модель умеет и где ей не
       нужно знать сегодняшних новостей.

Приговоров три, и они устроены по-разному:

    ok      публикуем как обычно;
    caveat  публикуем С ПОМЕТКОЙ: «препринт, не прошёл рецензирование»,
            «один источник, подтверждений нет». Читатель взрослый, ему нужна
            оговорка, а не тишина;
    hold    карантин — ждём подтверждения (см. п. 4).

Молча выбросить новость может только детерминированный слой: ссылки нет,
DOI в тексте есть, а статьи по нему не существует. По одному суждению модели
— никогда. Иначе бот превращается в цензора, у которого нет ни знаний, ни
ответственности.

Проверяем не всё подряд: только верхушку разделов из `fact_sections` (наука,
медицина, здоровье, космос, климат — там, где вброс дороже всего) и всё, что
уже подняли бесплатные слои. Вердикт кэшируется в таблице `claims` по
сигнатуре, поэтому переживает пересборку выпуска и общий для всех подписчиков.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import trust
from .config import CFG, log, now_iso
from .llm import LLMError, judge_claims, llm_cost
from .net import http_get
from .rank import primary_of

#: приговоры. Хранятся в таблице `claims`
OK, CAVEAT, HOLD = "ok", "caveat", "hold"

#: DOI: «10.» + номер регистранта + суффикс. Форма закреплена стандартом,
#: поэтому находится в тексте новости надёжно
DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)

#: препринты. Работа настоящая, рецензирования не было — это оговорка,
#: а не приговор: LK-99 тоже был препринтом, и это была настоящая новость
PREPRINTS = {
    "arxiv.org": "arXiv",
    "biorxiv.org": "bioRxiv",
    "medrxiv.org": "medRxiv",
    "chemrxiv.org": "ChemRxiv",
    "ssrn.com": "SSRN",
    "researchsquare.com": "Research Square",
    "preprints.org": "Preprints.org",
}

#: журналы и площадки, по одному упоминанию которых видно, что у работы есть
#: адрес. Список нарочно короткий: он не про «хороший журнал», а про «работа
#: вообще где-то опубликована»
JOURNALS = (
    "nature", "science", "cell", "the lancet", "ланцет", "nejm",
    "new england journal", "pnas", "jama", "bmj", "physical review",
    "astrophysical journal", "nature medicine", "nature physics",
    "nature climate", "ieee", "acm", "plos", "elife", "current biology",
    "journal of", "журнал", "препринт", "preprint", "arxiv", "biorxiv",
    "medrxiv",
)

#: слова, которыми узнаётся институция: у настоящей работы есть автор и адрес
INSTITUTIONS = (
    "университет", "university", "институт", "institute", "лаборатор",
    "laborator", "college", "колледж", "академи", "academy", "обсерватори",
    "observatory", "клиник", "clinic", "госпитал", "hospital",
    # короткие имена — только по границам слова, иначе «РАН» находится
    # в «стран», а «ВОЗ» — в «возраст»
    r"\bnasa\b", r"\besa\b", r"\bcern\b", r"\bmit\b", r"\bран\b",
    r"\bвоз\b", r"\bwho\b", r"\bfda\b", r"\bema\b", r"\bcdc\b",
    r"\bnih\b", "стэнфорд", "stanford", "гарвард", "harvard", "оксфорд",
    "oxford", "кембридж", "cambridge", "роскосмос", "минздрав", "max planck",
)

#: маркеры вброса. Слабый сигнал поодиночке, внятный — букетом.
#:
#: `loud` — то, чего в описании исследования не бывает: сенсация, шок,
#: «учёные в панике». `vague` — заявление без адреса: «учёные доказали», у
#: которого нет ни института, ни журнала. `hoax` — вечные жанры, которые
#: возвращаются раз в год и каждый раз оказываются ничем.
MARKERS = {
    "loud": [
        r"сенсаци\w+", r"шокирующ\w+", r"шок\b", r"ошеломля\w+", r"невероятн\w+",
        r"переворачива\w+ представлени\w+", r"перевернул\w* (?:мир|науку|всё)",
        r"учёные в (?:шоке|панике|ужасе)", r"ученые в (?:шоке|панике|ужасе)",
        r"скрыва\w+ (?:от|правду)", r"власти скрыва\w+", r"от вас скрывают",
        r"сменит\w* парадигм\w+", r"конец (?:физики|науки|медицины)",
        r"bombshell", r"shocking", r"stunned scientists", r"they don't want you",
        r"miracle cure", r"чудо-(?:лекарств\w+|средств\w+)",
    ],
    "vague": [
        r"учёные (?:доказали|выяснили|установили|подтвердили)",
        r"ученые (?:доказали|выяснили|установили|подтвердили)",
        r"британские учёные", r"британские ученые",
        r"эксперты (?:уверены|заявили|предупредили)",
        r"исследование показало", r"по данным исследовани\w+",
        r"scientists (?:prove[ds]?|confirm(?:ed)?|discover(?:ed)?) that",
        r"studies show", r"experts (?:say|warn)",
    ],
    "hoax": [
        r"вечн\w+ двигател\w+", r"perpetual motion",
        r"лекарств\w+ от рака", r"cure for cancer", r"рак побеждён",
        r"опроверг\w+ эйнштейна", r"einstein was wrong",
        r"сверхсветов\w+", r"faster.than.light",
        r"свободн\w+ энерги\w+", r"free energy device",
        r"nasa (?:подтвердил\w*|confirms)", r"плоск\w+ земл\w+",
        r"сигнал (?:от )?внеземн\w+", r"alien (?:signal|civilization)",
        r"чипирование", r"5g (?:вызывает|causes)",
        r"доказано существование душ\w+",
    ],
}

#: классы источников, чей одинокий голос ничего не подтверждает
WEAK_VOICES = ("pr", "state", "aggregator", "community", "other")

_compiled = {}
#: что Crossref ответил про DOI. Живёт до перезапуска демона: статья с
#: этим номером либо есть, либо её нет, и меняется это раз в жизни
_papers = {}


def reset() -> None:
    _compiled.clear()
    _papers.clear()


def enabled() -> bool:
    return bool(CFG["factcheck"])


def watched() -> set:
    """Разделы, где вброс дороже всего и проверка идёт всегда."""
    return {name.strip() for name in str(CFG["fact_sections"]).split(",")
            if name.strip()}


def cutoff() -> str:
    """С какого момента карантин ещё имеет смысл держать."""
    return (datetime.now(timezone.utc)
            - timedelta(hours=max(1, int(CFG["fact_hold_h"])))).isoformat()


# ------------------------------------------------------- слой 1: кто говорит
def witnesses(group) -> dict:
    """Кто подтверждает событие: сколько независимых голосов и каких.

    Голос считается по ИЗДАТЕЛЮ, а не по ленте: шесть фидов Guardian — одна
    редакция. И вес у голосов разный: пересказ пресс-релиза агрегатором не
    подтверждает ничего, потому что источник у них общий и он же и есть
    предмет проверки.
    """
    names = trust.publishers(group)
    strong = {trust.publisher(i["source_id"]) for i in group
              if trust.kind(i["source_id"]) in trust.STRONG_KINDS}
    return {"publishers": len(names), "strong": len(strong),
            "alone": len(names) <= 1,
            "weak_only": not strong and all(
                trust.kind(i["source_id"]) in WEAK_VOICES for i in group)}


def confirmed(group) -> bool:
    """Событие подтверждено настолько, что вбросом уже не бывает."""
    seen = witnesses(group)
    return (seen["strong"] >= int(CFG["fact_min_strong"])
            or seen["publishers"] >= int(CFG["fact_min_publishers"]))


# --------------------------------------------------- слой 2: на что ссылаются
def text_of(group) -> str:
    """Весь текст события: заголовки, лиды и адреса всех его заметок."""
    bits = []
    for item in group:
        bits.append(str(item.get("title") or ""))
        bits.append(str(item.get("summary") or ""))
        bits.append(urllib.parse.unquote(str(item.get("url") or "")))
    return " ".join(bits)


def paper_of(group) -> dict:
    """След научной статьи в новости: DOI, препринт, журнал, институт.

    Ничего не проверяет — только смотрит, названо ли вообще что-нибудь, за
    что можно ухватиться. Новость без единого следа не обязательно ложь, но
    и проверить в ней нечего: «учёные выяснили» — это не адрес.
    """
    text = text_of(group)
    low = text.lower()
    doi = DOI.search(text)
    server = ""
    for hosted, name in PREPRINTS.items():
        if hosted in low:
            server = name
            break
    return {
        "doi": (doi.group(0).rstrip(".,;)") if doi else ""),
        "preprint": server,
        "journal": next((j for j in JOURNALS if j in low), ""),
        "institution": bool(re.search("|".join(INSTITUTIONS), low)),
    }


def crossref(doi: str) -> dict:
    """Существует ли статья с таким DOI. {} — выяснить не удалось.

    Единственное место в модуле, которое не оценивает, а проверяет. Crossref
    бесплатен и без ключа — как каталог CISA в `signals.py`. Возвращаем
    заголовок статьи и год: по ним видно, о той ли работе речь.

    Молчание — это «не знаем», а не «нет такой статьи»: сеть могла не дойти,
    а Crossref знает не про все журналы. Отсутствие ответа новость не топит.
    """
    doi = str(doi or "").strip()
    if not doi or not CFG["doi_check"]:
        return {}
    if doi in _papers:
        # демон живёт неделями и пересобирает выпуск каждые несколько часов:
        # про один и тот же DOI незачем спрашивать дважды
        return _papers[doi]
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    status, raw = http_get(url, timeout=CFG["doi_timeout"])
    if status == 404:
        return _papers.setdefault(doi, {"missing": True})
    if status != 200 or not raw:
        return {}                   # не запоминаем: это «не дошли», а не ответ
    try:
        body = (json.loads(raw.decode("utf-8", "replace")).get("message") or {})
    except (ValueError, AttributeError):
        return {}
    titles = body.get("title") or []
    issued = ((body.get("issued") or {}).get("date-parts") or [[""]])[0]
    return _papers.setdefault(doi, {
        "title": str(titles[0]) if titles else "",
        "year": str(issued[0]) if issued else "",
        "type": str(body.get("type") or ""),
        "journal": str((body.get("container-title") or [""])[0])})


# ------------------------------------------------- слой 3: маркеры вброса
def _patterns(kind: str):
    ready = _compiled.get(kind)
    if ready is None:
        ready = re.compile("|".join(MARKERS[kind]), re.IGNORECASE)
        _compiled[kind] = ready
    return ready


def markers(group) -> dict:
    """Какие маркеры вброса есть в тексте. Слабый сигнал, но бесплатный."""
    text = text_of(group)
    return {kind: bool(_patterns(kind).search(text)) for kind in MARKERS}


# ------------------------------------------------------------ кэш приговоров
def cached(conn, sigs) -> dict:
    """Что уже решали про эти события: сигнатура -> (вердикт, оговорка)."""
    out = {}
    sigs = [s for s in dict.fromkeys(sigs) if s]
    for start in range(0, len(sigs), 400):      # SQLite не любит длинные IN
        part = sigs[start:start + 400]
        marks = ",".join("?" * len(part))
        for row in conn.execute(
                "SELECT sig, verdict, note, at FROM claims WHERE sig IN (%s)"
                % marks, part):
            out[row["sig"]] = (row["verdict"], row["note"], row["at"])
    return out


def remember(conn, verdicts) -> None:
    """Кладёт приговоры в кэш: за тот же вопрос второй раз не платим."""
    rows = [(sig, verdict, note, now_iso()) for sig, verdict, note in verdicts]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO claims(sig, verdict, note, at) VALUES (?,?,?,?) "
        "ON CONFLICT(sig) DO UPDATE SET verdict=excluded.verdict, "
        "note=excluded.note, at=excluded.at", rows)
    conn.commit()


# --------------------------------------------------------- бесплатный вердикт
def by_signals(conn, group, topic="") -> tuple:
    """Приговор по бесплатным слоям. Возвращает (вердикт, оговорка, спросить ли).

    Третьим значением — стоит ли платить за вопрос модели. Событие, которое
    подтвердили три редакции и у которого есть проверенный DOI, в её суждении
    не нуждается.

    Придержать (`HOLD`) здесь может только то, что выдаёт себя само: DOI, по
    которому нет статьи, и вечный жанр без единого подтверждения. Всё
    остальное — оговорка, а не карантин, и вот почему. Выпуск на две трети
    состоит из новостей, о которых написал ровно один источник: релиз Rust,
    заметка LWN, пост в блоге Postgres. Это не вброс, это норма — и правило
    «один источник, значит держим» вырезало бы из выпуска половину, начиная
    с самого интересного.

    Раздел при этом решает. «Один источник» в разделе про игры не значит
    ничего, а в медицине — значит, что читателю стоит об этом сказать.
    """
    seen = witnesses(group)
    paper = paper_of(group)
    flags = markers(group)
    watch = topic in watched()

    # DOI, которого не существует, — единственный случай, когда событие
    # придерживается без всяких моделей: текст ссылается на статью, а статьи
    # по этому номеру нет. Молчание Crossref при этом ничего не значит
    # (`crossref` возвращает {} и на сетевой сбой), и держать за него нельзя
    if paper["doi"]:
        found = crossref(paper["doi"])
        if found.get("missing"):
            return HOLD, "DOI %s не существует" % paper["doi"], False
        if found.get("title"):
            # статья есть — дальше проверять нечего, у заявления есть адрес
            if paper["preprint"]:
                return CAVEAT, "препринт %s, без рецензирования" % paper["preprint"], False
            return OK, "", False

    # вечный жанр: вечный двигатель, лекарство от рака, «опровергли Эйнштейна».
    # Иногда за этим стоит настоящая новость про очередную заявку — поэтому
    # подтверждённое проходит, а неподтверждённое ждёт
    if flags["hoax"] and not confirmed(group):
        return HOLD, "узнаваемый жанр вброса, подтверждений нет", True

    if paper["preprint"]:
        return CAVEAT, "препринт %s, без рецензирования" % paper["preprint"], not confirmed(group)

    if confirmed(group):
        return OK, "", False

    if not watch:
        # обычный раздел: один источник — это обычная новость, а не вброс
        return OK, "", False

    if seen["alone"] and seen["weak_only"]:
        return CAVEAT, "один источник, подтверждений нет", True
    if not paper["journal"] and not paper["institution"]:
        # научная новость, которая не называет ни работы, ни института
        return CAVEAT, "источник исследования не назван", True
    if flags["loud"] or flags["vague"]:
        return CAVEAT, "", True
    return OK, "", True


# ------------------------------------------------------------------- вопрос
def question(group, topic="") -> dict:
    """Что показать модели: событие целиком, без наших выводов.

    Свои подозрения ей не сообщаем нарочно: подсказка «мы думаем, это вброс»
    превращает вопрос в просьбу согласиться, и модель соглашается.
    """
    main = primary_of(group)
    paper = paper_of(group)
    return {
        "title": str(main.get("title") or "")[:300],
        "lead": str(main.get("summary") or "")[:500],
        "section": topic,
        "sources": sorted({trust.publisher(i["source_id"]) for i in group})[:5],
        "kinds": sorted({trust.kind(i["source_id"]) for i in group}),
        "paper": paper["doi"] or paper["preprint"] or paper["journal"] or "",
    }


def ask(conn, pending):
    """Спросить модель про спорные события. Возвращает ({sig: (вердикт, note)}, цена).

    `pending` — список (sig, вопрос). Модель недоступна — возвращаем пусто:
    бесплатные слои уже вынесли свой приговор, и он остаётся в силе. Ровно
    как в `dedup`: без модели работает то, что работало и раньше.
    """
    pending = pending[:max(0, int(CFG["fact_max"]))]
    if not pending or not CFG["fact_llm"]:
        return {}, 0.0

    size = max(1, int(CFG["fact_batch"]))
    fresh, cost, lost = {}, 0.0, 0
    for start in range(0, len(pending), size):
        part = pending[start:start + size]
        try:
            answers, usage = judge_claims([q for _sig, q in part])
        except LLMError as exc:
            lost += len(part)
            log.warning("Проверка заявлений не удалась (%s) — %d сужу своими "
                        "слоями", exc, len(part))
            continue
        cost += llm_cost(usage)
        for idx, answer in answers.items():
            if 0 <= idx < len(part):
                fresh[part[idx][0]] = answer
    if fresh:
        remember(conn, [(sig, verdict, note)
                        for sig, (verdict, note) in fresh.items()])
        log.info("Фактчек: спрошено %d, придержано %d%s", len(pending) - lost,
                 sum(1 for v, _n in fresh.values() if v == HOLD),
                 "" if not lost else ", про %d ответа не пришло" % lost)
    return fresh, cost


# ---------------------------------------------------------------- карантин
def held(conn, sig: str) -> bool:
    """Событие сейчас в карантине: приговор `hold` и он ещё не истёк."""
    row = conn.execute("SELECT verdict, at FROM claims WHERE sig = ?",
                       (sig,)).fetchone()
    return bool(row and row["verdict"] == HOLD and str(row["at"]) >= cutoff())


def release(conn, sig: str, why: str = "") -> None:
    """Снять карантин: подтверждение пришло, событие можно публиковать."""
    conn.execute(
        "INSERT INTO claims(sig, verdict, note, at) VALUES (?,?,?,?) "
        "ON CONFLICT(sig) DO UPDATE SET verdict=excluded.verdict, "
        "note=excluded.note, at=excluded.at", (sig, OK, why, now_iso()))
    conn.commit()


# ------------------------------------------------------------- точка входа
def top_of(shortlists) -> list:
    """Кандидаты, которых стоит проверять: верхушка «опасных» разделов.

    В выпуск из раздела идут один-два кандидата, и платить за хвост незачем.
    Разделы вне `fact_sections` сюда попадают только верхушкой: вброс про
    новый айфон читателю не страшен, а вброс про лекарство — страшен.
    """
    limit = max(1, int(CFG["fact_candidates"]))
    out = []
    for topic, groups in shortlists:
        depth = limit if topic in watched() else max(1, limit // 2)
        out.extend((topic, group) for group in groups[:depth])
    return out


def screen(conn, shortlists) -> float:
    """Проверить кандидатов выпуска. Правит списки на месте, возвращает цену.

    Зовётся после дедупликации и ДО ранжирования — по тем же причинам, по
    каким там стоит `dedup.prune`: платить за оценку и карточку события,
    которое не выйдет, незачем.

    Придержанное не исчезает навсегда: приговор лежит в `claims` со временем,
    и через `fact_hold_h` часов истекает сам. А если подтверждение придёт
    раньше — бесплатные слои увидят это на следующем прогоне и снимут
    карантин сами (`confirmed`), потому что кластер к тому времени соберёт
    новых издателей.
    """
    if not enabled():
        return 0.0
    pairs = top_of(shortlists)
    if not pairs:
        return 0.0

    known = cached(conn, [primary_of(group)["sig"] for _topic, group in pairs])
    notes, holds, questions = {}, set(), []
    for topic, group in pairs:
        sig = primary_of(group)["sig"]

        # подтверждение могло прийти после того, как мы придержали событие:
        # кластер собрал новых издателей, и карантин снимается сам
        if sig in known and known[sig][0] == HOLD:
            if confirmed(group):
                release(conn, sig, "подтверждено независимыми источниками")
                log.info("Фактчек: карантин снят, событие подтвердили — %s",
                         primary_of(group)["title"][:70])
            elif str(known[sig][2]) >= cutoff():
                holds.add(id(group))
                continue

        verdict, note, doubt = by_signals(conn, group, topic)
        if verdict == HOLD:
            holds.add(id(group))
            remember(conn, [(sig, HOLD, note)])
            continue
        # оговорка, за которую уже платили, важнее вычисленной заново:
        # в ней то, что увидела модель, а не только наши слои
        note = (known[sig][1] if sig in known else "") or note
        if note:
            notes[id(group)] = note
        if doubt and sig not in known and CFG["fact_llm"]:
            questions.append((sig, question(group, topic)))

    cost = 0.0
    if questions:
        answers, cost = ask(conn, questions)
        for topic, group in pairs:
            sig = primary_of(group)["sig"]
            if sig not in answers or id(group) in holds:
                continue
            verdict, note = answers[sig]
            if verdict == HOLD and not confirmed(group):
                # модель может придержать, но не выбросить: карантин истекает
                # сам, и подтверждение снимает его раньше срока
                holds.add(id(group))
            elif note:
                notes[id(group)] = note

    # оговорку кладём прямо в материал: дальше её подхватит карточка
    for _topic, group in pairs:
        note = notes.get(id(group))
        if note:
            for item in group:
                item["caveat"] = note

    if holds:
        for at, (topic, groups) in enumerate(shortlists):
            shortlists[at] = (topic, [g for g in groups if id(g) not in holds])
        log.info("Фактчек: придержано событий %d — ждём подтверждения", len(holds))
    return cost


def caveat_of(group) -> str:
    """Оговорка события, если она есть: «препринт», «один источник»."""
    for item in group:
        note = str(item.get("caveat") or "").strip()
        if note:
            return note
    return ""


def vet(conn, groups, topic="") -> tuple:
    """То же для срочного: (что можно слать, цена).

    Консенсус издателей здесь проверен раньше и жёстче нашего: до срочного
    доходит только то, что прошло `breaking.is_hot` (два агентства, три
    издателя с первоисточником или четыре вообще). Поэтому «кто говорит» тут
    уже спрошено, а наше дело — остальные слои: вечный жанр, несуществующий
    DOI, препринт.

    Разница с выпуском в том, что здесь нет оговорок как выхода: придержанное
    не отправляется вовсе. Оно не теряется — дождётся подтверждения и придёт
    плановым выпуском, где к нему будет пометка. Разбудить человека ночью
    ради вброса — худшее, на что бот способен.
    """
    if not enabled() or not groups:
        return groups, 0.0

    out, questions, pending = [], [], {}
    known = cached(conn, [primary_of(group)["sig"] for group in groups])
    for group in groups:
        main = primary_of(group)
        sig = main["sig"]
        # раздел берём у самой новости: срочное собирается по всем разделам
        # сразу, и вызывающему он неизвестен. Общее `topic` при этом не
        # трогаем — иначе раздел первой новости достался бы всем следующим
        section = topic or str(main.get("section") or "")
        if sig in known and known[sig][0] == HOLD and str(known[sig][2]) >= cutoff():
            if not confirmed(group):
                log.info("Срочное придержано (карантин): %s",
                         primary_of(group)["title"][:70])
                continue
            release(conn, sig, "подтверждено независимыми источниками")

        verdict, note, doubt = by_signals(conn, group, section)
        if verdict == HOLD:
            remember(conn, [(sig, HOLD, note)])
            log.info("Срочное придержано (%s): %s", note, main["title"][:70])
            continue
        if note:
            for item in group:
                item["caveat"] = note
        if doubt and sig not in known and CFG["fact_llm"]:
            questions.append((sig, question(group, section)))
            pending[sig] = group
        out.append(group)

    cost = 0.0
    if questions:
        answers, cost = ask(conn, questions)
        for sig, (verdict, note) in answers.items():
            group = pending.get(sig)
            if group is None:
                continue
            if verdict == HOLD and not confirmed(group):
                log.info("Срочное придержано моделью (%s): %s", note,
                         primary_of(group)["title"][:70])
                out = [g for g in out if g is not group]
            elif note:
                for item in group:
                    item["caveat"] = note
    return out, cost
