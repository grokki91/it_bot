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


def all_candidates(topics=None) -> list:
    """Плоский список: (раздел, source_id, url, tier, category, зачем)."""
    out = []
    for topic, rows in CANDIDATES.items():
        if topics and topic not in topics:
            continue
        for source_id, url, tier, category, why in rows:
            out.append((topic, source_id, url, tier, category, why))
    return out
