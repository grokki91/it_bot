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

from . import redact

# =============================================================================
#  Р А З Д Е Л   Н А С Т Р О Е К
# =============================================================================

CFG = {
    # --- что и когда ---------------------------------------------------------
    "topic":            "ai",      # [env ND_TOPIC] раздел по умолчанию: он идёт
                                   # в /news без аргумента и в срочные новости
    "sections":         "",        # [env ND_SECTIONS] разделы планового выпуска
                                   # через запятую. Пусто = подборка по умолчанию
                                   # (profiles.DEFAULT_SECTIONS). Список всех
                                   # разделов — команда /sections
    "favorites":        "",        # [env ND_FAVORITES] до пяти разделов, которые
                                   # идут в выпуске первыми. Пусто = обычный
                                   # порядок. У подписчика может быть свой топ
    "language":         "русский", # [env ND_LANGUAGE] язык дайджеста
    "translate":        True,      # [env ND_TRANSLATE] доводить выпуск до языка
                                   # дайджеста. Источники международные, и это
                                   # правильно: надёжность важнее языка. Но всё,
                                   # что осталось на чужом языке (модель не
                                   # перевела заголовок или вообще не ответила),
                                   # перед отправкой переводится отдельным
                                   # запросом. Проверка работает для русского:
                                   # у него свой алфавит. 0 = как ответила модель
    "send_at":          "09:00",   # [env ND_SEND_AT] во сколько отправлять (ВАШЕ время)
    "per_day":          2,         # [env ND_PER_DAY] выпусков в сутки: 1 — только
                                   # в send_at, 2 — ещё через 12 часов (по
                                   # умолчанию 09:00 и 21:00), 3 — каждые 8 часов.
                                   # Каждый выпуск — отдельные запросы к модели:
                                   # два выпуска в день стоят вдвое дороже одного
    "tz":               "Europe/Riga",  # [env ND_TZ] пояс по имени: сам учитывает
                                   # переход на летнее время. Пусто = использовать tz_offset.
    "tz_offset":        3,         # [env ND_TZ_OFFSET] запасной вариант, если пояс не найден
    "collect_every_h":  4,         # [env ND_COLLECT_EVERY] раз в сколько часов собирать

    # --- сколько новостей ----------------------------------------------------
    "per_section":      2,         # [env ND_PER_SECTION] новостей на раздел в выпуске
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
    "quiet_after_empty": 6,        # после стольких пустых обходов подряд фид
                                   # попадает в «молчащие» в `status`. Ошибки
                                   # нет — он отвечает 200 и ноль записей, — но
                                   # из выпуска выпал, и это надо увидеть
    "use_hackernews":   True,      # добавлять топ Hacker News (без ключа, бесплатно)
    "use_kev":          True,      # каталог CISA KEV: уязвимости, которые
                                   # эксплуатируются прямо сейчас. Один запрос
                                   # в сутки, без ключа. Нужен срочным новостям
                                   # (newsdigest/signals.py)
    "hn_min_points":    80,     # порог баллов HN. Ниже = больше шума с форума
    "hn_tier":          3,      # 3 = агрегатор: если ту же новость дал реальный
                                # сайт, ссылка ведёт на него, а не на тред HN

    # --- раздел новости ------------------------------------------------------
    # Раздел определяется по содержанию, а не по ленте: у Reuters, BBC, phys.org
    # и ScienceDaily в одном фиде идёт всё сразу, и раньше их материалы целиком
    # уезжали в тот раздел, к которому приписан сам источник.
    "classify_llm":     True,      # [env ND_CLASSIFY] спрашивать модель, когда
                                   # словарь правил не решил. 0 = только правила
    "classify_max":     40,        # сколько материалов за прогон отдаём модели.
                                   # Узкие ленты сюда не попадают вовсе, а из
                                   # широких берём самые надёжные и свежие
    "keep_routes_days": 14,        # сколько живёт кэш решений о разделе

    # --- дедупликация --------------------------------------------------------
    "similarity":       0.32,      # 0..1 порог склейки одинаковых новостей.
                                   # Меньше = агрессивнее склейка (риск потерять новость),
                                   # больше = чаще будут дубли одного события.
    # Слова ловят только пересказ. «Умер Тим Карри, звезда Рокки Хоррора» и
    # «Коллеги прощаются с Тимом Карри» — одно событие, но общих слов у них
    # два, и порог их не сводит. Поэтому спорную зону разбирает модель:
    # ниже similarity, но не ниже dup_gray — один вопрос, «одно и то же?».
    "dup_llm":          True,      # [env ND_DUP_LLM] спрашивать модель в спорной зоне
    "dup_gray":         0.05,      # нижняя граница спорной зоны. У «умер Тим
                                   # Карри» и «коллеги прощаются с Тимом Карри»
                                   # совпадение 0.08 — порог должен быть ниже.
                                   # Ниже 0.05 общих слов нет вовсе
    "dup_llm_max":      120,       # пар за прогон. Спорных пар при таком низком
                                   # пороге много, поэтому спрашиваем не про все,
                                   # а по очереди (см. dedup.NOW и dedup.weigh).
                                   # Было 30, и лимит выбирался подчистую: на
                                   # живой базе за 18 дней из 551 пары внутри
                                   # разделов до модели доехало 6. Сотня пар —
                                   # это ~$0.003 к выпуску, дешевле дубля
    "dup_batch":        40,        # пар в одном запросе. Одним куском сотня пар
                                   # упирается в потолок ответа, и обрыв стоит
                                   # ВСЕХ вердиктов сразу; пачками — только своей
    "dup_candidates":   6,         # сколько верхних кандидатов раздела проверяем.
                                   # В выпуск идут один-два, и платить за хвост,
                                   # который всё равно не покажут, незачем
    "dup_window_h":     48,        # насколько назад смотрим в истории. Дальше
                                   # это уже не повтор, а возвращение к теме
    "keep_dupes_days":  30,        # сколько живёт кэш её вердиктов

    # --- куда ведёт ссылка ---------------------------------------------------
    # Всё, что бот показывает, — ссылки из чужих лент, а подписью под ними
    # стоит наше имя источника. `https://apnews.com@phish.tk/login` читается
    # как «apnews», а ведёт на phish.tk. Проверка — каскадом, от бесплатного
    # к дорогому (newsdigest/safety.py).
    "safe_links":       True,   # [env ND_SAFE_LINKS] проверять ссылки при сборе
    "safe_strict":      False,  # публиковать ТОЛЬКО то, за что поручились.
                                # По умолчанию выключено: у половины хороших
                                # ссылок с Hacker News домен нам незнаком, и
                                # строгость выбросила бы источник целиком
    "safe_resolve":     True,   # разворачивать сокращатели (bit.ly, t.co).
                                # Сетевой запрос, но только на них
    "safe_resolve_max": 30,     # сколько сокращателей разворачиваем за прогон
    "safe_hops":        4,      # длина цепочки редиректов
    "safe_timeout":     10,     # секунд на запрос проверки
    "safe_seen_days":   7,      # с какого возраста домен считается знакомым
    "safe_seen_min":    3,      # и сколько раз он должен был нам встретиться
    # Google Safe Browsing — единственный слой, который знает про домен то,
    # чего не знаем мы: что его вчера отметили как фишинг. Ключ бесплатный
    # (console.cloud.google.com, Safe Browsing API), кладётся в env как
    # SAFEBROWSING_API_KEY. Без ключа слой просто выключен, остальные работают.
    "safebrowsing":     True,   # [env ND_SAFEBROWSING]
    "safe_ttl_h":       168,    # сколько живёт его ответ про домен: вчера
                                # чистый домен сегодня бывает взломан
    "keep_hosts_days":  180,    # сколько живёт наша репутация доменов

    # --- не вброс ли это -----------------------------------------------------
    # Проверяется НЕ истинность (модель её не знает), а обеспеченность
    # заявления: кто подтверждает, названа ли работа, существует ли DOI.
    # Сомнительное не выбрасывается, а ждёт подтверждения (newsdigest/factcheck.py).
    "factcheck":        True,   # [env ND_FACTCHECK]
    "fact_sections":    "science,medicine,health,space,climate,cybersec",
                                # где вброс дороже всего и проверка идёт глубже
    "fact_llm":         True,   # [env ND_FACT_LLM] спрашивать модель о форме
                                # заявления. 0 = только бесплатные слои
    "fact_candidates":  3,      # сколько верхних кандидатов раздела проверяем
    "fact_max":         24,     # заявлений за прогон
    "fact_batch":       8,      # заявлений в одном запросе
    "fact_hold_h":      18,     # сколько держим карантин. Дольше — и новость
                                # протухнет раньше, чем дождётся подтверждения
    "fact_min_publishers": 3,   # столько независимых издателей снимают все
                                # вопросы к событию
    "fact_min_strong":  2,      # либо столько сильных: агентство, первоисточник,
                                # редакция, которая проверяет факты
    "doi_check":        True,   # проверять DOI через Crossref (бесплатно, без ключа)
    "doi_timeout":      10,
    "keep_claims_days": 30,     # сколько живут приговоры

    # --- LLM (DeepSeek) ------------------------------------------------------
    "llm_base":         "https://api.deepseek.com",
    "model_rank":       "deepseek-v4-flash",  # ранжирование — дешёвая модель
    "model_summary":    "deepseek-v4-flash",  # саммари. Хотите качественнее: deepseek-v4-pro
    "llm_candidates":   28,        # сколько кластеров отдаём модели на оценку
    "translate_batch":  20,        # строк в одном запросе на перевод
    "llm_timeout":      120,
    "llm_retries":      4,
    "disable_thinking": True,      # V4 умеет "думать" — нам это не нужно, дороже и медленнее
    "price_in":         0.14,      # $/1M токенов — только для оценки расхода в `status`
    "price_out":        0.28,

    # --- Telegram ------------------------------------------------------------
    # Выпуск на полтора десятка разделов сплошной лентой — это простыня в
    # три-четыре сообщения: до «Науки» надо пролистать всё, а шапка со
    # статистикой съедает первый экран. Поэтому по умолчанию выпуск приходит
    # оглавлением: время суток, дата, сколько новостей, главное за день и
    # кнопки разделов. Раздел открывается прямо в этом же сообщении, «←
    # К разделам» возвращает назад.
    "tg_view":          "screens", # [env ND_TG_VIEW] screens — оглавление
                                   # и разделы по кнопкам, feed — старая
                                   # сплошная лента одним текстом
    "one_message":      True,      # весь дайджест одним сообщением (режем только если >4096)
    "link_preview":     False,     # превью ссылок раздувает сообщение
    "silent":           False,     # [env ND_SILENT] true = отправлять без звука
    # Команд в Telegram нет: бот там только рассылает выпуски. Управление —
    # на странице в браузере (см. ниже) или в терминале.
    "listen":           True,      # [env ND_LISTEN] принимать из чата нажатия
                                   # кнопок 👍/👎/🔖 и заявки новых чатов.
                                   # 0 = ничего не слушаем, только отправка
    "chat_reply":       "schedule",# [env ND_CHAT_REPLY] что отвечать на сообщение
                                   # в чате: schedule — расписание и время
                                   # следующего выпуска, off — вообще ничего
                                   # (переписки с ботом не будет совсем;
                                   # кнопки 👍/👎/🔖 при этом работают)
    "signup":           "ask",     # [env ND_SIGNUP] что делать с новым чатом:
                                   # ask — спросить владельца кнопками,
                                   # open — подписывать сразу (осторожно: каждый
                                   #        подписчик тратит ваш баланс модели),
                                   # off — отвечать «бот личный» и не пускать

    # --- страница в браузере -------------------------------------------------
    # То же, что бот в Telegram, но по адресу http://<ip-вашего-vps>:8080.
    # Новости на ней открыты всем, служебное — по паролю владельца.
    # Пароль (web_token) создаётся сам при первом запуске и пишется в env.
    "web":              True,       # [env ND_WEB] поднимать страницу вместе с демоном
    "web_host":         "0.0.0.0",  # [env ND_WEB_HOST] 0.0.0.0 = видно по IP VPS,
                                    # 127.0.0.1 = только с самой машины (через ssh-туннель)
    "web_port":         8080,       # [env ND_WEB_PORT]
    "web_token":        "",         # [env ND_WEB_TOKEN] пароль владельца; пусто = создам сам

    # --- срочные новости (вне расписания) ------------------------------------
    # Событие, о котором за пару часов написали сразу несколько первоисточников,
    # ждать до утра не должно. Условия нарочно строгие: одно ложное «срочно»
    # раздражает сильнее, чем десять пропущенных.
    "breaking":           True,   # [env ND_BREAKING] присылать срочное сразу
    "breaking_window_h":  6,      # за какое окно считаем подтверждения
    # Подтверждения считаются по ИЗДАТЕЛЯМ (newsdigest/trust.py), а не по фидам:
    # шесть лент Guardian — это одна редакция, а не шесть независимых сайтов.
    "breaking_min_sources": 3,    # столько РАЗНЫХ издателей, и хотя бы один tier-1
    "breaking_min_wires": 2,      # либо столько мировых агентств — им хватает двух
    "breaking_min_wide":  4,      # либо столько издателей вообще, без tier-1:
                                  # иначе в спорте и кино, где tier-1 нет ни
                                  # одного, срочное было бы невозможно в принципе
    "breaking_social":    0.9,    # либо ~270+ баллов Hacker News в одиночку
    # Срочность оценивается ОТДЕЛЬНЫМ промптом (llm.BREAKING_SYSTEM) по шкале
    # мировых агентств: это про масштаб события, а не про вкусы читателя.
    # Уровней два, и ведут они себя по-разному:
    "breaking_flash_score": 9.0,  # ⚡ молния: уходит сразу, отдельным сообщением
    "breaking_alert_score": 7.5,  # 🔔 важное: копится и уходит сводкой
    "breaking_max_per_day": 2,    # молний в сутки — больше это уже лента
    "alert_max_per_day":  3,      # и важного
    "breaking_alert_every_h": 4,  # как часто отдавать накопленное важное
    "breaking_every_min": 15,     # как часто опрашивать быструю полосу
                                  # (агентства и службы оповещения) и проверять
                                  # срочное. Полный обход всех источников
                                  # остаётся раз в collect_every_h
    "breaking_quiet":     "23:00-08:00",  # в эти часы молчим (ваше время)
    "flash_override_quiet": True, # ...кроме молнии мирового масштаба: ради
                                  # землетрясения M7 человека будят и ночью.
                                  # Важное за ночь копится и уходит утром

    # --- обратная связь ------------------------------------------------------
    "feedback_buttons": True,      # [env ND_FEEDBACK] кнопки 👍/👎/🔖 под выпуском
    # Ряд кнопок на каждую новость — это до тридцати кнопок под выпуском:
    # столбик «1 👍 1 👎 1 🔖 / 2 👍 …» перетягивает на себя внимание и мешает
    # читать сам дайджест. Поэтому по умолчанию кнопки свёрнуты в одну строку
    # и разворачиваются по нажатию — оценить по-прежнему можно, но выпуск
    # выглядит как выпуск, а не как пульт.
    "feedback_style":  "compact",  # [env ND_FEEDBACK_STYLE] compact — свёрнуто
                                   # в один ряд, rows — ряд под каждой новостью
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
    "trust":         0.30,   # насколько источнику верят (newsdigest/trust.py)
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
    "ND_FAVORITES": ("favorites", str),
    "ND_PER_SECTION": ("per_section", int),
    "ND_LANGUAGE": ("language", str),
    "ND_TRANSLATE": ("translate", lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_SEND_AT": ("send_at", str),
    "ND_PER_DAY": ("per_day", int),
    "ND_TZ": ("tz", str),
    "ND_TZ_OFFSET": ("tz_offset", int),
    "ND_COLLECT_EVERY": ("collect_every_h", int),
    "ND_MIN_ITEMS": ("min_items", int),
    "ND_MAX_ITEMS": ("max_items", int),
    "ND_MIN_SCORE": ("min_score", float),
    "ND_MODEL_SUMMARY": ("model_summary", str),
    "ND_SILENT": ("silent", lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_LISTEN": ("listen", lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_CHAT_REPLY": ("chat_reply", str),
    "ND_TG_VIEW": ("tg_view", str),
    "ND_FEEDBACK": ("feedback_buttons",
                    lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_FEEDBACK_STYLE": ("feedback_style", str),
    "ND_CLASSIFY": ("classify_llm",
                    lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_DUP_LLM": ("dup_llm", lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_SAFE_LINKS": ("safe_links",
                      lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_SAFE_STRICT": ("safe_strict",
                       lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_SAFEBROWSING": ("safebrowsing",
                        lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_FACTCHECK": ("factcheck",
                     lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_FACT_LLM": ("fact_llm",
                    lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_FACT_SECTIONS": ("fact_sections", str),
    "ND_BREAKING": ("breaking", lambda v: str(v).lower() in ("1", "true", "yes")),
    "ND_BREAKING_QUIET": ("breaking_quiet", str),
    "ND_BREAKING_EVERY": ("breaking_every_min", int),
    "ND_KEV": ("use_kev", lambda v: str(v).lower() in ("1", "true", "yes")),
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
#: ключ Google Safe Browsing. Необязателен: без него проверка
#: ссылок работает своими слоями, просто без внешней базы угроз
SB_KEY = ""


# ------------------------------------------------------------------ окружение
def load_env() -> None:
    """Читает ~/.newsdigest/env. Переменные, уже заданные снаружи, главнее."""
    global TG_TOKEN, TG_CHAT, DS_KEY, SB_KEY
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
    SB_KEY = os.environ.get("SAFEBROWSING_API_KEY", "").strip()
    remember_secrets()


def remember_secrets() -> None:
    """Показывает `redact`, какие значения сейчас живые.

    После этого свой токен, ключ модели и пароль страницы не покажутся ни в
    логе, ни в запросе к модели, даже если попадут туда кружным путём — через
    сообщение об ошибке или адрес источника. Заодно забираем всё, что лежит в
    окружении под говорящим именем: там же живут ключи, дописанные руками.
    """
    redact.remember(TG_TOKEN, DS_KEY, SB_KEY, CFG.get("web_token"))
    for name, value in os.environ.items():
        if redact.secret_name(name):
            redact.remember(value)


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
    # Лог читает не только автор: его вставляют в issue на гитхабе, поэтому
    # секреты вырезаются на самом выходе, а не «там, где мы помним»
    for handler in handlers:
        handler.addFilter(redact.SecretFilter())
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
