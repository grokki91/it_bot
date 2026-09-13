# -*- coding: utf-8 -*-
"""Источники-кандидаты: чего в подборке не хватает и что стоит попробовать.

Список фидов стареет сам по себе. AnandTech закрылся, у Reuters и AFP не стало
публичного RSS, GitHub начал отдавать 403 на releases.atom без токена, ленты
переезжают. Поэтому кандидаты живут отдельно от рабочей подборки: прежде чем
попасть в профиль, каждый должен ответить.

    python3 digest.py feeds --candidates          посмотреть, кто отвечает
    python3 digest.py feeds --candidates --adopt  добавить ответивших в профили

Добавляются только живые: `--adopt` пишет в ~/.newsdigest/profiles.json ровно
то, что вернуло записи. Мёртвая ссылка в подборку не попадает, и вручную
вычищать её потом не придётся.

Каждый кандидат — (source_id, url, tier, category, зачем он нужен).
"""
from __future__ import annotations

CANDIDATES = {

    "ai": [
        ("anthropic", "https://www.anthropic.com/rss.xml", 1, "labs",
         "лаборатория первого ряда, в подборке её нет вовсе"),
        ("apple-ml", "https://machinelearning.apple.com/rss.xml", 1, "labs",
         "исследования Apple: on-device и приватность, чего нет у остальных"),
        ("mistral", "https://mistral.ai/news/feed.xml", 1, "labs",
         "европейская лаборатория, заметный источник открытых весов"),
        ("ai2", "https://allenai.org/blog/rss.xml", 1, "research",
         "некоммерческий институт: открытые модели и датасеты"),
        ("hf-papers", "https://jamesg.blog/hf-papers.xml", 2, "research",
         "статьи, отобранные людьми, — замена шумному arXiv"),
        ("googleblog-ai", "https://blog.google/technology/ai/rss/", 1, "labs",
         "продуктовые анонсы Google по ИИ"),
    ],

    "dev": [
        ("lobsters", "https://lobste.rs/rss", 3, "community",
         "техническое сообщество плотнее Hacker News"),
        ("golem-changelog", "https://about.gitlab.com/atom.xml", 2, "labs",
         "релизы и инженерный блог GitLab"),
        ("sqlite-news", "https://sqlite.org/news.rss", 1, "opensource",
         "первоисточник релизов SQLite"),
        ("nodejs-blog", "https://nodejs.org/en/feed/blog.xml", 1, "opensource",
         "релизы и security-релизы Node.js"),
        ("djangoproject", "https://www.djangoproject.com/rss/weblog/", 1,
         "opensource", "релизы и уязвимости Django"),
        ("acm-queue", "https://queue.acm.org/rss/feeds/queuecontent.xml", 1,
         "research", "инженерные разборы уровня ACM, а не пересказ пресс-релизов"),
    ],

    "cybersec": [
        ("msrc", "https://msrc.microsoft.com/blog/feed/", 1, "policy",
         "первоисточник по уязвимостям Microsoft"),
        ("talos", "https://blog.talosintelligence.com/rss/", 1, "research",
         "разведка угроз Cisco: разборы кампаний с деталями"),
        ("nvd-recent", "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml", 1,
         "policy", "лента NVD: CVE с оценкой CVSS"),
        ("sans-isc", "https://isc.sans.edu/rssfeed.xml", 1, "community",
         "дежурная сводка SANS: что атакуют прямо сейчас"),
        ("googleblog-sec", "https://security.googleblog.com/feeds/posts/default", 1,
         "research", "исследования безопасности Google"),
    ],

    "hardware": [
        ("chipsandcheese", "https://chipsandcheese.com/feed/", 2, "media",
         "микроархитектурные разборы с замерами — жанр, которого не осталось "
         "после закрытия AnandTech"),
        ("semianalysis", "https://semianalysis.com/feed/", 2, "media",
         "экономика полупроводников и фабрик"),
        ("intel-newsroom", "https://newsroom.intel.com/feed", 1, "labs",
         "первоисточник анонсов Intel"),
        ("amd-press", "https://www.amd.com/en/newsroom/rss.xml", 1, "labs",
         "первоисточник анонсов AMD"),
    ],

    "medicine": [
        ("nejm", "https://www.nejm.org/action/showFeed?type=etoc&feed=rss&jc=nejm",
         1, "research", "журнал первого ряда; сейчас из таких есть только Lancet"),
        ("bmj", "https://www.bmj.com/rss/recent.xml", 1, "research",
         "доказательная медицина и разборы методик"),
        ("jama", "https://jamanetwork.com/rss/site_3/67.xml", 1, "research",
         "журнал первого ряда"),
        ("cdc-newsroom", "https://tools.cdc.gov/api/v2/resources/media/403372.rss",
         1, "policy", "вспышки заболеваний из первых рук"),
        ("ecdc", "https://www.ecdc.europa.eu/en/taxonomy/term/1000/feed", 1,
         "policy", "то же по Европе"),
    ],

    "science": [
        ("retractionwatch", "https://retractionwatch.com/feed/", 1, "research",
         "отзывы статей и подлоги — прямой сигнал НЕдостоверности, "
         "которого в подборке нет совсем"),
        ("pnas", "https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=pnas",
         1, "research", "журнал первого ряда"),
        ("cern", "https://home.cern/api/news/news/feed.rss", 1, "labs",
         "первоисточник по физике частиц"),
    ],

    "economy": [
        ("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml", 1,
         "policy", "решения ФРС из первых рук: сейчас из центробанков есть "
                   "только ЦБ РФ, ЕЦБ и НБП"),
        ("bls", "https://www.bls.gov/feed/bls_latest.rss", 1, "policy",
         "статистика занятости и инфляции США — первоисточник цифр"),
        ("boe", "https://www.bankofengland.co.uk/boeapps/rss/feeds.aspx?feed=News",
         1, "policy", "Банк Англии"),
        ("oecd", "https://www.oecd.org/newsroom/index.xml", 1, "policy",
         "макростатистика и доклады ОЭСР"),
        ("worldbank", "https://www.worldbank.org/en/news/all.rss", 1, "policy",
         "развивающиеся рынки"),
    ],

    "space": [
        ("jonathan-space", "https://planet4589.org/space/jsr/jsr.xml", 1, "research",
         "реестр запусков Джонатана Макдауэлла: сверять анонсы с фактами"),
        ("nasa-blogs", "https://blogs.nasa.gov/feed/", 1, "labs",
         "оперативные сообщения по ходу миссий"),
    ],

    "climate": [
        ("berkeley-earth", "https://berkeleyearth.org/feed/", 1, "research",
         "независимые температурные ряды"),
        ("nsidc", "https://nsidc.org/rss/news.xml", 1, "research",
         "морской лёд из первых рук"),
    ],

    "sports": [
        ("olympics", "https://olympics.com/en/news/rss", 1, "policy",
         "в разделе нет НИ ОДНОГО первоисточника, и из-за этого срочное "
         "в спорте долго было невозможно в принципе"),
        ("uefa", "https://www.uefa.com/rssfeed/news/rss.xml", 1, "policy",
         "то же: официальные решения вместо пересказа"),
        ("wada", "https://www.wada-ama.org/en/rss.xml", 1, "policy",
         "допинг и дисквалификации — первоисточник"),
    ],

    "cinema": [
        ("bafta", "https://www.bafta.org/media-centre/press-releases/rss", 1,
         "policy", "в разделе нет первоисточников — только пресса"),
        ("criterion", "https://www.criterion.com/feeds/current", 2, "media",
         "релизы и реставрации"),
    ],

    "games": [
        ("nintendo-pr", "https://www.nintendo.com/whatsnew/feed/", 1, "labs",
         "первоисточник анонсов Nintendo"),
        ("valve-steam", "https://store.steampowered.com/feeds/news.xml", 1, "labs",
         "обновления Steam из первых рук"),
    ],

    "robots": [
        ("nvidia-robotics", "https://blogs.nvidia.com/blog/category/robotics/feed/",
         1, "labs", "платформы для робототехники"),
        ("dji", "https://enterprise-insights.dji.com/blog/rss.xml", 1, "labs",
         "крупнейший производитель дронов"),
    ],
}


#: Куда переехала лента, которая перестала отвечать.
#:
#: Отдельно от CANDIDATES, потому что это не новый источник, а тот же самый по
#: новому адресу: имя сохраняется, и вместе с ним класс, доверие и быстрая
#: полоса из `trust.SOURCE_META`. Заменить адрес — не то же самое, что добавить
#: ленту заново.
#:
#: Адреса здесь — ПРЕДПОЛОЖЕНИЯ, а не проверенные ссылки: у фида нет способа
#: сообщить, куда он переехал, и угадывать приходится по тому, как устроены
#: адреса на сайте издания. Поэтому ни одна из них не попадает в работу сама.
#: `feeds --broken` стучится по всем подряд и показывает, что ответило;
#: `--adopt` прописывает в профиль первый ответивший. Неверная догадка не
#: стоит ничего: она просто не отвечает.
#:
#: HTTP 403 сюда обычно не лечится: это не переезд, а защита от роботов —
#: сайт видит запрос из дата-центра и закрывается. Смена адреса тут не поможет,
#: нужен другой источник о том же (см. CANDIDATES).
REPLACEMENTS = {
    # --- ИИ ---
    "the-batch": (
        ("https://www.deeplearning.ai/the-batch/rss.xml", "rss.xml вместо feed/"),
        ("https://info.deeplearning.ai/rss.xml", "рассылка на поддомене"),
    ),
    "venturebeat": (
        ("https://venturebeat.com/feed/", "общая лента вместо раздела"),
        ("https://venturebeat.com/category/ai/feed/atom/", "atom того же раздела"),
    ),
    "bair-berkeley": (
        ("https://bair.berkeley.edu/blog/atom.xml", "atom вместо rss"),
        ("https://bair.berkeley.edu/feed.xml", "лента в корне блога"),
    ),

    # --- софт ---
    "infoworld": (
        ("https://www.infoworld.com/feed/", "лента без index.rss"),
        ("https://www.infoworld.com/category/software-development/feed/",
         "лента профильного раздела"),
    ),

    # --- наука и медицина ---
    "nature-news": (
        ("https://www.nature.com/nature.rss", "настоящий фид журнала"),
        ("https://www.nature.com/subjects/news/nature.rss", "лента новостей"),
    ),
    "nih-news": (
        ("https://www.nih.gov/news-events/news-releases/feed", "лента пресс-релизов"),
        ("https://science.nih.gov/rss", "научная лента NIH"),
    ),
    "harvard-health": (
        ("https://www.health.harvard.edu/blog/feed/", "со слешем на конце"),
        ("https://www.health.harvard.edu/rss", "общая лента издания"),
    ),
    "ema": (
        ("https://www.ema.europa.eu/en/rss/news", "лента новостей агентства"),
        ("https://www.ema.europa.eu/en/news/rss.xml", "rss в разделе новостей"),
    ),
    "nice": (
        ("https://www.nice.org.uk/guidance/rss", "лента рекомендаций"),
        ("https://www.nice.org.uk/news/rss", "лента новостей"),
    ),
    "cochrane": (
        ("https://www.cochranelibrary.com/rss/reviews", "лента обзоров"),
    ),

    # --- климат ---
    "wmo": (
        ("https://wmo.int/rss.xml", "лента в корне сайта"),
        ("https://public.wmo.int/en/rss.xml", "прежний публичный адрес"),
    ),
    "noaa-climate": (
        ("https://www.climate.gov/news-features/feed", "лента раздела"),
        ("https://www.climate.gov/rss.xml", "лента в корне"),
    ),
    "noaa-news": (
        ("https://www.noaa.gov/rss.xml", "лента в корне"),
        ("https://www.noaa.gov/media-release/feed", "лента пресс-релизов"),
    ),
    "carbonbrief": (
        ("https://www.carbonbrief.org/feed", "без слеша на конце"),
    ),

    # --- экономика ---
    "eurostat": (
        ("https://ec.europa.eu/eurostat/api/dissemination/rss/en/euro_indicators.rss",
         "подчёркивание вместо дефиса"),
        ("https://ec.europa.eu/eurostat/api/dissemination/rss/en/news-release.rss",
         "лента пресс-релизов"),
    ),
    "imf": (
        ("https://www.imf.org/external/rss/feeds.aspx?category=News", "прежняя лента"),
        ("https://www.imf.org/en/News/RSS?Language=ENG", "тот же адрес, другой регистр"),
    ),
    "nbp": (
        ("https://nbp.pl/feed/", "лента без языкового префикса"),
        ("https://nbp.pl/en/rss/", "rss вместо feed"),
    ),

    # --- политика ---
    "ap-topnews": (
        ("https://apnews.com/hub/ap-top-news.rss", "лента раздела на сайте"),
        ("https://apnews.com/index.rss", "общая лента"),
        ("https://feeds.apnews.com/apnews/topnews", "прежний адрес без rss/"),
    ),

    # --- железо, кино, игры, роботы ---
    "notebookcheck": (
        ("https://www.notebookcheck-ru.com/index.php?type=100&tx_ttnews[type]=rss",
         "лента движка сайта"),
        ("https://www.notebookcheck.net/News.0.html?type=100", "английская лента"),
    ),
    "vulture": (
        ("https://www.vulture.com/rss/all.xml", "общая лента издания"),
        ("https://feeds.feedburner.com/nymag/vulture", "лента через feedburner"),
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
