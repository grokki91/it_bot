# -*- coding: utf-8 -*-
"""Настройки, окружение, часовой пояс, логирование.

Правьте значения прямо здесь. Всё, что помечено [env], можно переопределить
в ~/.newsdigest/env — там значения главнее (мастер `setup` пишет их туда сам).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

# =============================================================================
#  Р А З Д Е Л   Н А С Т Р О Е К
# =============================================================================

CFG = {
    # --- что и когда ---------------------------------------------------------
    "topic":            "ai",      # [env ND_TOPIC] раздел по умолчанию: он идёт
                                   # в /news без аргумента и в срочные новости
    "sections":         "",        # [env ND_SECTIONS] разделы утреннего выпуска
                                   # через запятую. Пусто = подборка по умолчанию
                                   # (profiles.DEFAULT_SECTIONS). Список всех
                                   # разделов — команда /sections
    "language":         "русский", # [env ND_LANGUAGE] язык дайджеста
    "send_at":          "09:00",   # [env ND_SEND_AT] во сколько отправлять (ВАШЕ время)
    "tz":               "Europe/Riga",  # [env ND_TZ] пояс по имени: сам учитывает
                                   # переход на летнее время. Пусто = использовать tz_offset.
    "tz_offset":        3,         # [env ND_TZ_OFFSET] запасной вариант, если пояс не найден
    "collect_every_h":  4,         # [env ND_COLLECT_EVERY] раз в сколько часов собирать

    # --- сколько новостей ----------------------------------------------------
    "per_section":      2,         # [env ND_PER_SECTION] новостей на раздел утром
    "section_items":    5,         # сколько отдаёт /news <раздел> без числа
    "section_max_items":10,        # и сколько максимум можно попросить
    "section_candidates": 14,      # кандидатов раздела показываем модели
    "section_workers":  3,         # разделов ранжируем параллельно (запросы к LLM)
    "summary_batch":    10,        # карточек в одном запросе на саммари
    "min_items":        5,         # [env ND_MIN_ITEMS] меньше — просто тихий день, не ошибка
    "max_items":        8,         # [env ND_MAX_ITEMS] сколько новостей в выпуске (5-10)
    "min_score":        5.5,       # [env ND_MIN_SCORE] порог важности 1-10, ниже не публикуем
    "max_per_source":   2,         # не даём одному сайту занять весь выпуск
    "max_per_category": 3,         # и одной теме тоже

    # --- сбор ----------------------------------------------------------------
    "window_hours":     30,        # насколько старые материалы ещё считаем свежими
    "http_timeout":     20,        # секунд на один источник
    "concurrency":      8,         # параллельных загрузок. Источников теперь
                                   # больше сотни, а упираются они в сеть, а не в CPU
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
    "listen":           True,      # [env ND_LISTEN] отвечать на команды в чате.
                                   # 0 = только рассылка по расписанию, как в 2.0
    "signup":           "ask",     # [env ND_SIGNUP] что делать с новым чатом:
                                   # ask — спросить владельца кнопками,
                                   # open — подписывать сразу (осторожно: каждый
                                   #        подписчик тратит ваш баланс модели),
                                   # off — отвечать «бот личный» и не пускать

    # --- страница в браузере -------------------------------------------------
    # То же, что бот в Telegram, но по адресу http://<ip-вашего-vps>:8080.
    # Пароль (web_token) создаётся сам при первом запуске и пишется в env.
    "web":              True,       # [env ND_WEB] поднимать страницу вместе с демоном
    "web_host":         "0.0.0.0",  # [env ND_WEB_HOST] 0.0.0.0 = видно по IP VPS,
                                    # 127.0.0.1 = только с самой машины (через ssh-туннель)
    "web_port":         8080,       # [env ND_WEB_PORT]
    "web_token":        "",         # [env ND_WEB_TOKEN] пароль страницы; пусто = создам сам

    # --- срочные новости (вне расписания) ------------------------------------
    # Событие, о котором за пару часов написали сразу несколько первоисточников,
    # ждать до утра не должно. Условия нарочно строгие: одно ложное «срочно»
    # раздражает сильнее, чем десять пропущенных.
    "breaking":           True,   # [env ND_BREAKING] присылать срочное сразу
    "breaking_window_h":  6,      # за какое окно считаем подтверждения
    "breaking_min_sources": 3,    # столько РАЗНЫХ сайтов, и хотя бы один tier-1
    "breaking_social":    0.9,    # либо ~270+ баллов Hacker News в одиночку
    "breaking_min_score": 8.0,    # и оценка модели не ниже (1-10)
    "breaking_max_per_day": 2,    # больше двух срочных в сутки — это уже лента
    "breaking_quiet":     "23:00-08:00",  # в эти часы молчим (ваше время)

    # --- обратная связь ------------------------------------------------------
    "feedback_buttons": True,      # [env ND_FEEDBACK] кнопки 👍/👎/🔖 под выпуском
    "feedback_weight":  0.25,      # насколько реакции двигают прескоринг.
                                   # 0 = кнопки собирают статистику, но ни на что
                                   # не влияют; 0.5 — вкусы почти важнее свежести

    # --- хранение ------------------------------------------------------------
    "keep_items_days":  10,
    "keep_sent_days":   60,        # история отправленного = защита от повторов
    # Часть сайтов за Cloudflare (openai.com, theverge) отдаёт 403 незнакомому
    # клиенту. Сначала ходим вежливо, при 403/429 автоматически повторяем
    # запрос вторым User-Agent. Если источник всё равно молчит — виден в `status`.
    "user_agent":       "Mozilla/5.0 (compatible; newsdigest/3.0; personal RSS reader)",
    "fallback_user_agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
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
PROFILES_FILE = HOME / "profiles.json"

#: имя, которым скрипт запускают из терминала — подставляется в подсказки
PROG = "digest.py"
#: путь к точке входа (нужен systemd-юниту)
LAUNCHER = Path(__file__).resolve().parent.parent / "digest.py"

ENV_MAP = {
    "ND_TOPIC": ("topic", str),
    "ND_SECTIONS": ("sections", str),
    "ND_PER_SECTION": ("per_section", int),
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
    "ND_LISTEN": ("listen", lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_FEEDBACK": ("feedback_buttons",
                    lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_BREAKING": ("breaking", lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_BREAKING_QUIET": ("breaking_quiet", str),
    "ND_FEEDBACK_WEIGHT": ("feedback_weight", float),
    "ND_SIGNUP": ("signup", str),
    "ND_WEB": ("web", lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_WEB_HOST": ("web_host", str),
    "ND_WEB_PORT": ("web_port", int),
    "ND_WEB_TOKEN": ("web_token", str),
}

log = logging.getLogger("nd")

# Секреты живут в модуле, а не в замыканиях: их перечитывает `load_env`, и все
# остальные модули обращаются к ним как `config.TG_TOKEN` — всегда актуально.
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


def write_env(values: dict, allow_empty: bool = False) -> None:
    """Дописывает значения в ~/.newsdigest/env, не теряя уже сохранённые.

    Пустые значения по умолчанию пропускаются: полупустой ответ мастера
    настройки не должен затирать уже сохранённый токен. Осознанной записи
    пустоты (например, «часов тишины нет») служит allow_empty.
    """
    HOME.mkdir(parents=True, exist_ok=True)
    existing = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.split("=", 1)
                existing[key.strip()] = val.strip()
    existing.update({k: v for k, v in values.items()
                     if v is not None and (allow_empty or v != "")})
    body = ["# Секреты и настройки дайджеста. Права 600, в git не класть.",
            "# Всё, кроме трёх верхних строк, можно менять и в newsdigest/config.py.", ""]
    for key in sorted(existing):
        body.append("%s=%s" % (key, existing[key]))
    ENV_FILE.write_text("\n".join(body) + "\n", encoding="utf-8")
    os.chmod(str(ENV_FILE), 0o600)


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


# ----------------------------------------------------------------- часовой пояс
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


def to_local(when: datetime) -> datetime:
    """Момент времени (обычно UTC из базы) в вашем поясе, наивный datetime."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if _TZ_NAME:
        return when.astimezone().replace(tzinfo=None)
    return (when.astimezone(timezone.utc)
            + timedelta(hours=CFG["tz_offset"])).replace(tzinfo=None)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
