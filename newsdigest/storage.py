# -*- coding: utf-8 -*-
"""SQLite: схема, миграции и мелкие обёртки над таблицами служебных данных."""
from __future__ import annotations

import json
import sqlite3

from . import config, translate
from .config import DB_FILE, HOME, log, now_iso

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
    state        TEXT NOT NULL DEFAULT 'new',
    -- раздел, определённый по содержанию (classify.route_all), и уверенность
    -- в нём. Пусто = решить не удалось, раздел доберётся по источнику
    section      TEXT NOT NULL DEFAULT '',
    route_conf   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_section ON items(section);

-- Кэш маршрутизации: во что модель разложила материал с такой сигнатурой.
-- Одну и ту же новость, приехавшую вторым проходом, второй раз не оплачиваем.
CREATE TABLE IF NOT EXISTS routes (
    sig     TEXT PRIMARY KEY,
    section TEXT NOT NULL DEFAULT '',
    at      TEXT NOT NULL
);

-- Кэш вердиктов «одно и то же событие?». Слова сводят только пересказ, и в
-- спорной зоне (dup_gray..similarity) вопрос задаётся модели — а её ответ
-- стоит денег. Ключ — пара сигнатур, поэтому вердикт общий для всех
-- подписчиков и переживает пересборку выпуска.
CREATE TABLE IF NOT EXISTS dupes (
    pair TEXT PRIMARY KEY,
    same INTEGER NOT NULL DEFAULT 0,
    at   TEXT NOT NULL
);

-- История отправленного персональна: у каждого подписчика свой дедуп.
-- Здесь же лежит и сама карточка — раздел, заголовок, суть и оценка модели.
-- Раньше всё это жило только внутри текста сообщения, и ленту новостей на
-- странице по такой истории было не построить: ни отфильтровать по разделу,
-- ни найти вчерашнюю новость поиском.
CREATE TABLE IF NOT EXISTS sent (
    chat_id     TEXT NOT NULL DEFAULT '',
    url_hash    TEXT NOT NULL,
    sig         TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    source_id   TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'other',
    section     TEXT NOT NULL DEFAULT '',
    headline    TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    score       REAL NOT NULL DEFAULT 0,
    breaking    INTEGER NOT NULL DEFAULT 0,
    digest_date TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    PRIMARY KEY (chat_id, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_sent_at ON sent(sent_at);
CREATE INDEX IF NOT EXISTS idx_sent_chat ON sent(chat_id, sent_at);
-- Одна новость лежит в истории у каждого, кому уходила. По одному хэшу её
-- ищут и поисковый индекс (см. SEARCH_SCHEMA), и `item_facts`, а ключ у
-- таблицы составной и с хэша не начинается.
CREATE INDEX IF NOT EXISTS idx_sent_hash ON sent(url_hash);

-- Очередь «важного» (🔔): событие прошло порог alert, но будить ради него
-- человека незачем. Копится и уходит одной короткой сводкой раз в
-- breaking_alert_every_h, а накопленное за тихие часы — утром.
-- Запись в `sent` кладётся сразу при постановке в очередь: событие уже
-- обещано читателю, и плановый выпуск повторять его не должен.
CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY,
    chat_id   TEXT NOT NULL DEFAULT '',
    url_hash  TEXT NOT NULL,
    title     TEXT NOT NULL,
    url       TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    section   TEXT NOT NULL DEFAULT '',
    headline  TEXT NOT NULL DEFAULT '',
    what      TEXT NOT NULL DEFAULT '',
    urgency   REAL NOT NULL DEFAULT 0,
    scope     TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    sent_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_alerts_chat ON alerts(chat_id, sent_at);

CREATE TABLE IF NOT EXISTS health (
    source_id  TEXT PRIMARY KEY,
    ok_at      TEXT,
    err        TEXT,
    err_at     TEXT,
    fails      INTEGER NOT NULL DEFAULT 0,
    last_count INTEGER NOT NULL DEFAULT 0,
    -- Сколько обходов подряд источник отвечает 200 и ноль записей. Фид,
    -- который молча перестал что-либо отдавать (сменился адрес, сломался
    -- поисковый синтаксис у витрины Google News), раньше считался здоровым:
    -- HTTP-ошибки нет — значит всё в порядке. Так теряются источники.
    empty      INTEGER NOT NULL DEFAULT 0,
    empty_at   TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id      INTEGER PRIMARY KEY,
    kind    TEXT NOT NULL,
    at      TEXT NOT NULL,
    status  TEXT NOT NULL,
    stats   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);

-- Кандидаты, которые модель отранжировала, но в выпуск они не влезли.
-- Это запас для команды /more: показать их стоит без нового запроса к LLM.
CREATE TABLE IF NOT EXISTS leftover (
    id        INTEGER PRIMARY KEY,
    chat_id   TEXT NOT NULL DEFAULT '',
    url_hash  TEXT NOT NULL,
    title     TEXT NOT NULL,
    url       TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    category  TEXT NOT NULL DEFAULT 'other',
    score     REAL NOT NULL DEFAULT 0,
    at        TEXT NOT NULL,
    shown     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_leftover_chat ON leftover(chat_id, shown, score);

-- Реакции 👍/👎 под карточками. Это единственный сигнал о вкусах читателя,
-- который у нас есть, — он правит прескоринг и подсказывает модели.
CREATE TABLE IF NOT EXISTS feedback (
    chat_id   TEXT NOT NULL,
    url_hash  TEXT NOT NULL,
    verdict   TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    category  TEXT NOT NULL DEFAULT 'other',
    title     TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    PRIMARY KEY (chat_id, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_feedback_at ON feedback(chat_id, at);

-- Подписчики. Пустая строка / 0 / -1 в настройке означает «как в CFG»,
-- поэтому личные настройки не расходятся с общими сами по себе.
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'private',
    role        TEXT NOT NULL DEFAULT 'member',
    topic       TEXT NOT NULL DEFAULT '',
    sections    TEXT NOT NULL DEFAULT '',
    -- до пяти разделов, которые идут в выпуске первыми. Это не второй список
    -- разделов, а порядок внутри своего: остальные приходят следом.
    favorites   TEXT NOT NULL DEFAULT '',
    per_section INTEGER NOT NULL DEFAULT 0,
    send_at     TEXT NOT NULL DEFAULT '',
    tz          TEXT NOT NULL DEFAULT '',
    language    TEXT NOT NULL DEFAULT '',
    max_items   INTEGER NOT NULL DEFAULT 0,
    min_score   REAL NOT NULL DEFAULT 0,
    silent      INTEGER NOT NULL DEFAULT -1,
    paused      INTEGER NOT NULL DEFAULT 0,
    last_digest TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved (
    chat_id   TEXT NOT NULL,
    url_hash  TEXT NOT NULL,
    title     TEXT NOT NULL DEFAULT '',
    url       TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    PRIMARY KEY (chat_id, url_hash)
);

-- Кэш переводов: строка на языке источника -> она же на языке выпуска. Одна и
-- та же новость уходит нескольким подписчикам, всплывает в /more и в закладках,
-- а назавтра приходит из второго источника с тем же заголовком — платить за её
-- перевод больше одного раза незачем.
CREATE TABLE IF NOT EXISTS translations (
    lang     TEXT NOT NULL DEFAULT '',
    src_hash TEXT NOT NULL,
    src      TEXT NOT NULL DEFAULT '',
    text     TEXT NOT NULL,
    at       TEXT NOT NULL,
    PRIMARY KEY (lang, src_hash)
);

-- Копии сообщений бота: их показывает веб-страница. Всё, что уходит в
-- Telegram, попадает и сюда, поэтому в браузере видно ровно то же самое.
-- message_id — номер того же сообщения в Telegram. По нему бот достаёт полную
-- раскладку кнопок, когда читатель разворачивает свёрнутые реакции: в самой
-- кнопке места нет (64 байта на всё), а в копии сообщения раскладка уже есть.
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'bot',
    text       TEXT NOT NULL,
    keyboard   TEXT NOT NULL DEFAULT '',
    message_id INTEGER NOT NULL DEFAULT 0,
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_chat ON outbox(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_outbox_message ON outbox(chat_id, message_id);

-- Выпуск, разложенный по разделам. В Telegram выпуск приходит одним
-- сообщением-оглавлением, а разделы читатель открывает кнопками: чтобы
-- собрать экран раздела через час после отправки, нужны сами карточки —
-- ни кластеров, ни ответа модели к тому времени уже нет. В кнопку выпуск
-- не влезает (64 байта на callback_data), поэтому в ней едет только номер
-- строки отсюда.
CREATE TABLE IF NOT EXISTS issues (
    id      INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL DEFAULT '',
    at      TEXT NOT NULL,
    data    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_issues_chat ON issues(chat_id, id);
"""

#: сколько последних сообщений храним для страницы (на каждый чат)
OUTBOX_KEEP = 400

#: сколько выпусков помним на чат. Telegram и так не даёт править сообщения
#: старше двух суток, а десятка выпусков хватает на неделю вперёд
ISSUES_KEEP = 10


def table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone())


def columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def ensure_column(conn, table: str, column: str, decl: str) -> bool:
    """Добавляет колонку, если её ещё нет. Возвращает True, если добавили.

    Апгрейд с прошлой версии не должен требовать «удалите базу и начните
    заново» — история отправленного это и защита от повторов тоже.
    """
    if column in columns(conn, table):
        return False
    conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
    conn.commit()
    return True


# ------------------------------------------------- поиск по ленте (FTS5)
#: Виртуальная таблица полнотекстового поиска над историей отправленного.
#:
#: Раньше поиск на странице читал из `sent` три тысячи строк и перебирал их
#: в Python (`newsfeed._hit`): SQLite-функция LOWER() знает только латиницу,
#: а «Ормузский» читатель ищет запросом «ормузский». Токенизатор unicode61
#: сворачивает регистр по всему Unicode — значит, и по-русски, — и отбор
#: наконец делает база, а не цикл по всей истории.
#:
#: Морфологию даёт не индекс, а запрос: `newsfeed.needle` режет окончания,
#: и основа ищется префиксом («иран*» находит и «Иран», и «в Иране»).
#: Поэтому в индексе лежат целые слова: одна и та же таблица годится и для
#: точного слова, и для любой его формы.
SEARCH_TABLE = "sent_fts"

#: Поля истории, по которым идёт поиск, — ровно те же, что читает `_hit`.
SEARCH_FIELDS = ("title", "headline", "summary", "source_id", "url")

#: Версия содержимого индекса. Меняется вместе с SEARCH_FIELDS: старый индекс
#: тогда собирается заново, а не остаётся с половиной полей.
SEARCH_VERSION = "1"


def search_text(alias: str) -> str:
    """SQL-выражение «весь текст новости одной строкой» для строки истории.

    К полям самой истории добавляется текст материала (`items.summary`):
    карточка показывает его у записей, где сути нет (до версии 3.5 её не
    сохраняли), — значит, и находиться по нему новость должна.

    `alias` — имя строки в запросе: таблица (`n`) или `new`/`old` в триггере.
    """
    parts = ["COALESCE(%s.%s, '')" % (alias, name) for name in SEARCH_FIELDS]
    parts.append("COALESCE((SELECT summary FROM items "
                 "WHERE url_hash = %s.url_hash), '')" % alias)
    return " || ' ' || ".join(parts)


def _refresh(alias: str) -> str:
    """Тело триггера «пересобрать индекс у всех записей с этим хэшем».

    Одна новость лежит в истории у каждого, кому уходила, а материал в
    `items` один на всех — поэтому правка материала трогает несколько строк
    индекса сразу.
    """
    return ("DELETE FROM {t} WHERE rowid IN "
            "(SELECT rowid FROM sent WHERE url_hash = {a}.url_hash);\n"
            "    INSERT INTO {t}(rowid, text) SELECT n.rowid, {text} "
            "FROM sent n WHERE n.url_hash = {a}.url_hash;").format(
                t=SEARCH_TABLE, a=alias, text=search_text("n"))


#: Индекс и триггеры, которые его наполняют. Триггеры, а не запись из Python:
#: в `sent` пишут три места (`pipeline`, `breaking` и `newsfeed.remember` —
#: последний правит заголовок и суть задним числом, когда лента доводит
#: карточку до русского), историю подрезает `sources.collect`, а материалы
#: приходят и уходят сами по себе. Забыть одно из этих мест — значит тихо
#: разойтись с лентой; база не забывает.
SEARCH_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS {t} USING fts5(text, tokenize = 'unicode61');

CREATE TRIGGER IF NOT EXISTS {t}_sent_ins AFTER INSERT ON sent BEGIN
    INSERT INTO {t}(rowid, text) VALUES (new.rowid, {new});
END;

CREATE TRIGGER IF NOT EXISTS {t}_sent_del AFTER DELETE ON sent BEGIN
    DELETE FROM {t} WHERE rowid = old.rowid;
END;

CREATE TRIGGER IF NOT EXISTS {t}_sent_upd AFTER UPDATE ON sent BEGIN
    DELETE FROM {t} WHERE rowid = old.rowid;
    INSERT INTO {t}(rowid, text) VALUES (new.rowid, {new});
END;

CREATE TRIGGER IF NOT EXISTS {t}_item_ins AFTER INSERT ON items BEGIN
    {by_new}
END;

CREATE TRIGGER IF NOT EXISTS {t}_item_upd AFTER UPDATE OF summary ON items BEGIN
    {by_new}
END;

CREATE TRIGGER IF NOT EXISTS {t}_item_del AFTER DELETE ON items BEGIN
    {by_old}
END;
""".format(t=SEARCH_TABLE, new=search_text("new"),
           by_new=_refresh("new"), by_old=_refresh("old"))


def searchable(conn) -> bool:
    """Годен ли индекс: и таблица на месте, и триггеры, которые её наполняют.

    Одной таблицы мало. Без триггеров индекс отстаёт от истории с первой же
    новости, и искать по нему хуже, чем перебором: свежего в ответе не будет
    вовсе. Нет чего-то из двух — поиск идёт перебором, как раньше.
    """
    kinds = {row["type"] for row in conn.execute(
        "SELECT type FROM sqlite_master WHERE (type='table' AND name=?)"
        " OR (type='trigger' AND tbl_name='sent' AND sql LIKE ?)",
        (SEARCH_TABLE, "%" + SEARCH_TABLE + "%"))}
    return {"table", "trigger"} <= kinds


def drop_search_index(conn) -> None:
    """Снимает триггеры индекса. Без FTS5 они не дают писать даже в `sent`.

    Так бывает, если базу завели на сборке с FTS5, а открыли на сборке без
    него: триггер ссылается на таблицу, которую SQLite больше не понимает, и
    вместе с ней падает вся запись истории. Остаться без индекса можно,
    остаться без истории — нет.

    Сама виртуальная таблица остаётся: удалить её нечем, модуля-то нет.
    Поиск на неё всё равно не пойдёт — запрос к ней не выполнится, а `page`
    на этот случай держит перебор.
    """
    triggers = [row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND sql LIKE ?",
        ("%" + SEARCH_TABLE + "%",))]
    for name in triggers:
        conn.execute("DROP TRIGGER IF EXISTS %s" % name)
    if triggers:
        conn.commit()
        log.info("Триггеры поискового индекса сняты: FTS5 недоступен")


def reindex_search(conn) -> None:
    """Собирает индекс заново по всей истории.

    Нужно ровно дважды: когда индекса ещё не было (база с прошлой версии —
    триггеры-то новые, а история уже накоплена) и когда поменялся состав
    индексируемого текста.
    """
    conn.execute("DELETE FROM %s" % SEARCH_TABLE)
    conn.execute("INSERT INTO {t}(rowid, text) SELECT n.rowid, {text} "
                 "FROM sent n".format(t=SEARCH_TABLE, text=search_text("n")))
    rows = conn.execute(
        "SELECT COUNT(*) c FROM %s" % SEARCH_TABLE).fetchone()["c"]
    conn.commit()
    meta_set(conn, "search_index", SEARCH_VERSION)
    if rows:
        log.info("Поисковый индекс ленты собран заново: %d запись(ей)", rows)


def add_search_index(conn) -> bool:
    """Заводит индекс поиска. False — FTS5 в сборке нет, и это не беда.

    Вызывается ПОСЛЕ создания схемы: индекс и триггеры стоят над `sent` и
    `items`, и без них создаваться им не над чем.

    FTS5 собран в SQLite почти везде, но не везде: если его нет, CREATE
    VIRTUAL TABLE упадёт, индекса не будет и поиск останется прежним —
    перебором по `newsfeed._hit`. Это медленно, но работает, и лучше так,
    чем не запуститься вовсе.
    """
    fresh = not searchable(conn)
    try:
        conn.executescript(SEARCH_SCHEMA)
        # индекс мог остаться от сборки, где FTS5 был: тогда CREATE ... IF NOT
        # EXISTS промолчит, а первое же обращение к таблице скажет правду
        conn.execute("SELECT rowid FROM %s LIMIT 1" % SEARCH_TABLE).fetchone()
    except sqlite3.OperationalError as exc:
        log.info("FTS5 недоступен (%s) — поиск по ленте идёт перебором", exc)
        drop_search_index(conn)
        return False
    if fresh or meta_get(conn, "search_index") != SEARCH_VERSION:
        reindex_search(conn)
    return True


def upgrade(conn) -> None:
    """Подтягивает старую базу до текущей схемы.

    Вызывается ДО создания схемы: иначе индексы по новым колонкам не лягут
    на таблицу, оставшуюся от прошлой версии.
    """
    split_sent_by_chat(conn)
    add_sections(conn)
    add_outbox_message_id(conn)
    add_digest_slot(conn)
    add_news_card(conn)
    add_breaking_mark(conn)
    add_item_section(conn)
    add_favorites(conn)
    add_empty_feed_counter(conn)


def add_empty_feed_counter(conn) -> None:
    """Счётчик пустых ответов фида (3.7, здоровье источников).

    Раньше «ok» значило только «HTTP 200». Фид, который отвечает двухсоткой и
    отдаёт ноль записей, считался здоровым и молча выпадал из выпуска.
    """
    if not table_exists(conn, "health"):
        return                      # новая база: колонки придут из SCHEMA
    ensure_column(conn, "health", "empty", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "health", "empty_at", "TEXT")


def add_item_section(conn) -> None:
    """Раздел прямо у материала (3.7, маршрутизация по содержанию).

    Раньше раздел вычислялся на лету по source_id, и у широкой ленты вроде
    Reuters он всегда получался один и тот же. Теперь раздел определяется по
    содержанию и хранится рядом с материалом.

    Уже накопленные материалы остаются без раздела — и это нормально:
    `pipeline.for_topic` разложит их по источнику, как делал всегда, а через
    `keep_items_days` они и так уйдут.
    """
    if not table_exists(conn, "items"):
        return                      # новая база: колонки придут из SCHEMA
    ensure_column(conn, "items", "section", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "items", "route_conf", "REAL NOT NULL DEFAULT 0")


def add_news_card(conn) -> None:
    """Карточка новости прямо в истории (3.5, лента новостей на странице).

    Идёт ПОСЛЕ split_sent_by_chat: та пересобирает `sent` по старой схеме,
    и колонки надо досыпать в уже пересобранную таблицу.

    Старые записи остаются без раздела и оценки — это нормально: раздел лента
    достанет по источнику (`sections.by_source`), а звёздочку у таких новостей
    просто не покажет. Переписывать историю задним числом нечем.
    """
    if not table_exists(conn, "sent"):
        return                      # новая база: колонки придут из SCHEMA
    ensure_column(conn, "sent", "section", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sent", "headline", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sent", "summary", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sent", "score", "REAL NOT NULL DEFAULT 0")


def add_breaking_mark(conn) -> None:
    """Пометка «срочное» прямо в истории (3.6, срочное видно на странице).

    Раньше срочная новость отличалась от плановой только тем, что пришла
    одна и не в свой час: в самом сообщении стояло «⚡ Срочно», а в истории
    от этого не оставалось ничего. Странице этого мало — по ленте не
    отличить срочное от обычного, — поэтому метка переезжает в базу.

    Старые записи остаются с нулём: какая из них приходила вне расписания,
    достоверно уже не сказать, а угадывать по времени отправки значит
    развесить молнии не там.
    """
    if not table_exists(conn, "sent"):
        return                      # новая база: колонка придёт из SCHEMA
    ensure_column(conn, "sent", "breaking", "INTEGER NOT NULL DEFAULT 0")


def add_digest_slot(conn) -> None:
    """Метка выпуска обзавелась номером (3.4, несколько выпусков в сутки).

    Раньше в last_digest лежала дата: один выпуск в день — одна запись.
    Теперь метка выглядит как «2026-08-15#2», поэтому старую дату закрываем
    последним выпуском суток: иначе сразу после обновления пришёл бы лишний.
    """
    if not table_exists(conn, "subscribers"):
        return                      # новая база: колонки придут из SCHEMA
    if "last_digest" not in columns(conn, "subscribers"):
        return
    from .subscribers import per_day

    cur = conn.execute(
        "UPDATE subscribers SET last_digest = last_digest || ? "
        "WHERE last_digest != '' AND instr(last_digest, '#') = 0",
        ("#%d" % per_day(),))
    conn.commit()
    if cur.rowcount:
        log.info("Метка выпуска обновлена у %d подписчика(ов)", cur.rowcount)


def add_outbox_message_id(conn) -> None:
    """Связь копии сообщения с номером в Telegram (3.3, свёрнутые реакции)."""
    if table_exists(conn, "outbox"):
        ensure_column(conn, "outbox", "message_id", "INTEGER NOT NULL DEFAULT 0")


def add_sections(conn) -> None:
    """Разделы подписчика (3.2). У кого их нет — читает подборку по умолчанию.

    Кто раньше выбрал себе личную тему, тот выбирал именно её и не должен
    внезапно получить выпуск на полтора десятка разделов: при переезде личная тема
    становится его личным списком разделов. Дальше он волен добавить ещё —
    /sections add. Общая тема (CFG['topic']) сюда не переносится: у владельца
    она стояла у всех по умолчанию и означала просто «о чём бот».
    """
    if not table_exists(conn, "subscribers"):
        return                      # новая база: колонки придут из SCHEMA
    ensure_column(conn, "subscribers", "per_section", "INTEGER NOT NULL DEFAULT 0")
    if not ensure_column(conn, "subscribers", "sections", "TEXT NOT NULL DEFAULT ''"):
        return
    cur = conn.execute("UPDATE subscribers SET sections=topic "
                       "WHERE sections='' AND topic!=''")
    conn.commit()
    if cur.rowcount:
        log.info("Личная тема %d подписчика(ов) стала их списком разделов",
                 cur.rowcount)


def add_favorites(conn) -> None:
    """Личный топ разделов (3.6): что подписчик хочет видеть первым.

    Пустая колонка значит «как у всех» — порядок остаётся прежним, поэтому
    после обновления ни у кого выпуск не переставится сам по себе.
    """
    if not table_exists(conn, "subscribers"):
        return                      # новая база: колонка придёт из SCHEMA
    ensure_column(conn, "subscribers", "favorites", "TEXT NOT NULL DEFAULT ''")


def split_sent_by_chat(conn) -> None:
    """История отправленного становится персональной.

    До 3.0 таблица `sent` была общей: один читатель — одна история. С
    подписчиками так нельзя, у каждого свой дедуп. Ключ меняется на
    (chat_id, url_hash), а старые записи достаются владельцу — он их и
    получал. Терять историю нельзя: это защита от повторов.
    """
    if not table_exists(conn, "sent") or "chat_id" in columns(conn, "sent"):
        return
    have = columns(conn, "sent")
    owner = str(getattr(config, "TG_CHAT", "") or "")
    conn.executescript("""
        CREATE TABLE sent_new (
            chat_id     TEXT NOT NULL DEFAULT '',
            url_hash    TEXT NOT NULL,
            sig         TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL,
            url         TEXT NOT NULL DEFAULT '',
            source_id   TEXT NOT NULL DEFAULT '',
            category    TEXT NOT NULL DEFAULT 'other',
            digest_date TEXT NOT NULL,
            sent_at     TEXT NOT NULL,
            PRIMARY KEY (chat_id, url_hash)
        );
    """)
    # source_id и category появились в 3.0: в базе 2.0 их нет, подставляем пусто
    picked = ["?"] + [name if name in have else default for name, default in (
        ("url_hash", "''"), ("sig", "''"), ("title", "''"), ("url", "''"),
        ("source_id", "''"), ("category", "'other'"),
        ("digest_date", "''"), ("sent_at", "''"))]
    conn.execute(
        "INSERT OR IGNORE INTO sent_new(chat_id,url_hash,sig,title,url,source_id,"
        "category,digest_date,sent_at) SELECT %s FROM sent" % ",".join(picked),
        (owner,))
    moved = conn.execute("SELECT COUNT(*) c FROM sent_new").fetchone()["c"]
    conn.executescript("DROP TABLE sent; ALTER TABLE sent_new RENAME TO sent;")
    conn.commit()
    if moved:
        log.info("История отправленного (%d записей) закреплена за chat_id %s",
                 moved, owner or "—")


def item_facts(conn, url_hash):
    """Заголовок, ссылка, источник и категория по хэшу — где бы они ни лежали.

    Заголовок отдаём на языке выпуска: в базе лежит строка из фида, а читатель,
    нажимая 🔖 или 👍, видел русскую карточку — она и должна попасть в закладки
    и в историю реакций.
    """
    for table in ("items", "sent"):
        row = conn.execute(
            "SELECT title, url, source_id, category FROM %s WHERE url_hash=?" % table,
            (url_hash,)).fetchone()
        if row:
            facts = dict(row)
            facts["title"] = translate.known(conn, facts["title"])
            return facts
    return {"title": "", "url": "", "source_id": "", "category": "other"}


def save_leftover(conn, chat_id, rows) -> None:
    """Запоминает хвост ранжирования: то, что не влезло в сегодняшний выпуск."""
    conn.execute("DELETE FROM leftover WHERE chat_id=?", (str(chat_id),))
    conn.executemany(
        "INSERT INTO leftover(chat_id,url_hash,title,url,source_id,category,score,at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(str(chat_id), r["url_hash"], r["title"], r["url"], r["source_id"],
          r["category"], r["score"], now_iso()) for r in rows])
    conn.commit()


def take_leftover(conn, chat_id, limit):
    """Отдаёт следующие непоказанные новости из хвоста и помечает их показанными."""
    rows = list(conn.execute(
        "SELECT * FROM leftover WHERE chat_id=? AND shown=0 "
        "ORDER BY score DESC LIMIT ?", (str(chat_id), limit)))
    if rows:
        conn.executemany("UPDATE leftover SET shown=1 WHERE id=?",
                         [(r["id"],) for r in rows])
        conn.commit()
    return rows


def save_outbox(conn, chat_id, text, keyboard=None, kind="bot") -> int:
    """Кладёт копию сообщения для веб-страницы. Возвращает её номер.

    Хвост подрезаем изредка, а не каждый раз: лишний DELETE на каждое
    сообщение ничего не даёт, а страница живёт последними сотнями строк.
    """
    cur = conn.execute(
        "INSERT INTO outbox(chat_id, kind, text, keyboard, at) VALUES (?,?,?,?,?)",
        (str(chat_id), kind, text,
         json.dumps(keyboard, ensure_ascii=False) if keyboard else "", now_iso()))
    conn.commit()
    new_id = int(cur.lastrowid)
    if new_id % 25 == 0:
        conn.execute("DELETE FROM outbox WHERE chat_id=? AND id<=?",
                     (str(chat_id), new_id - OUTBOX_KEEP))
        conn.commit()
    return new_id


def link_outbox(conn, row_id, message_id) -> None:
    """Запоминает, каким номером сообщение ушло в Telegram."""
    conn.execute("UPDATE outbox SET message_id=? WHERE id=?",
                 (int(message_id), int(row_id)))
    conn.commit()


def outbox_keyboard(conn, chat_id, message_id) -> list:
    """Полная раскладка кнопок отправленного сообщения (пусто — не нашли)."""
    if not message_id:
        return []
    row = conn.execute(
        "SELECT keyboard FROM outbox WHERE chat_id=? AND message_id=? "
        "ORDER BY id DESC LIMIT 1", (str(chat_id), int(message_id))).fetchone()
    try:
        keyboard = json.loads(row["keyboard"]) if row and row["keyboard"] else []
    except ValueError:
        return []
    return keyboard if isinstance(keyboard, list) else []


def save_issue(conn, chat_id, issue) -> int:
    """Кладёт выпуск для листания по разделам. Возвращает его номер.

    Заодно подрезает хвост: старые выпуски листать всё равно нельзя —
    Telegram не даёт править сообщения старше 48 часов.
    """
    cur = conn.execute(
        "INSERT INTO issues(chat_id, at, data) VALUES (?,?,?)",
        (str(chat_id), now_iso(), json.dumps(issue, ensure_ascii=False)))
    new_id = int(cur.lastrowid)
    conn.execute("DELETE FROM issues WHERE chat_id=? AND id NOT IN "
                 "(SELECT id FROM issues WHERE chat_id=? ORDER BY id DESC "
                 "LIMIT ?)", (str(chat_id), str(chat_id), ISSUES_KEEP))
    conn.commit()
    return new_id


def load_issue(conn, chat_id, ident) -> dict:
    """Выпуск по номеру. Пусто — выпуска нет или он не этого чата."""
    if not ident:
        return {}
    row = conn.execute("SELECT data FROM issues WHERE id=? AND chat_id=?",
                       (int(ident), str(chat_id))).fetchone()
    try:
        issue = json.loads(row["data"]) if row and row["data"] else {}
    except ValueError:
        return {}
    return issue if isinstance(issue, dict) else {}


def outbox_page(conn, chat_id, after=None, limit=60):
    """Сообщения чата: хвост ленты (after=None) или всё новее номера after."""
    if after is None:
        rows = list(conn.execute(
            "SELECT * FROM outbox WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit)))
        return rows[::-1]
    return list(conn.execute(
        "SELECT * FROM outbox WHERE chat_id=? AND id>? ORDER BY id LIMIT ?",
        (str(chat_id), int(after), limit)))


def db() -> sqlite3.Connection:
    HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    # INSERT OR REPLACE в `sent` без этого не снимает с индекса старую строку:
    # без рекурсии REPLACE молча пропускает триггеры удаления
    conn.execute("PRAGMA recursive_triggers=ON")
    upgrade(conn)                 # сначала чиним старое, потом досоздаём новое
    conn.executescript(SCHEMA)
    add_search_index(conn)        # индекс стоит над схемой — значит, после неё
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
