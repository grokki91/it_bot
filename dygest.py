#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
digest.py — ежедневный дайджест новостей в Telegram. LLM: DeepSeek.

Принципы:
  * ТОЛЬКО стандартная библиотека Python 3.8+ — pip ставить не нужно вообще,
    значит нечего сломать в системном Python вашего VPS;
  * НИКОГДА не требует root, ничего не пишет вне своего каталога,
    не трогает apt/docker/nginx/postgres/VPN;
  * все данные в одном месте: ~/.newsdigest (база, настройки, логи);
  * один процесс-демон сам знает, когда собирать и когда отправлять.

Команды:
  python3 digest.py setup          мастер настройки (токены, чат, тема, время)
  python3 digest.py doctor         проверка Telegram / DeepSeek / базы / источников
  python3 digest.py run --dry-run  собрать и показать дайджест в терминале
  python3 digest.py run            собрать и отправить прямо сейчас
  python3 digest.py daemon         фоновый режим по расписанию
  python3 digest.py status         прогоны, расход, здоровье источников
  python3 digest.py feeds          проверить все источники по одному
  python3 digest.py service        напечатать unit-файл systemd (по желанию)
"""
from __future__ import annotations

import argparse
import getpass
import gzip
import hashlib
import html as html_mod
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# =============================================================================
#  Р А З Д Е Л   Н А С Т Р О Е К
#  Правьте прямо здесь. Значения с пометкой [env] можно также задать в файле
#  ~/.newsdigest/env — там они главнее (мастер `setup` пишет их туда сам).
# =============================================================================

CFG = {
    # --- что и когда ---------------------------------------------------------
    "topic":            "ai",      # [env ND_TOPIC] ai | crypto | cybersec | custom
    "language":         "русский", # [env ND_LANGUAGE] язык дайджеста
    "send_at":          "09:00",   # [env ND_SEND_AT] во сколько отправлять (ВАШЕ время)
    "tz":               "Europe/Riga",  # [env ND_TZ] пояс по имени: сам учитывает
                                   # переход на летнее время. Пусто = использовать tz_offset.
    "tz_offset":        3,         # [env ND_TZ_OFFSET] запасной вариант, если пояс не найден
    "collect_every_h":  4,         # [env ND_COLLECT_EVERY] раз в сколько часов собирать

    # --- сколько новостей ----------------------------------------------------
    "min_items":        5,         # [env ND_MIN_ITEMS] меньше — просто тихий день, не ошибка
    "max_items":        8,         # [env ND_MAX_ITEMS] сколько новостей в выпуске (5-10)
    "min_score":        5.5,       # [env ND_MIN_SCORE] порог важности 1-10, ниже не публикуем
    "max_per_source":   2,         # не даём одному сайту занять весь выпуск
    "max_per_category": 3,         # и одной теме тоже

    # --- сбор ----------------------------------------------------------------
    "window_hours":     30,        # насколько старые материалы ещё считаем свежими
    "http_timeout":     20,        # секунд на один источник
    "concurrency":      4,         # параллельных загрузок (на 1 vCPU больше не нужно)
    "max_per_feed":     30,        # сколько записей брать из одного фида
    "mute_after_fails": 5,         # после N сбоев подряд источник молчит сутки
    "use_hackernews":   True,      # добавлять топ Hacker News (без ключа, бесплатно)
    "hn_min_points":    80,     # порог баллов HN. Ниже = больше шума с форума
    "hn_tier":          3,      # 3 = агрегатор: если ту же новость дал реальный
                                # сайт, ссылка ведёт на него, а не на тред HN

    # --- дедупликация --------------------------------------------------------
    "similarity":       0.32,      # 0..1 порог склейки одинаковых новостей.
                                   # Меньше = агрессивнее склейка (риск потерять новость),
                                   # больше = чаще будут дубли одного события.

    # --- LLM (DeepSeek) ------------------------------------------------------
    "llm_base":         "https://api.deepseek.com",
    "model_rank":       "deepseek-v4-flash",  # ранжирование — дешёвая модель
    "model_summary":    "deepseek-v4-flash",  # саммари. Хотите качественнее: deepseek-v4-pro
    "llm_candidates":   28,        # сколько кластеров отдаём модели на оценку
    "llm_timeout":      120,
    "llm_retries":      4,
    "disable_thinking": True,      # V4 умеет "думать" — нам это не нужно, дороже и медленнее
    "price_in":         0.14,      # $/1M токенов — только для оценки расхода в `status`
    "price_out":        0.28,

    # --- Telegram ------------------------------------------------------------
    "one_message":      True,      # весь дайджест одним сообщением (режем только если >4096)
    "link_preview":     False,     # превью ссылок раздувает сообщение
    "silent":           False,     # [env ND_SILENT] true = отправлять без звука

    # --- хранение ------------------------------------------------------------
    "keep_items_days":  10,
    "keep_sent_days":   60,        # история отправленного = защита от повторов
    # Часть сайтов за Cloudflare (openai.com, theverge) отдаёт 403 незнакомому
    # клиенту. Сначала ходим вежливо, при 403/429 автоматически повторяем
    # запрос вторым User-Agent. Если источник всё равно молчит — виден в `status`.
    "user_agent":       "Mozilla/5.0 (compatible; newsdigest/2.0; personal RSS reader)",
    "fallback_user_agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
}

# --- Темы. Сменить тематику = поменять CFG["topic"] и, если надо, дописать фиды.
#     tier: 1 = первоисточник, 2 = профильное СМИ, 3 = агрегатор/форум.
#     Сломанный фид сам отключится на сутки и будет виден в `digest.py status`.
PROFILES = {

    "ai": {
        "persona": (
            "инженер-разработчик. Ему интересны: новые модели и их реальные "
            "возможности, инструменты и библиотеки, применимые в работе, "
            "архитектурные решения, бенчмарки, цены на API, open-source релизы. "
            "НЕ интересны: маркетинговые анонсы без деталей, раунды финансирования "
            "без технической сути, общие рассуждения о будущем AI, тексты уровня "
            "«как AI изменит вашу отрасль»."
        ),
        "keywords": [  # используются только для фильтра Hacker News
            "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic", "deepmind",
            "deepseek", "model", "neural", "transformer", "agent", "inference",
            "diffusion", "machine learning", "mistral", "llama", "qwen", "rag",
        ],
        "feeds": [
            # --- лаборатории и вендоры (первоисточники) ---
            ("openai",            "https://openai.com/news/rss.xml",                          1, "labs"),
            ("google-deepmind",   "https://deepmind.google/blog/rss.xml",                     1, "labs"),
            ("google-research",   "https://research.google/blog/rss/",                        1, "labs"),
            # ai.meta.com/blog/rss отдаёт 404 — у Meta публичного RSS нет.
            ("meta-engineering",  "https://engineering.fb.com/feed/",                         1, "labs"),
            ("nvidia-dev",        "https://developer.nvidia.com/blog/feed/",                  1, "labs"),
            ("huggingface",       "https://huggingface.co/blog/feed.xml",                     1, "labs"),
            ("microsoft-research","https://www.microsoft.com/en-us/research/feed/",           1, "labs"),
            ("bair-berkeley",     "https://bair.berkeley.edu/blog/feed.xml",                  1, "research"),
            # --- технологические СМИ ---
            ("techcrunch",        "https://techcrunch.com/category/artificial-intelligence/feed/", 2, "media"),
            ("venturebeat",       "https://venturebeat.com/category/ai/feed/",                2, "media"),
            ("theverge",          "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 2, "media"),
            ("arstechnica",       "https://arstechnica.com/ai/feed/",                         2, "media"),
            ("techreview",        "https://www.technologyreview.com/topic/artificial-intelligence/feed", 2, "media"),
            ("theregister",       "https://www.theregister.com/software/ai_ml/headlines.atom",2, "media"),
            # --- экспертные подборки (уже отфильтрованы человеком) ---
            ("simonwillison",     "https://simonwillison.net/atom/everything/",               2, "community"),
            ("import-ai",         "https://importai.substack.com/feed",                       2, "community"),
            ("the-batch",         "https://www.deeplearning.ai/the-batch/feed/",              2, "community"),
            ("interconnects",     "https://www.interconnects.ai/feed",                        2, "community"),
            # --- open-source: релизы через GitHub Atom (работает без токена) ---
            ("gh-vllm",           "https://github.com/vllm-project/vllm/releases.atom",       1, "opensource"),
            ("gh-llama-cpp",      "https://github.com/ggml-org/llama.cpp/releases.atom",      1, "opensource"),
            ("gh-ollama",         "https://github.com/ollama/ollama/releases.atom",           1, "opensource"),
            ("gh-transformers",   "https://github.com/huggingface/transformers/releases.atom",1, "opensource"),
            ("gh-pytorch",        "https://github.com/pytorch/pytorch/releases.atom",         1, "opensource"),
            # --- сообщества (Reddit иногда режет ботов — если падает, удалите строку) ---
            ("r-localllama",      "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day",       3, "community"),
            # --- наука. Шумно: сотни статей в день. Раскомментируйте, если нужно.
            # ("arxiv-cs-AI",     "https://rss.arxiv.org/rss/cs.AI",                          1, "research"),
            # ("arxiv-cs-CL",     "https://rss.arxiv.org/rss/cs.CL",                          1, "research"),
        ],
    },

    "crypto": {
        "persona": (
            "разработчик и инвестор в криптовалютах. Интересны: протоколы и "
            "обновления сетей, регулирование, крупные движения капитала, взломы и "
            "уязвимости, инфраструктура. НЕ интересны: ценовые предсказания, "
            "реклама бирж, «топ-5 монет которые взлетят»."
        ),
        "keywords": ["bitcoin", "ethereum", "crypto", "defi", "stablecoin", "sec",
                     "blockchain", "solana", "l2", "rollup", "etf"],
        "feeds": [
            ("coindesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/", 2, "media"),
            ("cointelegraph", "https://cointelegraph.com/rss",                   2, "media"),
            ("theblock",      "https://www.theblock.co/rss.xml",                 2, "media"),
            ("decrypt",       "https://decrypt.co/feed",                         2, "media"),
            ("ethereum-blog", "https://blog.ethereum.org/en/feed.xml",           1, "labs"),
            ("bitcoinmag",    "https://bitcoinmagazine.com/feed",                2, "media"),
        ],
    },

    "cybersec": {
        "persona": (
            "инженер по информационной безопасности. Интересны: активно "
            "эксплуатируемые уязвимости, крупные утечки и взломы, новые техники "
            "атак, инструменты, изменения в регулировании. НЕ интересны: "
            "вендорский маркетинг, «5 советов по паролям», отчёты без деталей."
        ),
        "keywords": ["cve", "vulnerability", "exploit", "ransomware", "breach",
                     "zero-day", "malware", "patch", "backdoor"],
        "feeds": [
            ("krebs",          "https://krebsonsecurity.com/feed/",                       1, "community"),
            ("bleepingcomputer","https://www.bleepingcomputer.com/feed/",                 2, "media"),
            ("thehackernews",  "https://thehackernews.com/feeds/posts/default",           2, "media"),
            ("schneier",       "https://www.schneier.com/feed/",                          1, "community"),
            ("darkreading",    "https://www.darkreading.com/rss.xml",                     2, "media"),
            ("project-zero",   "https://googleprojectzero.blogspot.com/feeds/posts/default", 1, "research"),
            ("cisa-advisories","https://www.cisa.gov/cybersecurity-advisories/all.xml",   1, "policy"),
        ],
    },

    # Своя тема: скопируйте блок, замените фиды/persona и поставьте topic = "custom".
    "custom": {
        "persona": "внимательный читатель, которому важны факты, а не мнения.",
        "keywords": ["news"],
        "feeds": [
            ("example", "https://news.ycombinator.com/rss", 2, "media"),
        ],
    },
}

# Веса детерминированного прескоринга (отбирает кандидатов ДО обращения к LLM).
# Сумма не обязана равняться 1. Правьте по итогам первой недели.
WEIGHTS = {
    "source_tier":   0.30,   # первоисточник важнее пересказа
    "corroboration": 0.25,   # о чём написали сразу несколько сайтов
    "social":        0.20,   # баллы Hacker News
    "freshness":     0.25,   # свежее важнее
}

# =============================================================================
#  Дальше — механика. Менять не обязательно.
# =============================================================================

HOME = Path(os.environ.get("ND_HOME", str(Path.home() / ".newsdigest")))
ENV_FILE = HOME / "env"
DB_FILE = HOME / "digest.db"
LOG_FILE = HOME / "digest.log"

ENV_MAP = {
    "ND_TOPIC": ("topic", str),
    "ND_LANGUAGE": ("language", str),
    "ND_SEND_AT": ("send_at", str),
    "ND_TZ": ("tz", str),
    "ND_TZ_OFFSET": ("tz_offset", int),
    "ND_COLLECT_EVERY": ("collect_every_h", int),
    "ND_MIN_ITEMS": ("min_items", int),
    "ND_MAX_ITEMS": ("max_items", int),
    "ND_MIN_SCORE": ("min_score", float),
    "ND_MODEL_SUMMARY": ("model_summary", str),
    "ND_SILENT": ("silent", lambda v: str(v).lower() in ("1", "true", "yes")),
}

log = logging.getLogger("nd")
TG_TOKEN = ""
TG_CHAT = ""
DS_KEY = ""


# ------------------------------------------------------------------ окружение
def load_env() -> None:
    """Читает ~/.newsdigest/env. Переменные, уже заданные снаружи, главнее."""
    global TG_TOKEN, TG_CHAT, DS_KEY
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

    for env_name, (cfg_key, cast) in ENV_MAP.items():
        raw = os.environ.get(env_name, "")
        if raw == "":
            continue
        try:
            CFG[cfg_key] = cast(raw)
        except (TypeError, ValueError):
            print("Не понял значение %s=%r, использую значение по умолчанию"
                  % (env_name, raw))

    init_tz()
    TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    DS_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()


def profile() -> dict:
    prof = PROFILES.get(CFG["topic"])
    if not prof:
        sys.exit("Неизвестная тема %r. Доступны: %s"
                 % (CFG["topic"], ", ".join(PROFILES)))
    return prof


def setup_logging(verbose: bool = False, to_file: bool = False) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    if to_file:
        # Ротация своими силами: 3 файла по 2 МБ, потолок 6 МБ навсегда.
        # В системный journal демон не пишет — он у вас и так разросся.
        handlers.append(RotatingFileHandler(
            str(LOG_FILE), maxBytes=2000000, backupCount=2, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


_TZ_NAME = ""


def init_tz() -> None:
    """Ставит часовой пояс по имени (Europe/Riga). Переход на летнее время
    учитывается системной базой tzdata — время отправки не «уедет» зимой."""
    global _TZ_NAME
    name = str(CFG.get("tz") or "").strip()
    _TZ_NAME = ""
    if not name or not hasattr(time, "tzset"):
        return
    if not (Path("/usr/share/zoneinfo") / name).exists():
        print("Часовой пояс %r не найден в системе, использую смещение UTC%+d"
              % (name, CFG["tz_offset"]))
        return
    os.environ["TZ"] = name
    time.tzset()
    _TZ_NAME = name


def tz_label() -> str:
    return _TZ_NAME or "UTC%+d" % CFG["tz_offset"]


def local_now() -> datetime:
    """Ваше местное время (наивный datetime — нужен только для часов и дат)."""
    if _TZ_NAME:
        return datetime.now()
    return (datetime.now(timezone.utc)
            + timedelta(hours=CFG["tz_offset"])).replace(tzinfo=None)


# ----------------------------------------------------------------------- HTTP
class _Redirect308(urllib.request.HTTPRedirectHandler):
    """Python до 3.11 не умеет следовать за 308 Permanent Redirect, а на нём
    сидит часть фидов (venturebeat, deeplearning.ai). Учим вручную."""
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_301

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(
            req, fp, 301 if code == 308 else code, msg, headers, newurl)


_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(
            _Redirect308,
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    return _OPENER


def _open(url: str, data=None, headers=None, timeout=30, method=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", CFG["user_agent"])
    req.add_header("Accept", "*/*")
    for key, val in (headers or {}).items():
        req.add_header(key, val)
    with _opener().open(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return 200, raw


def http_get(url: str, timeout=None, ua=None):
    """Возвращает (status, bytes). Исключения сети наружу не выпускает."""
    headers = {"User-Agent": ua} if ua else None
    try:
        return _open(url, headers=headers, timeout=timeout or CFG["http_timeout"])
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return exc.code, body


def post_json(url: str, payload: dict, headers=None, timeout=60):
    """POST с JSON. Возвращает (status, dict|None, текст ошибки)."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdr = {"Content-Type": "application/json"}
    hdr.update(headers or {})
    try:
        status, raw = _open(url, data=body, headers=hdr, timeout=timeout, method="POST")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001
            raw = b""
    except Exception as exc:  # noqa: BLE001 — таймауты, DNS, TLS
        return 0, None, "%s: %s" % (type(exc).__name__, exc)
    try:
        return status, json.loads(raw.decode("utf-8", "replace")), ""
    except (ValueError, UnicodeDecodeError):
        return status, None, raw[:400].decode("utf-8", "replace")


# ------------------------------------------------------------------- разбор RSS
def _tagname(tag) -> str:
    return tag.split("}")[-1].lower() if isinstance(tag, str) else ""


def _text(el) -> str:
    return "".join(el.itertext())


def strip_html(raw: str, limit: int = 1000) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:                                    # RFC 822: "Mon, 06 Sep 2021 12:00:00 GMT"
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    text = value.replace("Z", "+00:00")      # ISO 8601
    text = re.sub(r"\.\d+", "", text)
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed(raw: bytes) -> list:
    """RSS 2.0 / RSS 1.0 / Atom одним кодом: сравниваем локальные имена тегов."""
    start = raw.find(b"<")
    if start > 0:
        raw = raw[start:]
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)
        root = ET.fromstring(cleaned)       # если снова упадёт — обработает вызывающий

    out = []
    for el in root.iter():
        if _tagname(el.tag) not in ("item", "entry"):
            continue
        title = link = summary = ""
        published = None
        for child in el:
            name = _tagname(child.tag)
            if name == "title" and not title:
                title = strip_html(_text(child), 300)
            elif name == "link":
                href = child.get("href")
                if href:
                    rel = (child.get("rel") or "alternate").lower()
                    if rel == "alternate" and not link:
                        link = href.strip()
                elif (child.text or "").strip() and not link:
                    link = child.text.strip()
            elif name in ("guid", "id") and not link:
                if (child.text or "").strip().startswith("http"):
                    link = child.text.strip()
            elif name in ("description", "summary", "content", "encoded", "subtitle"):
                candidate = strip_html(_text(child), 1000)
                if len(candidate) > len(summary):
                    summary = candidate
            elif name in ("pubdate", "published", "updated", "date") and published is None:
                published = parse_date(child.text or "")
        if title and link:
            out.append({"title": title, "link": link,
                        "summary": summary, "published": published})
    return out


# ------------------------------------------------------- нормализация и дедуп
TRACKING = re.compile(
    r"^(utm_|fbclid|gclid|msclkid|mc_cid|mc_eid|ref|ref_src|source|_hsenc|igshid|"
    r"share|at_medium|at_campaign|CMP|smid|guccounter)", re.IGNORECASE)


def canonical_url(url: str) -> str:
    """Снимаем трекинг — самый дешёвый и надёжный слой дедупликации."""
    url = (url or "").strip()
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return url
    if not parts.scheme.startswith("http"):
        return url
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not TRACKING.match(k)]
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    path = re.sub(r"/amp$", "", parts.path.rstrip("/")) or "/"
    return urllib.parse.urlunparse(
        ("https", host, path, "", urllib.parse.urlencode(sorted(query)), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:32]


STOPWORDS = set("""
a an the of for on in to and or with is are was were be been being by at from as it
its this that these those has have had will would can could should new now more most
after before over under how why what when who which you your they their we our
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только
ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни
быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут
где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
""".split())


def signature(text: str) -> str:
    """Множество содержательных слов. Для коротких заголовков это работает
    заметно надёжнее SimHash: перефразировка рушит все шинглы, а слова остаются."""
    tokens = re.findall(r"[a-zа-яё0-9]+", (text or "").lower())
    return " ".join(sorted({t for t in tokens if len(t) > 1 and t not in STOPWORDS}))


def similarity(sig_a: str, sig_b: str) -> float:
    """0.5*Жаккар + 0.5*перекрытие. Перекрытие спасает, когда один заголовок
    заметно длиннее другого — частый случай у агрегаторов."""
    a, b = set(sig_a.split()), set(sig_b.split())
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return 0.5 * (inter / len(a | b)) + 0.5 * (inter / min(len(a), len(b)))


# --------------------------------------------------------------------- база
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    url_hash     TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    tier         INTEGER NOT NULL DEFAULT 2,
    category     TEXT NOT NULL DEFAULT 'other',
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    fetched_at   TEXT NOT NULL,
    sig          TEXT NOT NULL DEFAULT '',
    social       REAL NOT NULL DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);

CREATE TABLE IF NOT EXISTS sent (
    url_hash    TEXT PRIMARY KEY,
    sig         TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    digest_date TEXT NOT NULL,
    sent_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sent_at ON sent(sent_at);

CREATE TABLE IF NOT EXISTS health (
    source_id  TEXT PRIMARY KEY,
    ok_at      TEXT,
    err        TEXT,
    err_at     TEXT,
    fails      INTEGER NOT NULL DEFAULT 0,
    last_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id      INTEGER PRIMARY KEY,
    kind    TEXT NOT NULL,
    at      TEXT NOT NULL,
    status  TEXT NOT NULL,
    stats   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn


def meta_get(conn, key, default=""):
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def meta_set(conn, key, value):
    conn.execute("INSERT INTO meta(k, v) VALUES (?, ?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))
    conn.commit()


def log_run(conn, kind, status, stats):
    conn.execute("INSERT INTO runs(kind, at, status, stats) VALUES (?,?,?,?)",
                 (kind, now_iso(), status, json.dumps(stats, ensure_ascii=False)))
    conn.execute("DELETE FROM runs WHERE id NOT IN "
                 "(SELECT id FROM runs ORDER BY id DESC LIMIT 200)")
    conn.commit()


# ------------------------------------------------------------------- сбор
def fetch_source(src):
    """(id, url, tier, category) -> (src, items, error). Не бросает исключений."""
    source_id, url, tier, category = src
    try:
        status, raw = http_get(url)
        if status in (403, 405, 429, 451):      # похоже на защиту от ботов — пробуем ещё
            status, raw = http_get(url, ua=CFG["fallback_user_agent"])
        if status != 200 or not raw:
            return src, [], "HTTP %s" % status
        entries = parse_feed(raw)
    except Exception as exc:  # noqa: BLE001 — падение источника не роняет прогон
        return src, [], "%s: %s" % (type(exc).__name__, exc)

    window = datetime.now(timezone.utc) - timedelta(hours=CFG["window_hours"])
    out = []
    for entry in entries[: CFG["max_per_feed"]]:
        published = entry["published"]
        if published and published < window:
            continue
        title, body = entry["title"], entry["summary"]
        out.append({
            "url_hash": url_hash(entry["link"]),
            "url": canonical_url(entry["link"]),
            "source_id": source_id,
            "tier": tier,
            "category": category,
            "title": title,
            "summary": body[:700],
            "published_at": published.isoformat(timespec="seconds") if published else None,
            "sig": signature(title + " " + body[:250]),
            "social": 0.0,
        })
    return src, out, ""


def fetch_hackernews():
    """HN даёт готовый числовой сигнал важности — баллы и комментарии."""
    since = int(time.time()) - CFG["window_hours"] * 3600
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story"
           "&numericFilters=created_at_i>%d,points>%d&hitsPerPage=80"
           % (since, CFG["hn_min_points"]))
    try:
        status, raw = http_get(url, timeout=20)
        hits = json.loads(raw.decode("utf-8", "replace")).get("hits", []) if status == 200 else []
    except Exception as exc:  # noqa: BLE001
        log.warning("Hacker News недоступен: %s", exc)
        return []

    keywords = [k.lower() for k in profile()["keywords"]]
    out = []
    for hit in hits:
        title = strip_html(hit.get("title") or "", 300)
        if not title or not any(k in title.lower() for k in keywords):
            continue
        link = hit.get("url") or ("https://news.ycombinator.com/item?id=%s"
                                  % hit.get("objectID"))
        points = float(hit.get("points") or 0)
        created = datetime.fromtimestamp(
            hit.get("created_at_i", time.time()), timezone.utc)
        out.append({
            "url_hash": url_hash(link),
            "url": canonical_url(link),
            "source_id": "hackernews",
            "tier": CFG["hn_tier"],
            "category": "community",
            "title": title,
            "summary": "Hacker News: %d баллов, %d комментариев."
                       % (int(points), hit.get("num_comments") or 0),
            "published_at": created.isoformat(timespec="seconds"),
            "sig": signature(title),
            "social": min(points / 300.0, 1.0),
        })
    return out


def is_muted(conn, source_id) -> bool:
    """Сломанный источник молчит сутки, потом пробуем снова — сам вернётся в строй."""
    row = conn.execute("SELECT fails, err_at FROM health WHERE source_id=?",
                       (source_id,)).fetchone()
    if not row or row["fails"] < CFG["mute_after_fails"] or not row["err_at"]:
        return False
    last = parse_date(row["err_at"])
    return bool(last and datetime.now(timezone.utc) - last < timedelta(hours=24))


def mark_health(conn, source_id, ok, err="", count=0):
    if ok:
        conn.execute(
            "INSERT INTO health(source_id, ok_at, fails, last_count) VALUES (?,?,0,?) "
            "ON CONFLICT(source_id) DO UPDATE SET ok_at=excluded.ok_at, fails=0, "
            "last_count=excluded.last_count", (source_id, now_iso(), count))
    else:
        conn.execute(
            "INSERT INTO health(source_id, err, err_at, fails) VALUES (?,?,?,1) "
            "ON CONFLICT(source_id) DO UPDATE SET err=excluded.err, "
            "err_at=excluded.err_at, fails=health.fails+1",
            (source_id, err[:200], now_iso()))
    conn.commit()


def collect() -> dict:
    conn = db()
    stats = {"ok": 0, "failed": 0, "muted": 0, "fetched": 0, "new": 0}
    sources = [s for s in profile()["feeds"] if not is_muted(conn, s[0])]
    stats["muted"] = len(profile()["feeds"]) - len(sources)

    rows = []
    with ThreadPoolExecutor(max_workers=CFG["concurrency"]) as pool:
        for src, items, err in pool.map(fetch_source, sources):
            if err:
                stats["failed"] += 1
                mark_health(conn, src[0], False, err)
                log.warning("%s: %s", src[0], err)
            else:
                stats["ok"] += 1
                mark_health(conn, src[0], True, count=len(items))
                rows.extend(items)

    if CFG["use_hackernews"]:
        rows.extend(fetch_hackernews())

    stats["fetched"] = len(rows)
    before = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    for row in rows:
        conn.execute(
            "INSERT INTO items(url_hash,url,source_id,tier,category,title,summary,"
            "published_at,fetched_at,sig,social) "
            "VALUES (:url_hash,:url,:source_id,:tier,:category,:title,:summary,"
            ":published_at,:fetched_at,:sig,:social) "
            "ON CONFLICT(url_hash) DO UPDATE SET social=MAX(items.social, excluded.social)",
            dict(row, fetched_at=now_iso()))
    conn.commit()
    stats["new"] = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] - before

    cutoff_i = (datetime.now(timezone.utc)
                - timedelta(days=CFG["keep_items_days"])).isoformat()
    cutoff_s = (datetime.now(timezone.utc)
                - timedelta(days=CFG["keep_sent_days"])).isoformat()
    conn.execute("DELETE FROM items WHERE fetched_at < ? AND state != 'sent'", (cutoff_i,))
    conn.execute("DELETE FROM sent WHERE sent_at < ?", (cutoff_s,))
    conn.commit()

    meta_set(conn, "last_collect", now_iso())
    log_run(conn, "collect", "ok", stats)
    conn.close()
    log.info("Сбор: источников ok=%d, ошибок=%d, отключено=%d, получено=%d, новых=%d",
             stats["ok"], stats["failed"], stats["muted"], stats["fetched"], stats["new"])
    return stats


# ------------------------------------------------------------ отбор и скоринг
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


def already_sent(conn, group, threshold) -> bool:
    """Межсуточный дедуп: не повторяем то, что уже уходило."""
    hashes = [i["url_hash"] for i in group]
    marks = ",".join("?" * len(hashes))
    if conn.execute("SELECT 1 FROM sent WHERE url_hash IN (%s) LIMIT 1" % marks,
                    hashes).fetchone():
        return True
    main = primary_of(group)
    for row in conn.execute("SELECT sig FROM sent"):
        if similarity(main["sig"], row["sig"]) >= threshold:
            return True
    return False


# ---------------------------------------------------------------- DeepSeek
class LLMError(RuntimeError):
    pass


def llm_json(system: str, user: str, model: str, max_tokens: int = 3000):
    """Один вызов DeepSeek в режиме JSON. Возвращает (данные, usage)."""
    if not DS_KEY:
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
    headers = {"Authorization": "Bearer " + DS_KEY}
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


# ---------------------------------------------------------------- Telegram
TG_LIMIT = 4096


def tg_call(method, payload, attempts=4):
    if not TG_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
    url = "https://api.telegram.org/bot%s/%s" % (TG_TOKEN, method)
    last = ""
    for attempt in range(1, attempts + 1):
        status, data, err = post_json(url, payload, timeout=30)
        if data and data.get("ok"):
            return data["result"]
        code = (data or {}).get("error_code", status)
        desc = (data or {}).get("description", err)
        last = "%s: %s" % (code, desc)
        if code == 429:
            wait = float(((data or {}).get("parameters") or {}).get("retry_after", 5))
            log.warning("Telegram 429, ждём %.0fs", wait)
            time.sleep(wait + 0.5)
            continue
        if code and 400 <= int(code) < 500:
            raise RuntimeError("Telegram отклонил запрос: %s" % last)
        if attempt < attempts:
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError("Telegram недоступен: %s" % last)


def tg_send(chat_id, text):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": not CFG["link_preview"],
               "disable_notification": bool(CFG["silent"])}
    try:
        return tg_call("sendMessage", payload)
    except RuntimeError as exc:
        message = str(exc).lower()
        if "parse" not in message and "entit" not in message and "tag" not in message:
            raise
        log.warning("HTML не принят (%s), повторяю простым текстом", exc)
        payload.pop("parse_mode")
        payload["text"] = html_mod.unescape(re.sub(r"<[^>]+>", "", text))[:TG_LIMIT]
        return tg_call("sendMessage", payload)


def tg_detect_chat():
    """Достаёт chat_id из последних апдейтов — чтобы не искать его вручную."""
    updates = tg_call("getUpdates", {"limit": 20, "timeout": 0})
    for upd in reversed(updates):
        for key in ("message", "channel_post", "edited_message", "my_chat_member"):
            obj = upd.get(key) or {}
            chat = obj.get("chat") or {}
            if chat.get("id"):
                title = chat.get("title") or chat.get("username") or chat.get("first_name")
                return str(chat["id"]), (title or ""), chat.get("type", "")
    return None, "", ""


# ------------------------------------------------------------------ рендер
EMOJI = {"labs": "🚀", "research": "🔬", "opensource": "🛠", "media": "📰",
         "community": "💬", "business": "💰", "policy": "⚖️", "other": "📌"}
MONTHS = ("января февраля марта апреля мая июня июля августа сентября октября "
          "ноября декабря").split()


def esc(text) -> str:
    return html_mod.escape(str(text or ""), quote=False)


def render(cards, scanned, trim=0):
    """trim: 0 — полный вид, 1 — без «почему», 2 — только заголовки со ссылками."""
    day = local_now()
    head = ["📡 <b>Дайджест · %d %s</b>" % (day.day, MONTHS[day.month - 1]),
            "<i>%d из %d материалов за сутки</i>" % (len(cards), scanned), ""]
    blocks = []
    for num, (card, group, score, category) in enumerate(cards, 1):
        main = primary_of(group)
        title = card.get("headline") or main["title"]
        others = sorted({i["source_id"] for i in group} - {main["source_id"]})[:2]
        also = " · " + esc(", ".join(others)) if others else ""
        link = '🔗 <a href="%s">%s</a>%s · ⭐ %.1f' % (
            esc(main["url"]), esc(main["source_id"]), also, score)
        if trim >= 2:
            blocks.append("%s <b>%d. %s</b>\n%s"
                          % (EMOJI.get(category, "📌"), num, esc(title), link))
            continue
        lines = ["%s <b>%d. %s</b>" % (EMOJI.get(category, "📌"), num, esc(title))]
        what = str(card.get("what") or main["summary"][:300]).strip()
        if what:
            lines.append(esc(what))
        why = str(card.get("why") or "").strip()
        if why and trim == 0:
            lines.append("💡 " + esc(why))
        lines.append(link)
        blocks.append("\n".join(lines))
    return "\n".join(head) + "\n" + "\n\n".join(blocks)


def fit_message(cards, scanned):
    """Возвращает список сообщений. Сначала пытаемся уместить всё в одно."""
    for trim in (0, 1, 2):
        text = render(cards, scanned, trim)
        if len(text) <= TG_LIMIT - 60:
            if trim and CFG["one_message"]:
                log.info("Сообщение длинное — сократил детализацию (уровень %d)", trim)
            return [text]
    half = max(len(cards) // 2, 1)          # всё ещё длинно — режем по новостям
    if len(cards) <= 1:
        return [render(cards, scanned, 2)[:TG_LIMIT]]
    return fit_message(cards[:half], scanned) + fit_message(cards[half:], scanned)


# -------------------------------------------------------------------- прогон
def build_and_send(dry_run=False) -> dict:
    conn = db()
    stats = {"candidates": 0, "clusters": 0, "selected": 0, "sent": 0, "cost": 0.0}
    prof = profile()

    window = (datetime.now(timezone.utc)
              - timedelta(hours=CFG["window_hours"])).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE fetched_at > ? AND state != 'sent' "
        "ORDER BY published_at DESC", (window,))]
    stats["candidates"] = len(rows)
    if not rows:
        log.warning("Нет свежих материалов — дайджест не формируется")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    groups = cluster(rows, CFG["similarity"])
    fresh = [g for g in groups if not already_sent(conn, g, CFG["similarity"])]
    stats["clusters"] = len(fresh)
    if not fresh:
        log.warning("После дедупликации новых новостей не осталось")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    shortlist = sorted(fresh, key=prescore, reverse=True)[: CFG["llm_candidates"]]

    # 1) ранжирование
    try:
        ranking, usage = rank_clusters(shortlist, prof["persona"])
        stats["cost"] += llm_cost(usage)
    except LLMError as exc:                 # деградируем, но выпуск не срываем
        log.error("Ранжирование не удалось (%s) — беру порядок прескоринга", exc)
        ranking = [{"id": i, "score": 7.0, "category": primary_of(g)["category"]}
                   for i, g in enumerate(shortlist)]

    picked = select(ranking, shortlist)
    if not picked:
        log.warning("Ничего не прошло порог важности %.1f — тихий день", CFG["min_score"])
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats
    if len(picked) < CFG["min_items"]:
        log.info("Отобрано только %d новостей — это нормально для тихого дня", len(picked))

    # 2) саммари одним запросом на весь выпуск
    try:
        cards_map, usage = summarize(picked, prof["persona"], CFG["language"])
        stats["cost"] += llm_cost(usage)
    except LLMError as exc:
        log.error("Саммари не удалось (%s) — публикую исходные заголовки", exc)
        cards_map = {}

    cards = []
    for idx, (group, score, category) in enumerate(picked):
        main = primary_of(group)
        card = cards_map.get(idx) or {"headline": main["title"],
                                      "what": main["summary"][:300], "why": ""}
        cards.append((card, group, score, category))
    stats["selected"] = len(cards)

    messages = fit_message(cards, stats["candidates"])

    if dry_run:
        print()
        print(("\n" + "─" * 60 + "\n").join(
            html_mod.unescape(re.sub(r"<[^>]+>", "", m)) for m in messages))
        print("\n[dry-run] отправки не было. Примерная стоимость запроса: $%.4f"
              % stats["cost"])
        log_run(conn, "digest", "dry-run", stats)
        conn.close()
        return stats

    for text in messages:
        tg_send(TG_CHAT, text)
        stats["sent"] += 1
        time.sleep(1.0)

    day = local_now().strftime("%Y-%m-%d")
    for _card, group, _score, _cat in cards:
        main = primary_of(group)
        conn.execute("INSERT OR IGNORE INTO sent(url_hash,sig,title,url,digest_date,sent_at)"
                     " VALUES (?,?,?,?,?,?)",
                     (main["url_hash"], main["sig"], main["title"], main["url"],
                      day, now_iso()))
        for item in group:
            conn.execute("UPDATE items SET state='sent' WHERE url_hash=?",
                         (item["url_hash"],))
    conn.commit()
    meta_set(conn, "last_digest_date", day)
    log_run(conn, "digest", "ok", stats)
    conn.close()
    log.info("Отправлено: %d новостей, %d сообщение(й), ~$%.4f",
             stats["selected"], stats["sent"], stats["cost"])
    return stats


# -------------------------------------------------------------------- демон
def daemon():
    log.info("Демон запущен. Тема: %s. Отправка в %s (%s). Сбор раз в %d ч.",
             CFG["topic"], CFG["send_at"], tz_label(), CFG["collect_every_h"])
    log.info("Каталог данных: %s | лог: %s", HOME, LOG_FILE)
    require_secrets()
    try:
        hour, minute = [int(x) for x in CFG["send_at"].split(":")]
    except ValueError:
        sys.exit("Некорректное время отправки: %r. Формат ЧЧ:ММ" % CFG["send_at"])

    while True:
        try:
            conn = db()
            last_collect = parse_date(meta_get(conn, "last_collect", ""))
            last_digest = meta_get(conn, "last_digest_date", "")
            conn.close()

            need_collect = (last_collect is None or
                            datetime.now(timezone.utc) - last_collect
                            >= timedelta(hours=CFG["collect_every_h"]))
            now = local_now()
            today = now.strftime("%Y-%m-%d")
            due = now.hour * 60 + now.minute >= hour * 60 + minute
            need_digest = due and last_digest != today

            if need_digest:
                log.info("Время дайджеста — собираю свежее и отправляю")
                collect()
                build_and_send()
            elif need_collect:
                collect()
        except Exception as exc:  # noqa: BLE001 — демон не должен умирать
            log.exception("Ошибка в цикле: %s", exc)
        time.sleep(60)


# ------------------------------------------------------------------- команды
def require_secrets():
    missing = [name for name, val in (("TELEGRAM_BOT_TOKEN", TG_TOKEN),
                                      ("TELEGRAM_CHAT_ID", TG_CHAT),
                                      ("DEEPSEEK_API_KEY", DS_KEY)) if not val]
    if missing:
        sys.exit("Не заданы: %s\nЗапустите: python3 %s setup"
                 % (", ".join(missing), Path(__file__).name))


def write_env(values: dict):
    HOME.mkdir(parents=True, exist_ok=True)
    existing = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.split("=", 1)
                existing[key.strip()] = val.strip()
    existing.update({k: v for k, v in values.items() if v not in (None, "")})
    body = ["# Секреты и настройки дайджеста. Права 600, в git не класть.",
            "# Всё, кроме трёх верхних строк, можно менять и в самом digest.py.", ""]
    for key in sorted(existing):
        body.append("%s=%s" % (key, existing[key]))
    ENV_FILE.write_text("\n".join(body) + "\n", encoding="utf-8")
    os.chmod(str(ENV_FILE), 0o600)


def ask(prompt, default="", secret=False):
    suffix = " [%s]" % default if default else ""
    while True:
        if secret:
            value = getpass.getpass("%s%s: " % (prompt, suffix)).strip()
        else:
            value = input("%s%s: " % (prompt, suffix)).strip()
        if value:
            return value
        if default != "":
            return default
        print("  нужно значение")


def cmd_setup(_args):
    global TG_TOKEN, TG_CHAT, DS_KEY
    print("\n=== Настройка дайджеста ===")
    print("Каталог данных: %s\n" % HOME)

    have = "уже задан, Enter — оставить" if TG_TOKEN else ""
    token = ask("1/6 Токен Telegram-бота (от @BotFather)", have, secret=True)
    if token != have:
        TG_TOKEN = token
    try:
        bot = tg_call("getMe", {})
        print("      бот: @%s\n" % bot.get("username"))
    except Exception as exc:  # noqa: BLE001
        print("      не смог проверить токен: %s\n" % exc)

    print("2/6 chat_id. СНАЧАЛА напишите боту любое сообщение в Telegram")
    print("    (для канала: добавьте бота админом и опубликуйте пост).")
    typed = input("    Enter — определить автоматически, либо введите chat_id: ").strip()
    chat = TG_CHAT
    if typed:
        chat = typed
        print("      принято: %s\n" % chat)
    else:
        try:
            found, title, kind = tg_detect_chat()
            if found:
                chat = found
                print("      найден: %s (%s, %s)\n" % (found, title, kind))
            else:
                print("      апдейтов нет — вы ещё не писали боту.")
                print("      Напишите ему и выполните: python3 %s chatid\n"
                      % Path(__file__).name)
        except Exception as exc:  # noqa: BLE001
            print("      ошибка: %s\n" % exc)
    TG_CHAT = chat

    have = "уже задан, Enter — оставить" if DS_KEY else ""
    key = ask("3/6 Ключ DeepSeek (platform.deepseek.com)", have, secret=True)
    if key != have:
        DS_KEY = key

    topics = ", ".join(PROFILES)
    topic = ask("4/6 Тема (%s)" % topics, CFG["topic"])
    if topic not in PROFILES:
        print("      неизвестная тема, оставляю %s" % CFG["topic"])
        topic = CFG["topic"]

    send_at = ask("5/6 Время отправки ЧЧ:ММ (по вашему времени)", CFG["send_at"])
    tz = ask("    Часовой пояс (например Europe/Riga, Europe/Warsaw)", CFG["tz"])
    count = ask("6/6 Сколько новостей в выпуске (5-10)", str(CFG["max_items"]))

    write_env({
        "TELEGRAM_BOT_TOKEN": TG_TOKEN,
        "TELEGRAM_CHAT_ID": TG_CHAT,
        "DEEPSEEK_API_KEY": DS_KEY,
        "ND_TOPIC": topic,
        "ND_SEND_AT": send_at,
        "ND_TZ": tz,
        "ND_MAX_ITEMS": count,
    })
    print("\nСохранено в %s (права 600).\n" % ENV_FILE)
    print("Дальше:")
    print("  python3 %s doctor            # проверить связь" % Path(__file__).name)
    print("  python3 %s run --dry-run     # посмотреть выпуск в терминале"
          % Path(__file__).name)
    print("  python3 %s daemon            # запустить фоном" % Path(__file__).name)
    return 0


def cmd_chatid(_args):
    """Определить chat_id и дописать его в env, не трогая остальные настройки."""
    if not TG_TOKEN:
        sys.exit("Сначала задайте токен: python3 %s setup" % Path(__file__).name)
    bot = tg_call("getMe", {})
    print("Бот: @%s" % bot.get("username"))
    found, title, kind = tg_detect_chat()
    if not found:
        print("\nchat_id не найден. Что сделать:")
        print("  1. Откройте Telegram и напишите боту @%s любое сообщение"
              % bot.get("username"))
        print("     (для канала: добавьте бота администратором и опубликуйте пост)")
        print("  2. Запустите эту команду снова: python3 %s chatid"
              % Path(__file__).name)
        return 1
    write_env({"TELEGRAM_CHAT_ID": found})
    print("Найден и сохранён: %s  (%s, %s)" % (found, title or "без названия", kind))
    print("\nПроверьте: python3 %s doctor" % Path(__file__).name)
    return 0


def cmd_doctor(_args):
    print("Каталог      :", HOME)
    print("Тема         :", CFG["topic"], "| источников:", len(profile()["feeds"]))
    print("Часовой пояс :", tz_label(), "| сейчас у вас", local_now().strftime("%H:%M"),
          "| на сервере", datetime.now(timezone.utc).strftime("%H:%M UTC"))
    print("Расписание   : отправка в %s, сбор раз в %d ч"
          % (CFG["send_at"], CFG["collect_every_h"]))
    print("Новостей     : %d-%d, порог важности %.1f"
          % (CFG["min_items"], CFG["max_items"], CFG["min_score"]))
    print("Модели       :", CFG["model_rank"], "/", CFG["model_summary"])
    print()
    good = True

    if TG_TOKEN:
        try:
            bot = tg_call("getMe", {})
            print("[OK]   Telegram: @%s" % bot.get("username"))
        except Exception as exc:  # noqa: BLE001
            print("[FAIL] Telegram: %s" % exc)
            good = False
    else:
        print("[FAIL] Telegram: нет TELEGRAM_BOT_TOKEN")
        good = False

    if TG_CHAT:
        print("[OK]   chat_id: %s" % TG_CHAT)
    else:
        print("[FAIL] chat_id не задан")
        good = False

    if DS_KEY:
        try:
            data, usage = llm_json("Отвечай только json.",
                                   'Верни ровно {"items": [{"id": 0, "ok": true}]}',
                                   CFG["model_rank"], max_tokens=60)
            print("[OK]   DeepSeek отвечает (%s), стоимость проверки ~$%.6f"
                  % (CFG["model_rank"], llm_cost(usage)))
        except Exception as exc:  # noqa: BLE001
            print("[FAIL] DeepSeek: %s" % exc)
            good = False
        status, balance, _ = post_json(
            CFG["llm_base"].rstrip("/") + "/user/balance", {},
            {"Authorization": "Bearer " + DS_KEY}, 20)
        if status == 200 and balance:
            infos = balance.get("balance_infos") or []
            if infos:
                print("[OK]   Баланс DeepSeek: %s %s"
                      % (infos[0].get("total_balance"), infos[0].get("currency")))
    else:
        print("[FAIL] DeepSeek: нет DEEPSEEK_API_KEY")
        good = False

    try:
        conn = db()
        count = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        size = DB_FILE.stat().st_size / 1e6 if DB_FILE.exists() else 0
        print("[OK]   База: %d материалов, %.1f МБ" % (count, size))
        conn.close()
    except sqlite3.Error as exc:
        print("[FAIL] База: %s" % exc)
        good = False

    print("\nИТОГ:", "готово к запуску" if good else "есть проблемы, см. [FAIL] выше")
    return 0 if good else 1


def cmd_feeds(_args):
    print("Проверяю %d источников...\n" % len(profile()["feeds"]))
    with ThreadPoolExecutor(max_workers=CFG["concurrency"]) as pool:
        for src, items, err in pool.map(fetch_source, profile()["feeds"]):
            mark = "ok  " if not err else "FAIL"
            print("  [%s] %-20s %3d свежих  %s" % (mark, src[0], len(items), err[:60]))
    return 0


def cmd_collect(_args):
    collect()
    return 0


def cmd_run(args):
    if not args.dry_run:
        require_secrets()
    elif not DS_KEY:
        sys.exit("Для dry-run нужен хотя бы DEEPSEEK_API_KEY")
    if not args.no_collect:
        collect()
    build_and_send(dry_run=args.dry_run)
    return 0


def cmd_status(_args):
    conn = db()
    print("=== Последние прогоны ===")
    for row in conn.execute("SELECT kind, at, status, stats FROM runs "
                            "ORDER BY id DESC LIMIT 12"):
        print("  %s  %-8s %-8s %s" % (row["at"][:16], row["kind"], row["status"],
                                      row["stats"][:90]))
    day = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    fresh = conn.execute("SELECT COUNT(*) c FROM items WHERE fetched_at > ?",
                         (day,)).fetchone()["c"]
    sent = conn.execute("SELECT COUNT(*) c FROM sent WHERE sent_at > ?",
                        (week,)).fetchone()["c"]
    print("\nМатериалов за сутки: %d   отправлено за неделю: %d" % (fresh, sent))

    cost = 0.0
    for row in conn.execute("SELECT stats FROM runs WHERE at > ?", (week,)):
        try:
            cost += float(json.loads(row["stats"]).get("cost", 0))
        except (ValueError, TypeError):
            pass
    print("Расход DeepSeek за неделю: $%.4f  (≈ $%.2f/мес)" % (cost, cost * 4.3))

    print("\n=== Проблемные источники ===")
    bad = list(conn.execute("SELECT source_id, fails, err FROM health WHERE fails > 0 "
                            "ORDER BY fails DESC LIMIT 15"))
    if not bad:
        print("  нет — все источники отвечают")
    for row in bad:
        print("  %-22s сбоев подряд: %-3d %s"
              % (row["source_id"], row["fails"], (row["err"] or "")[:55]))

    size = DB_FILE.stat().st_size / 1e6 if DB_FILE.exists() else 0
    print("\nРазмер базы: %.1f МБ   каталог: %s" % (size, HOME))
    conn.close()
    return 0


SERVICE_TEMPLATE = """[Unit]
Description=News digest bot (Telegram)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={workdir}
ExecStart={python} {script} --log-file daemon
Restart=always
RestartSec=60
TimeoutStopSec=20

# Логи пишутся в {workdir}/digest.log с ротацией (3 x 2 МБ).
# В systemd journal не идут — он не растёт из-за этого сервиса.
StandardOutput=journal
StandardError=journal
# Лог событий идёт в свой файл с ротацией; в journal попадают только
# строки старта и аварий — он от этого практически не растёт.
SyslogIdentifier=newsdigest

# Лимиты: сервис физически не может помешать VPN, nginx и postgres
MemoryMax=250M
MemoryAccounting=yes
CPUQuota=35%
CPUAccounting=yes
{weights}
Nice=15
IOSchedulingClass=idle

# Изоляция
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
"""


def cgroup_v2() -> bool:
    return Path("/sys/fs/cgroup/cgroup.controllers").exists()


def cmd_service(_args):
    user = (os.environ.get("SUDO_USER") or os.environ.get("USER")
            or os.environ.get("LOGNAME") or "root")
    v2 = cgroup_v2()
    weights = ("CPUWeight=20\nIOWeight=20" if v2
               else "CPUShares=256\nBlockIOWeight=100")
    unit = SERVICE_TEMPLATE.format(
        user=user, workdir=str(HOME),
        python=shutil.which("python3") or "/usr/bin/python3",
        script=str(Path(__file__).resolve()), weights=weights)
    path = HOME / "newsdigest.service"
    HOME.mkdir(parents=True, exist_ok=True)
    path.write_text(unit, encoding="utf-8")
    print(unit)
    print("cgroup: %s — лимиты записаны в подходящем формате\n"
          % ("v2 (unified)" if v2 else "v1 (legacy)"))
    if os.geteuid() == 0:
        print("ВНИМАНИЕ: команда запущена от root, поэтому каталог данных = %s,\n"
              "  а в юните User=%s. Это рассогласование сломает запуск.\n"
              "  Запустите setup и service БЕЗ sudo, от обычного пользователя.\n"
              % (HOME, user))
    print("Файл сохранён: %s\n" % path)
    print("Установка (sudo нужен только на эти три команды):")
    print("  sudo cp %s /etc/systemd/system/newsdigest.service" % path)
    print("  sudo systemctl daemon-reload")
    print("  sudo systemctl enable --now newsdigest")
    print("  systemctl status newsdigest --no-pager")
    print("\nПроверить, что лимиты применились:")
    print("  systemctl show newsdigest -p MemoryMax -p CPUQuotaPerSecUSec")
    print("\nБез root можно и так:")
    print("  nohup python3 %s daemon --log-file >/dev/null 2>&1 &"
          % Path(__file__).resolve())
    return 0


def main(argv=None):
    # Общие флаги вынесены в родителя, чтобы работали в ЛЮБОЙ позиции:
    # и `digest.py --log-file daemon`, и `digest.py daemon --log-file`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--log-file", action="store_true",
                        help="писать лог в ~/.newsdigest/digest.log с ротацией")

    parser = argparse.ArgumentParser(
        prog="digest.py", description="Ежедневный дайджест новостей в Telegram",
        parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    _add = sub.add_parser

    def add_parser(name, **kw):
        kw.setdefault("parents", [common])
        return _add(name, **kw)

    sub.add_parser = add_parser

    sub.add_parser("setup", help="мастер настройки").set_defaults(func=cmd_setup)
    sub.add_parser("chatid", help="определить chat_id и сохранить").set_defaults(
        func=cmd_chatid)
    sub.add_parser("doctor", help="проверить Telegram, DeepSeek, базу").set_defaults(
        func=cmd_doctor)
    sub.add_parser("feeds", help="проверить каждый источник").set_defaults(func=cmd_feeds)
    sub.add_parser("collect", help="только собрать новости").set_defaults(
        func=cmd_collect)

    run = sub.add_parser("run", help="собрать и отправить дайджест сейчас")
    run.add_argument("--dry-run", action="store_true", help="показать, не отправлять")
    run.add_argument("--no-collect", action="store_true",
                     help="не собирать заново, взять из базы")
    run.set_defaults(func=cmd_run)

    sub.add_parser("daemon", help="фоновый режим по расписанию").set_defaults(
        func=lambda a: daemon())
    sub.add_parser("status", help="прогоны, расход, здоровье источников").set_defaults(
        func=cmd_status)
    sub.add_parser("service", help="напечатать unit-файл systemd").set_defaults(
        func=cmd_service)

    args = parser.parse_args(argv)
    load_env()
    setup_logging(args.verbose, args.log_file)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())