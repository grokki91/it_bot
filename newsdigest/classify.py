# -*- coding: utf-8 -*-
"""Раздел новости — по её содержанию, а не по ленте, из которой она пришла.

Раньше раздел определялся ровно одним способом: `source_id` входит в список
фидов темы. Пока ленты узкие, это работает — из `arstechnica.com/ai/feed` ничего
кроме ИИ не придёт. Но половина хороших источников широкие: у Reuters, BBC,
Guardian, phys.org, ScienceDaily, Nature и IEEE Spectrum в одной ленте идёт всё
сразу. И тогда получалось так:

    новость про GPT-6 от Reuters      -> «Политика» (Reuters приписан туда)
    статья про таяние льдов с phys.org -> «Наука» (общая лента phys.org там)
    робототехника из IEEE Spectrum     -> «Наука» (all-topics стоит в науке)

То есть материал попадал не в свой раздел и там конкурировал с чужими — а в
свой не попадал вообще. Здесь это чинится каскадом, от бесплатного к дорогому:

    1. узкий фид (`trust.is_strict`) — раздел берётся из ленты, как и раньше.
       Это большинство материалов, и на них не тратится ничего;
    2. словарь правил — веса по заголовку и лиду. Тоже бесплатно;
    3. модель — только для широких лент, только когда правила не дали
       уверенного ответа, пачкой и с кэшем по сигнатуре. Ограничена
       `classify_max` материалами за прогон;
    4. не решилось — раздел остаётся пустым, и всё работает как раньше:
       `pipeline.for_topic` доберёт такой материал по источнику.

Разделы для маршрутизации берутся из тех, что кто-то читает: незачем уводить
новость в раздел, на который никто не подписан, — там её никто не увидит.
"""
from __future__ import annotations

import json
import re

from . import trust
from .config import CFG, log, now_iso
from .llm import LLMError, as_list, llm_cost, llm_json
from .profiles import PROFILES

#: сколько слово весит, если нашлось в заголовке, а не в лиде
TITLE_BONUS = 2.0
#: вес обычного слова и «сильного» — того, что почти однозначно называет раздел
WEIGHT, STRONG = 1.0, 3.0
#: балл ниже — считаем, что правила не сработали
MIN_SCORE = 3.0
#: и отрыв от второго места должен быть заметным, иначе это не решение
MARGIN = 2.0

#: Словарь маршрутизации. `strong` — то, что почти однозначно называет раздел,
#: `weak` — то, что лишь склоняет к нему, `stop` — то, что уводит прочь.
#:
#: Слова ищутся по границам слова, поэтому короткие («ии», «ai», «цб») не
#: цепляют «дай», «said» и «цбор». Русский и английский вперемешку намеренно:
#: источники и те и другие, а материал приходит до перевода.
ROUTE = {
    "ai": {
        "strong": ["openai", "anthropic", "deepmind", "chatgpt", "gpt-4", "gpt-5",
                   "gpt-6", "llm", "нейросет\\w*", "языков\\w+ модел\\w+", "gemini",
                   "claude", "llama", "mistral", "generative ai", "нейронн\\w+ сет\\w+",
                   "machine learning", "машинн\\w+ обучени\\w+", "ии", "\\bai\\b",
                   "искусственн\\w+ интеллект\\w*", "deepseek", "qwen", "трансформер"],
        "weak": ["model", "модел\\w+", "inference", "агент", "agent", "training",
                 "обучени\\w+", "датасет", "dataset", "бенчмарк", "benchmark",
                 "diffusion", "rag", "промпт", "prompt", "чат-бот", "chatbot"],
        "stop": [],
    },
    "dev": {
        "strong": ["open source", "opensource", "открыт\\w+ исходн\\w+", "linux kernel",
                   "ядр\\w+ linux", "kubernetes", "postgresql", "sqlite", "git\\b",
                   "github", "gitlab", "compiler", "компилятор", "фреймворк",
                   "framework", "javascript", "typescript", "golang", "\\brust\\b",
                   "\\bpython\\b", "webassembly", "docker", "devops", "ci/cd",
                   "языка программировани\\w+", "programming language"],
        "weak": ["api", "sdk", "библиотек\\w+", "library", "release", "релиз",
                 "разработчик\\w*", "developer", "программист\\w*", "код\\b",
                 "\\bcode\\b", "баг", "\\bbug\\b", "runtime", "браузер", "browser"],
        "stop": [],
    },
    "hardware": {
        "strong": ["\\bcpu\\b", "\\bgpu\\b", "процессор\\w*", "видеокарт\\w+",
                   "semiconductor", "полупроводник\\w*", "\\btsmc\\b", "risc-v",
                   "\\bssd\\b", "\\bnvme\\b", "ddr5", "ryzen", "radeon", "geforce",
                   "материнск\\w+ плат\\w+", "литограф\\w+", "\\bчип\\w*",
                   "техпроцесс\\w*", "нанометр\\w*", "оверклок\\w*"],
        "weak": ["nvidia", "\\bamd\\b", "intel", "\\barm\\b", "chip", "benchmark",
                 "ноутбук\\w*", "laptop", "смартфон\\w*", "накопител\\w+",
                 "память", "memory", "серверн\\w+", "энергопотреблени\\w+"],
        "stop": [],
    },
    "robots": {
        "strong": ["robot\\w*", "робот\\w*", "humanoid", "гуманоид\\w*", "\\bдрон\\w*",
                   "drone\\w*", "беспилотн\\w+", "\\bros2?\\b", "манипулятор\\w*",
                   "экзоскелет\\w*", "робототехник\\w+", "robotics"],
        "weak": ["lidar", "лидар", "автономн\\w+", "autonomous", "actuator",
                 "склад\\w+ автоматиз\\w+", "warehouse"],
        "stop": [],
    },
    "space": {
        "strong": ["nasa", "spacex", "\\besa\\b", "роскосмос", "ракет\\w+", "rocket",
                   "satellite", "спутник\\w*", "\\bmars\\b", "марс\\w*", "lunar",
                   "лунн\\w+", "telescope", "телескоп\\w*", "orbit\\w*", "орбит\\w+",
                   "starship", "astronaut", "космонавт\\w*", "asteroid", "астероид\\w*",
                   "экзопланет\\w+", "exoplanet", "галактик\\w+", "galaxy",
                   "космическ\\w+", "космос\\w*"],
        "weak": ["launch", "запуск\\w*", "миссии?", "mission", "\\bmoon\\b",
                 "\\bлун\\w+", "звезд\\w+", "чёрн\\w+ дыр\\w+"],
        "stop": [],
    },
    "climate": {
        "strong": ["climate", "климат\\w*", "emissions?", "выброс\\w*",
                   "углеродн\\w+", "потеплени\\w+", "\\bipcc\\b", "renewable",
                   "возобновляем\\w+", "drought", "засух\\w+", "wildfires?",
                   "sea level", "уровн\\w+ моря", "biodiversity", "биоразнообрази\\w+",
                   "deforestation", "вырубк\\w+ лес\\w+", "greenhouse gas",
                   "парников\\w+ газ\\w*", "энергоперехо\\w+", "arctic", "арктик\\w+",
                   "ледник\\w*", "glacier"],
        "weak": ["carbon", "solar power", "wind power", "экологи\\w+", "environment",
                 "загрязнени\\w+", "pollution", "температур\\w+ рекорд\\w*"],
        "stop": [],
    },
    "science": {
        "strong": ["physics", "физик\\w+", "quantum", "квантов\\w+", "genome",
                   "геном\\w*", "fusion", "термоядерн\\w+", "археолог\\w+",
                   "archaeolog\\w+", "палеонтолог\\w+", "нейтрино", "neutrino",
                   "particle physics", "математик\\w+", "mathematic\\w+",
                   "сверхпроводник\\w*", "superconduct\\w+"],
        "weak": ["research", "исследовани\\w+", "study", "учёны\\w+",
                 "scientists?", "эксперимент\\w*", "experiment", "biolog\\w+",
                 "биолог\\w+", "chemistr\\w+", "хими\\w+", "открыти\\w+",
                 "материал\\w+", "эволюци\\w+"],
        # у климата и медицины есть свои разделы: не забирать их себе
        "stop": ["климат\\w*", "climate", "clinical trial", "вакцин\\w+"],
    },
    "medicine": {
        "strong": ["clinical trials?", "клиническ\\w+ испытани\\w+", "\\bfda\\b",
                   "\\bema\\b", "vaccines?", "вакцин\\w+", "онколог\\w+", "cancer",
                   "опухол\\w+", "antibiotics?", "антибиотик\\w*", "outbreak",
                   "вспышк\\w+ забол\\w+", "gene therapy", "генн\\w+ терапи\\w+",
                   "crispr", "препарат\\w*", "лекарств\\w+", "эпидеми\\w+",
                   "пациент\\w+", "patients?", "диагностик\\w+", "\\bвоз\\b",
                   "заболевани\\w+", "disease"],
        "weak": ["health", "врач\\w*", "doctor", "больниц\\w+", "hospital",
                 "инфекц\\w+", "infection", "\\bвирус\\w*", "virus", "терапи\\w+",
                 "симптом\\w*", "смертност\\w+"],
        "stop": [],
    },
    "health": {
        "strong": ["nutrition", "питани\\w+", "\\bдиет\\w+", "\\bdiet\\b",
                   "\\bсон\\b", "\\bсна\\b", "\\bsleep\\b", "mental health",
                   "психическ\\w+ здоров\\w+", "longevity", "долголети\\w+",
                   "obesity", "ожирени\\w+", "фитнес\\w*", "fitness", "\\bзож\\b",
                   "витамин\\w*", "vitamin", "физическ\\w+ активност\\w+"],
        "weak": ["exercise", "упражнени\\w+", "профилактик\\w+", "prevention",
                 "образ жизни", "lifestyle", "стресс\\w*", "привычк\\w+"],
        "stop": ["clinical trials?", "клиническ\\w+ испытани\\w+"],
    },
    "politics": {
        "strong": ["выбор\\w+ президент\\w+", "elections?", "парламент\\w*",
                   "parliament", "president", "президент\\w*", "министр\\w*",
                   "minister", "sanctions?", "санкци\\w+", "договор\\w*", "treaty",
                   "\\bвойн\\w+", "\\bwar\\b", "ceasefire", "перемири\\w+",
                   "законопроект\\w*", "referendum", "референдум\\w*", "госдум\\w+",
                   "конгресс\\w*", "congress", "senate", "сенат\\w*", "дипломат\\w+",
                   "переговор\\w+", "саммит\\w*", "summit"],
        "weak": ["власт\\w+", "government", "правительств\\w+", "закон\\w*",
                 "\\blaw\\b", "военн\\w+", "military", "конфликт\\w*", "протест\\w*"],
        "stop": [],
    },
    "economy": {
        "strong": ["inflation", "инфляци\\w+", "central bank", "центробанк\\w*",
                   "\\bцб\\b", "recession", "рецесси\\w+", "tariffs?", "пошлин\\w+",
                   "\\bgdp\\b", "\\bввп\\b", "interest rates?", "ключев\\w+ ставк\\w+",
                   "\\bipo\\b", "bankruptc\\w+", "банкротств\\w+", "квартальн\\w+ отчёт\\w*",
                   "earnings", "выручк\\w+", "фондов\\w+ рынк\\w+", "stock market",
                   "oil prices?", "цен\\w+ на нефт\\w+", "баррел\\w+", "биржа?\\w*"],
        # «нефть» сама по себе экономику не делает: нефтебаза горит в
        # «Происшествиях», а нефтепровод строят в «Политике»
        "weak": ["экономик\\w+", "economy", "рубл\\w+", "доллар\\w*", "\\beuro\\b",
                 "прибыл\\w+", "profit", "инвестици\\w+", "сделк\\w+", "\\bdeal\\b",
                 "налог\\w*", "\\btax\\b", "нефт\\w+", "подорожа\\w+",
                 "подешеве\\w+", "котировк\\w+"],
        "stop": [],
    },
    "sports": {
        "strong": ["\\bматч\\w*", "tournament", "турнир\\w*", "чемпионат\\w*",
                   "championship", "олимпи\\w+", "olympics?", "world cup",
                   "футбол\\w*", "football", "хоккей\\w*", "hockey", "теннис\\w*",
                   "tennis", "баскетбол\\w*", "basketball", "\\bдопинг\\w*",
                   "doping", "\\bфифа\\b", "\\bfifa\\b", "\\buefa\\b", "\\bуефа\\b",
                   "\\bнба\\b", "\\bnba\\b", "сборн\\w+ по", "премьер-лиг\\w+"],
        "weak": ["\\bлиг\\w+", "league", "трансфер\\w*", "transfer", "\\bгол\\w*",
                 "\\bкубок\\b", "\\bсезон\\w*", "спортсмен\\w*", "athlete",
                 "\\bтренер\\w*", "coach"],
        "stop": [],
    },
    "incidents": {
        "strong": ["earthquake", "землетрясени\\w+", "\\bflood\\w*", "наводнени\\w+",
                   "eruption", "изверж\\w+", "крушени\\w+", "катастроф\\w+",
                   "\\bавари\\w+", "эвакуац\\w+", "evacuat\\w+", "hurricanes?",
                   "ураган\\w*", "tsunami", "цунами", "tornado\\w*", "торнадо",
                   "\\bвзрыв\\w*", "explosion", "магнитуд\\w+", "magnitude",
                   "оползен\\w*", "landslide", "\\bпожар\\w*", "casualties",
                   "погибл\\w+", "пострадавш\\w+"],
        "weak": ["\\bcrash\\b", "спасател\\w+", "rescue", "чрезвычайн\\w+",
                 "emergency", "жертв\\w+", "\\bvictims?\\b"],
        "stop": [],
    },
    "cinema": {
        "strong": ["\\bфильм\\w*", "\\bкино\\w*", "сериал\\w*", "\\bmovie\\w*",
                   "box office", "кассов\\w+ сбор\\w*", "трейлер\\w*", "trailer",
                   "netflix", "\\bhbo\\b", "disney", "оскар\\w*", "\\boscars?\\b",
                   "каннск\\w+", "\\bcannes\\b", "режиссёр\\w*", "премьер\\w+ фильм\\w*",
                   "стриминг\\w*", "streaming service"],
        "weak": ["актёр\\w*", "actors?", "\\bstudio\\b", "\\bстуди\\w+",
                 "\\bсъёмк\\w+", "экраниз\\w+", "\\bcasting\\b", "кастинг\\w*"],
        "stop": [],
    },
    "games": {
        "strong": ["видеоигр\\w+", "video ?games?", "nintendo", "playstation",
                   "\\bxbox\\b", "\\bsteam\\b", "valve", "геймплей\\w*", "gameplay",
                   "консол\\w+", "\\bdlc\\b", "геймер\\w*", "гейминг\\w*",
                   "unreal engine", "\\bgodot\\b", "\\bunity\\b", "инди-игр\\w+",
                   "roguelike", "speedrun", "игров\\w+ студи\\w+", "game studio"],
        "weak": ["\\bигр\\w+", "\\bgames?\\b", "\\bпатч\\w*", "\\bмод\\w*",
                 "разработчик\\w+ игр", "релиз игр\\w+", "\\bиндустри\\w+ игр"],
        "stop": [],
    },
    "crypto": {
        "strong": ["bitcoin", "биткоин\\w*", "ethereum", "эфириум\\w*", "крипто\\w*",
                   "криптовалют\\w+", "cryptocurrenc\\w+", "\\bdefi\\b", "stablecoin",
                   "стейблкоин\\w*", "blockchain", "блокчейн\\w*", "solana",
                   "\\bnft\\b", "майнинг\\w*"],
        "weak": ["\\btoken\\w*", "\\bтокен\\w*", "\\bwallet\\b", "кошел[её]к",
                 "\\bexchange\\b", "\\bбирж\\w+ крипт"],
        "stop": [],
    },
    "cybersec": {
        "strong": ["vulnerabilit\\w+", "уязвимост\\w+", "\\bcve-?\\d*", "exploit\\w*",
                   "эксплойт\\w*", "ransomware", "шифровальщик\\w*", "\\bbreach\\w*",
                   "утечк\\w+ данн\\w+", "zero-?day", "0-day", "malware",
                   "вредоносн\\w+", "backdoor", "бэкдор\\w*", "phishing", "фишинг\\w*",
                   "\\bddos\\b", "кибератак\\w+", "cyberattack\\w*", "\\bвзлом\\w*",
                   "хакер\\w*", "hackers?"],
        "weak": ["\\bпатч\\w*", "security", "безопасност\\w+", "шифровани\\w+",
                 "encryption", "\\bпароль\\w*", "\\bpassword\\b"],
        "stop": [],
    },
}

CLASSIFY_SYSTEM = """Ты раскладываешь новости по разделам новостного дайджеста.

Доступные разделы (используй ТОЛЬКО эти идентификаторы):
{sections}

Правила:
- выбирай раздел по теме САМОЙ новости, а не по изданию, которое её выпустило;
- если новость подходит сразу нескольким, бери тот, о чём она В ОСНОВНОМ;
- confidence — насколько ты уверен: 1.0 очевидно, 0.5 сомнительно;
- если новость не подходит ни одному разделу, верни section "" и confidence 0.

Ответь ТОЛЬКО валидным json вида:
{{"items": [{{"id": 0, "section": "ai", "confidence": 0.9}}]}}
Верни ответ для КАЖДОГО входного id."""

_patterns = {}


def reset() -> None:
    """Сбросить собранные регулярки. Зовётся, когда профили пересобраны."""
    _patterns.clear()


def _compile(words) -> object:
    """Один шаблон на список слов. Границы слова обязательны: без них «ии»
    находится в «дай», а «ai» — в «said»."""
    parts = [w if w.startswith("\\b") or w.endswith("\\b") else r"\b%s\b" % w
             for w in words if w]
    return re.compile("|".join(parts), re.IGNORECASE | re.UNICODE) if parts else None


def rules_for(topic: str) -> dict:
    """Правила раздела: встроенный словарь, поверх — то, что задал профиль.

    Свой раздел из profiles.json правил не имеет — для него сгодятся его
    собственные keywords: они и заводились как «о чём эта тема».
    """
    if topic in _patterns:
        return _patterns[topic]
    body = PROFILES.get(topic) or {}
    base = ROUTE.get(topic) or {}
    strong = list(base.get("strong") or []) + list(body.get("route") or [])
    weak = list(base.get("weak") or [])
    if not strong and not weak:
        weak = [re.escape(k) for k in (body.get("keywords") or [])]
    stop = list(base.get("stop") or []) + list(body.get("route_stop") or [])
    _patterns[topic] = {"strong": _compile(strong), "weak": _compile(weak),
                        "stop": _compile(stop)}
    return _patterns[topic]


def _hits(pattern, title, lead) -> float:
    """Сколько раз шаблон нашёлся, с надбавкой за попадание в заголовок."""
    if pattern is None:
        return 0.0
    in_title = len(set(m.group(0).lower() for m in pattern.finditer(title)))
    in_lead = len(set(m.group(0).lower() for m in pattern.finditer(lead)))
    return in_title * TITLE_BONUS + in_lead


def scores(title: str, lead: str, topics) -> dict:
    """Балл каждого раздела по словарю правил."""
    title, lead = str(title or ""), str(lead or "")
    out = {}
    for topic in topics:
        rules = rules_for(topic)
        value = (STRONG * _hits(rules["strong"], title, lead)
                 + WEIGHT * _hits(rules["weak"], title, lead)
                 - STRONG * _hits(rules["stop"], title, lead))
        if value > 0:
            out[topic] = value
    return out


def by_rules(title: str, lead: str, topics):
    """(раздел, уверенность) по словарю. Пустой раздел — правила не решили.

    Требуется и достаточный балл, и отрыв от второго места: новость, которая
    одинаково похожа на «Науку» и «Медицину», лучше отдать модели, чем
    угадывать.
    """
    table = scores(title, lead, topics)
    if not table:
        return "", 0.0
    ranked = sorted(table.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < MIN_SCORE or best_score - second < MARGIN:
        return "", 0.0
    return best, min(best_score / (STRONG * TITLE_BONUS), 1.0)


# --------------------------------------------------------------- кэш решений
def cached(conn, sigs) -> dict:
    """Что уже классифицировали раньше: сигнатура -> раздел."""
    out = {}
    sigs = [s for s in sigs if s]
    for start in range(0, len(sigs), 400):       # SQLite не любит длинные IN
        part = sigs[start:start + 400]
        marks = ",".join("?" * len(part))
        for row in conn.execute(
                "SELECT sig, section FROM routes WHERE sig IN (%s)" % marks, part):
            out[row["sig"]] = row["section"]
    return out


def remember(conn, decided) -> None:
    """Кладёт решения модели в кэш: тот же материал второй раз не оплачиваем."""
    conn.executemany(
        "INSERT INTO routes(sig, section, at) VALUES (?,?,?) "
        "ON CONFLICT(sig) DO UPDATE SET section=excluded.section, at=excluded.at",
        [(sig, section, now_iso()) for sig, section in decided if sig])
    conn.commit()


# ---------------------------------------------------------------- модель
def ask_model(rows, topics):
    """Классификация пачкой. Возвращает ({номер строки: раздел}, стоимость)."""
    from .profiles import title as topic_title

    listing = "\n".join("- %s — %s" % (t, topic_title(t)) for t in topics)
    payload = [{"id": idx, "title": row["title"][:200],
                "lead": (row["summary"] or "")[:200]}
               for idx, row in enumerate(rows)]
    data, usage = llm_json(
        CLASSIFY_SYSTEM.format(sections=listing),
        "Новости (json):\n" + json.dumps(payload, ensure_ascii=False),
        CFG["model_rank"], max_tokens=60 * len(payload) + 500)
    out = {}
    for entry in as_list(data):
        try:
            idx = int(entry.get("id", -1))
            confidence = float(entry.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        section = str(entry.get("section") or "").strip()
        if 0 <= idx < len(rows) and section in topics and confidence >= 0.5:
            out[idx] = section
    return out, llm_cost(usage)


def undecided_for_model(rows):
    """Кого имеет смысл отдать модели: широкие ленты, лучшие — первыми.

    Узкий фид сюда не попадает по определению, а из широких берём самые
    заслуживающие доверия и самые свежие: до выпуска доедут именно они,
    а платить за классификацию хвоста незачем.
    """
    wide = [row for row in rows
            if not row.get("section") and not trust.is_strict(row["source_id"])]
    # сортировка стабильна, поэтому два прохода дают «доверие, потом свежесть»
    wide.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    wide.sort(key=lambda r: -trust.trust(r["source_id"]))
    return wide[:max(0, int(CFG["classify_max"]))]


# ---------------------------------------------------------------- точка входа
def route_all(conn, rows, topics) -> float:
    """Проставляет `section` каждому материалу. Возвращает стоимость запроса.

    Меняет `rows` на месте: `section` — раздел, `route_conf` — уверенность.
    Раздел остаётся пустым, если решить не удалось; такой материал
    `pipeline.for_topic` доберёт по источнику, как делал всегда.
    """
    topics = [t for t in topics if t in PROFILES]
    if not topics or not rows:
        for row in rows:
            row.setdefault("section", "")
            row.setdefault("route_conf", 0.0)
        return 0.0

    from .sections import by_source

    known = cached(conn, [row.get("sig") for row in rows])
    stats = {"feed": 0, "rules": 0, "cache": 0, "model": 0, "unsure": 0}

    for row in rows:
        section, confidence = "", 0.0
        source_id = row["source_id"]
        if trust.is_strict(source_id):
            section = by_source(source_id)
            confidence = 1.0
            stats["feed"] += 1
        else:
            hit = known.get(row.get("sig"))
            if hit is not None:
                section, confidence = hit, 0.8
                stats["cache"] += 1
            else:
                section, confidence = by_rules(row["title"], row.get("summary"),
                                               topics)
                if section:
                    stats["rules"] += 1
        row["section"] = section if section in topics else ""
        row["route_conf"] = confidence if row["section"] else 0.0

    cost = 0.0
    if CFG["classify_llm"]:
        pending = undecided_for_model(rows)
        if pending:
            try:
                decided, cost = ask_model(pending, topics)
            except LLMError as exc:
                # не беда: раздел останется пустым, а материал доберётся по
                # источнику — ровно как до появления маршрутизации
                log.warning("Классификация не удалась (%s) — раскладываю по лентам",
                            exc)
                decided = {}
            for idx, section in decided.items():
                pending[idx]["section"] = section
                pending[idx]["route_conf"] = 0.8
            stats["model"] = len(decided)
            remember(conn, [(pending[idx].get("sig"), section)
                            for idx, section in decided.items()])

    stats["unsure"] = sum(1 for row in rows if not row["section"])
    log.info("Разделы: по ленте %d, по словарю %d, из кэша %d, моделью %d, "
             "не решено %d", stats["feed"], stats["rules"], stats["cache"],
             stats["model"], stats["unsure"])
    return cost
