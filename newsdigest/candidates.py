# -*- coding: utf-8 -*-
"""Источники-кандидаты: чего в подборке не хватает и что стоит попробовать.

Список фидов стареет сам по себе. AnandTech закрылся, у Reuters, AP и AFP не
стало публичного RSS, climate.gov закрыли, ленты переезжают. Поэтому кандидаты
живут отдельно от рабочей подборки: прежде чем попасть в профиль, каждый
должен ответить.

    python3 digest.py feeds --candidates          посмотреть, кто отвечает
    python3 digest.py feeds --candidates --adopt  добавить ответивших в профили

Добавляются только живые: `--adopt` пишет в ~/.newsdigest/profiles.json ровно
то, что вернуло записи. Мёртвая ссылка в подборку не попадает, и вручную
вычищать её потом не придётся.

Каждый адрес здесь проверен из открытого интернета (`tools/feedcheck.py`, он
же идёт в CI при каждой правке списка). Здесь остаются два сорта лент:
живые, но необязательные — узкие, шумные или зеркала чужих сайтов, — и те,
что закрыты от роботов из CI (403), но могут ответить вашему серверу.

Каждый кандидат — (source_id, url, tier, category, зачем он нужен).
"""
from __future__ import annotations

#: зеркала лент для сайтов, у которых своей ленты нет. Их собирают по
#: расписанию открытые проекты на GitHub; ссылки внутри ведут на сам сайт
OLSHANSK = "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/%s"
TURING = ("https://raw.githubusercontent.com/alan-turing-institute/"
          "ai-rss-feeds/main/feeds/%s")

CANDIDATES = {

    "ai": [
        ("mistral", TURING % "mistral-news.xml", 1, "labs",
         "европейская лаборатория, заметный источник открытых весов. Своей "
         "ленты у mistral.ai нет — это зеркало Alan Turing Institute"),
        ("anthropic-research", OLSHANSK % "feed_anthropic_research.xml", 1,
         "research", "исследования Anthropic: интерпретируемость и оценки "
                     "моделей; новости лаборатории уже в подборке"),
        ("meta-ai", OLSHANSK % "feed_meta_ai.xml", 1, "labs",
         "блог AI at Meta: Llama и исследования. Зеркало обновляется "
         "нерегулярно — последняя запись при проверке была двухмесячной"),
        ("hf-papers", "https://jamesg.blog/hf-papers.xml", 2, "research",
         "статьи, отобранные людьми, — замена шумному arXiv. CI не пустили"),
        ("404media", "https://www.404media.co/rss/", 2, "media",
         "независимая редакция: расследования про ИИ, слежку и платформы"),
    ],

    "dev": [
        ("golem-changelog", "https://about.gitlab.com/atom.xml", 2, "labs",
         "релизы и инженерный блог GitLab"),
        ("djangoproject", "https://www.djangoproject.com/rss/weblog/", 1,
         "opensource", "релизы и уязвимости Django"),
        ("mozilla-hacks", "https://hacks.mozilla.org/feed/", 1, "opensource",
         "Firefox и веб-платформа из первых рук"),
        ("acm-queue", "https://queue.acm.org/rss/feeds/queuecontent.xml", 1,
         "research", "инженерные разборы уровня ACM, а не пересказ "
                     "пресс-релизов. CI не пустили"),
    ],

    "cybersec": [
        ("oss-security", "https://seclists.org/rss/oss-sec.rss", 1, "research",
         "раскрытия уязвимостей в открытом ПО из первых рук. Полтора десятка "
         "писем в день — шумно"),
    ],

    "hardware": [
        ("amd-press", "https://www.amd.com/en/newsroom/rss.xml", 1, "labs",
         "первоисточник анонсов AMD. CI не пустили"),
    ],

    "medicine": [
        ("nejm", "https://www.nejm.org/action/showFeed?type=etoc&feed=rss&jc=nejm",
         1, "research", "журнал первого ряда. CI не пустили"),
    ],

    "science": [
        ("pnas", "https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=pnas",
         1, "research", "журнал первого ряда. CI не пустили"),
    ],

    "economy": [
        ("oecd", "https://www.oecd.org/newsroom/index.xml", 1, "policy",
         "макростатистика и доклады ОЭСР. CI не пустили"),
        ("sec-press", "https://www.sec.gov/news/pressreleases.rss", 1, "policy",
         "регулятор рынков США: иски и правила, в том числе по крипте"),
        ("cnbc", "https://www.cnbc.com/id/100003114/device/rss/rss.html", 2,
         "media", "рынки и компании США — быстро, но три десятка в день"),
    ],

    "climate": [
        ("ember", "https://ember-energy.org/feed/", 1, "research",
         "данные об электроэнергетике: выработка, выбросы, солнце и ветер. "
         "CI не пустили"),
    ],

    "cinema": [
        ("bafta", "https://www.bafta.org/media-centre/press-releases/rss", 1,
         "policy", "первоисточник по премии. CI не пустили"),
        ("criterion", "https://www.criterion.com/feeds/current", 2, "media",
         "релизы и реставрации"),
    ],

    "games": [
        ("valve-steam", "https://store.steampowered.com/feeds/news.xml", 1,
         "labs", "обновления Steam из первых рук"),
    ],

    "robots": [
        ("dji", "https://enterprise-insights.dji.com/blog/rss.xml", 1, "labs",
         "крупнейший производитель дронов"),
        ("suasnews", "https://www.suasnews.com/feed/", 2, "media",
         "беспилотники: регулирование и рынок. CI не пустили"),
    ],
}


#: Куда переехала лента, которая перестала отвечать.
#:
#: Отдельно от CANDIDATES, потому что это не новый источник, а тот же самый по
#: новому адресу: имя сохраняется, и вместе с ним класс, доверие и быстрая
#: полоса из `trust.SOURCE_META`. Заменить адрес — не то же самое, что добавить
#: ленту заново.
#:
#: Когда новый адрес известен наверняка, он прописывается прямо в подборку
#: (так в сентябре 2026-го переехали AP, ВОЗ, WMO, EMA, NICE, Cochrane,
#: Eurostat, МВФ, NOAA, InfoWorld и Vulture), а архив при проверке стучится и
#: по нему (`feeds --archive`).
#: Здесь остаётся запасной адрес для ленты, которая из CI отвечает, но у
#: сервера могла сломаться: `feeds --broken` стучится по нему, `--adopt`
#: прописывает ответивший.
#:
#: HTTP 403 сюда обычно не лечится: это не переезд, а защита от роботов —
#: сайт видит запрос из дата-центра и закрывается. Смена адреса тут не поможет,
#: нужен другой источник о том же (см. CANDIDATES).
REPLACEMENTS = {
    "carbonbrief": (
        ("https://www.carbonbrief.org/feed", "без слеша на конце"),
    ),
    "xbox-wire": (
        ("https://news.xbox.com/en-us/feed/atom/", "atom вместо rss"),
    ),
    "dronelife": (
        ("https://dronelife.com/feed/atom/", "atom вместо rss"),
    ),
}


def replacements_for(source_id) -> tuple:
    """Куда мог переехать этот источник. Ничего не известно — пустой список."""
    return REPLACEMENTS.get(str(source_id), ())


def all_candidates(topics=None) -> list:
    """Плоский список: (раздел, source_id, url, tier, category, зачем)."""
    out = []
    for topic, rows in CANDIDATES.items():
        if topics and topic not in topics:
            continue
        for source_id, url, tier, category, why in rows:
            out.append((topic, source_id, url, tier, category, why))
    return out
