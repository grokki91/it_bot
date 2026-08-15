# -*- coding: utf-8 -*-
"""Разделы: набор источников, ключевых слов и портрет читателя.

Раздел (он же «тема») — это единица, по которой бот собирает и выдаёт новости:
ИИ, медицина, политика, спорт, космос и так далее. Утренний выпуск проходит
по всем разделам подписчика, а команда `/news <раздел>` показывает топ одного.

    title/emoji  как раздел выглядит в списках и заголовках выпуска
    aliases      как его можно назвать в команде («мед», «кино», «hardware»)
    persona      портрет читателя: им калибруется оценка модели
    keywords     фильтр для Hacker News (у нетехнических разделов пуст)
    feeds        tier: 1 = первоисточник, 2 = профильное СМИ, 3 = агрегатор

Сломанный фид сам отключится на сутки и будет виден в `digest.py status`.
Добавить свой источник можно, не трогая этот файл: `/feed add <ссылка>`.
"""
from __future__ import annotations

import sys

from .config import CFG

BUILTIN = {

    "ai": {
        "title": "ИИ и технологии",
        "emoji": "🤖",
        "aliases": ("ии", "аи", "нейросети", "it", "ai"),
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

    "hardware": {
        "title": "Компьютерное железо",
        "emoji": "🖥",
        "aliases": ("железо", "хардвар", "hw", "комплектующие", "гаджеты"),
        "persona": (
            "человек, который собирает и обслуживает компьютеры. Интересны: "
            "анонсы и тесты процессоров, видеокарт, памяти и накопителей, "
            "реальная производительность и энергопотребление, цены и доступность, "
            "серверное железо, поддержка в драйверах и ядре. НЕ интересны: "
            "пресс-релизы без цифр, «топ-10 сборок», обзоры чехлов и мышек, "
            "слухи без источника."
        ),
        "keywords": ["cpu", "gpu", "nvidia", "amd", "intel", "arm", "risc-v", "ryzen",
                     "radeon", "geforce", "ssd", "nvme", "ddr5", "chip", "tsmc",
                     "benchmark", "silicon", "motherboard"],
        "feeds": [
            ("tomshardware",  "https://www.tomshardware.com/feeds/all",        2, "media"),
            ("techpowerup",   "https://www.techpowerup.com/rss/news",          2, "media"),
            ("phoronix",      "https://www.phoronix.com/rss.php",              2, "media"),
            ("servethehome",  "https://www.servethehome.com/feed/",            2, "media"),
            ("ars-gadgets",   "https://arstechnica.com/gadgets/feed/",         2, "media"),
            ("nvidia-blog",   "https://blogs.nvidia.com/feed/",                1, "labs"),
            ("ixbt",          "https://www.ixbt.com/export/news.rss",          2, "media"),
            ("3dnews",        "https://3dnews.ru/news/rss/",                   2, "media"),
            ("notebookcheck", "https://www.notebookcheck-ru.com/rss.xml",      2, "media"),
            ("overclockers",  "https://overclockers.ru/rss/all.rss",           3, "media"),
        ],
    },

    "robots": {
        "title": "Роботы",
        "emoji": "🦾",
        "aliases": ("роботы", "робототехника", "robotics", "дроны"),
        "persona": (
            "инженер, которому интересна робототехника. Интересны: новые "
            "платформы и их реальные возможности, автономность и управление, "
            "промышленное и складское применение, дроны и беспилотный транспорт, "
            "открытые стеки вроде ROS, цена и серийность. НЕ интересны: "
            "постановочные ролики без подробностей, обещания «через пять лет», "
            "рассуждения о восстании машин."
        ),
        "keywords": ["robot", "robotics", "humanoid", "drone", "autonomous", "ros",
                     "manipulator", "lidar", "actuator", "warehouse automation"],
        "feeds": [
            ("ieee-robotics",  "https://spectrum.ieee.org/feeds/topic/robotics.rss", 1, "media"),
            ("robotreport",    "https://www.therobotreport.com/feed/",               2, "media"),
            ("robohub",        "https://robohub.org/feed/",                          2, "community"),
            ("techxplore-bot", "https://techxplore.com/rss-feed/robotics-news/",     2, "media"),
            ("dronelife",      "https://dronelife.com/feed/",                        2, "media"),
            ("gh-ros2",        "https://github.com/ros2/ros2/releases.atom",         1, "opensource"),
        ],
    },

    "space": {
        "title": "Космос",
        "emoji": "🚀",
        "aliases": ("космос", "space", "астрономия", "ракеты"),
        "persona": (
            "человек, который следит за космонавтикой и астрономией. Интересны: "
            "запуски и их результаты, ход миссий, открытия телескопов и "
            "аппаратов, новые аппараты и двигатели, контракты и бюджеты агентств. "
            "НЕ интересны: гороскопы, уфология, пересказ старых снимков, "
            "«учёные не исключают» без данных."
        ),
        "keywords": ["nasa", "spacex", "esa", "rocket", "launch", "satellite",
                     "mars", "moon", "lunar", "telescope", "orbit", "starship",
                     "astronaut", "asteroid"],
        "feeds": [
            ("nasa",            "https://www.nasa.gov/feed/",                             1, "labs"),
            ("esa",             "https://www.esa.int/rssfeed/Our_Activities/Space_News",  1, "labs"),
            ("spacenews",       "https://spacenews.com/feed/",                            2, "media"),
            ("nasaspaceflight", "https://www.nasaspaceflight.com/feed/",                  2, "media"),
            ("ars-space",       "https://arstechnica.com/space/feed/",                    2, "media"),
            ("phys-space",      "https://phys.org/rss-feed/space-news/",                  2, "media"),
            ("universetoday",   "https://www.universetoday.com/feed",                     2, "media"),
        ],
    },

    "climate": {
        "title": "Климат и экология",
        "emoji": "🌍",
        "aliases": ("климат", "climate", "экология", "потепление", "природа"),
        "persona": (
            "читатель, которому нужны данные о климате и окружающей среде, а не "
            "лозунги. Интересны: измерения и рекорды (температура, лёд, уровень "
            "моря, выбросы), доклады IPCC и метеослужб, экстремальная погода с "
            "разбором причин, энергопереход и его экономика, климатическое "
            "регулирование и суды, загрязнение и биоразнообразие. НЕ интересны: "
            "алармизм без цифр, колонки активистов и отрицателей, «через N лет "
            "всё погибнет», корпоративный greenwashing, прогноз погоды на выходные."
        ),
        "keywords": ["climate", "emissions", "carbon", "warming", "renewable",
                     "solar power", "wind power", "ipcc", "drought", "wildfire",
                     "sea level", "biodiversity", "deforestation"],
        "feeds": [
            ("carbonbrief",     "https://www.carbonbrief.org/feed/",                    1, "research"),
            ("nature-climate",  "https://www.nature.com/nclimate.rss",                  1, "research"),
            ("noaa-climate",    "https://www.climate.gov/feeds/news-features.rss",      1, "policy"),
            ("guardian-environment", "https://www.theguardian.com/environment/rss",     2, "media"),
            ("insideclimate",   "https://insideclimatenews.org/feed/",                  2, "media"),
            ("yale-e360",       "https://e360.yale.edu/feed.xml",                       2, "media"),
            ("climatehome",     "https://www.climatechangenews.com/feed/",              2, "media"),
            ("grist",           "https://grist.org/feed/",                              2, "media"),
            ("phys-earth",      "https://phys.org/rss-feed/earth-news/",                2, "media"),
            ("sd-climate",      "https://www.sciencedaily.com/rss/earth_climate.xml",   2, "media"),
        ],
    },

    "science": {
        "title": "Наука",
        "emoji": "🔬",
        "aliases": ("наука", "science", "исследования"),
        "persona": (
            "любознательный читатель с техническим образованием. Интересны: "
            "результаты исследований с понятной методикой, крупные эксперименты, "
            "физика, химия, биология, археология, воспроизводимость и опровержения. "
            "НЕ интересны: пересказ пресс-релиза университета без сути работы, "
            "«учёные доказали» на выборке в 12 человек, научпоп ни о чём, "
            "климат и экология — для них есть отдельный раздел."
        ),
        "keywords": ["research", "study", "physics", "quantum", "biology", "genome",
                     "fusion", "materials", "chemistry", "archaeology"],
        "feeds": [
            ("nature",       "https://www.nature.com/nature.rss",                 1, "research"),
            ("science-news", "https://www.science.org/rss/news_current.xml",      1, "research"),
            ("quanta",       "https://api.quantamagazine.org/feed/",              2, "media"),
            ("phys-all",     "https://phys.org/rss-feed/",                        2, "media"),
            ("sd-science",   "https://www.sciencedaily.com/rss/top/science.xml",  2, "media"),
            ("newscientist", "https://www.newscientist.com/feed/home/",           2, "media"),
            ("nplus1",       "https://nplus1.ru/rss",                             2, "media"),
        ],
    },

    "medicine": {
        "title": "Медицина",
        "emoji": "🩺",
        "aliases": ("медицина", "мед", "medicine", "фарма"),
        "persona": (
            "врач или человек, читающий медицинские новости по существу. "
            "Интересны: результаты клинических испытаний с цифрами, одобрения и "
            "отзывы препаратов регуляторами, вспышки заболеваний, новые методы "
            "диагностики и лечения, крупные метаанализы. НЕ интересны: "
            "advertorial производителей, БАДы и «чудо-средства», единичные "
            "случаи, поданные как открытие, страшилки без данных."
        ),
        "keywords": ["clinical trial", "fda", "vaccine", "cancer", "drug",
                     "antibiotic", "outbreak", "gene therapy", "crispr", "who"],
        "feeds": [
            ("statnews",      "https://www.statnews.com/feed/",                             2, "media"),
            ("medicalxpress", "https://medicalxpress.com/rss-feed/",                        2, "media"),
            ("nature-med",    "https://www.nature.com/nm.rss",                              1, "research"),
            ("lancet",        "https://www.thelancet.com/rssfeed/lancet_current.xml",       1, "research"),
            ("who-news",      "https://www.who.int/rss-feeds/news-english.xml",             1, "policy"),
            ("fda-press",     "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", 1, "policy"),
            ("sd-medicine",   "https://www.sciencedaily.com/rss/health_medicine.xml",       2, "media"),
        ],
    },

    "health": {
        "title": "Здоровье",
        "emoji": "🥗",
        "aliases": ("здоровье", "зож", "health", "питание", "фитнес"),
        "persona": (
            "человек, который следит за своим здоровьем и хочет решений, "
            "подкреплённых данными. Интересны: питание, сон, физическая "
            "активность, психическое здоровье, профилактика, разбор популярных "
            "мифов, рекомендации организаций здравоохранения. НЕ интересны: "
            "детокс и очищение, реклама добавок, «одно упражнение, которое "
            "заменит спортзал», выводы из исследований на мышах, поданные как "
            "готовый совет."
        ),
        "keywords": ["nutrition", "sleep", "exercise", "diet", "mental health",
                     "longevity", "obesity", "fitness", "prevention"],
        "feeds": [
            ("harvard-health",  "https://www.health.harvard.edu/blog/feed",                     1, "research"),
            ("nih-news",        "https://www.nih.gov/news-releases/feed.xml",                   1, "policy"),
            ("guardian-health", "https://www.theguardian.com/society/health/rss",               2, "media"),
            ("npr-health",      "https://feeds.npr.org/1128/rss.xml",                           2, "media"),
            ("sd-nutrition",    "https://www.sciencedaily.com/rss/health_medicine/nutrition.xml", 2, "media"),
            ("sd-fitness",      "https://www.sciencedaily.com/rss/health_medicine/fitness.xml", 2, "media"),
        ],
    },

    "politics": {
        "title": "Политика",
        "emoji": "🏛",
        "aliases": ("политика", "politics", "мир", "world"),
        "persona": (
            "читатель, которому нужны факты о происходящем, а не колонка "
            "мнений. Интересны: решения властей и их последствия, выборы, "
            "международные соглашения и санкции, конфликты, законы, которые "
            "что-то меняют на практике. НЕ интересны: пересказ чужого твита, "
            "прогнозы политологов, заголовки в жанре «а что если», материалы "
            "без указания источника."
        ),
        "keywords": [],
        "feeds": [
            ("bbc-russian",    "https://feeds.bbci.co.uk/russian/rss.xml",              2, "media"),
            ("bbc-world",      "https://feeds.bbci.co.uk/news/world/rss.xml",           2, "media"),
            ("guardian-world", "https://www.theguardian.com/world/rss",                 2, "media"),
            ("aljazeera",      "https://www.aljazeera.com/xml/rss/all.xml",             2, "media"),
            ("politico",       "https://rss.politico.com/politics-news.xml",            2, "media"),
            ("dw-russian",     "https://rss.dw.com/rdf/rss-ru-all",                     2, "media"),
            ("un-news",        "https://news.un.org/feed/subscribe/ru/news/all/rss.xml", 1, "policy"),
            ("tass",           "https://tass.ru/rss/v2.xml",                            2, "media"),
            ("rbc",            "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",     2, "media"),
        ],
    },

    "economy": {
        "title": "Экономика",
        "emoji": "💰",
        "aliases": ("экономика", "economy", "финансы", "бизнес", "рынки"),
        "persona": (
            "читатель, который следит за экономикой и рынками. Интересны: "
            "решения центробанков, инфляция и статистика, крупные сделки и "
            "банкротства, отчётности значимых компаний, налоги и регулирование, "
            "цены на сырьё. НЕ интересны: «акция взлетит», гадания аналитиков, "
            "реклама брокеров, ежедневный шум котировок без события."
        ),
        "keywords": ["inflation", "central bank", "recession", "tariff", "gdp",
                     "interest rate", "earnings", "ipo", "bankruptcy"],
        "feeds": [
            ("kommersant-econ",  "https://www.kommersant.ru/RSS/section-economics.xml",   2, "media"),
            ("interfax",         "https://www.interfax.ru/rss.asp",                       2, "media"),
            ("cbr",              "https://www.cbr.ru/rss/eventrss",                       1, "policy"),
            ("economist-fin",    "https://www.economist.com/finance-and-economics/rss.xml", 1, "media"),
            ("guardian-business","https://www.theguardian.com/business/rss",              2, "media"),
            ("marketwatch",      "https://feeds.content.dowjones.io/public/rss/mw_topstories", 2, "media"),
            ("yahoo-finance",    "https://finance.yahoo.com/news/rssindex",               3, "media"),
        ],
    },

    "sports": {
        "title": "Спорт",
        "emoji": "⚽",
        "aliases": ("спорт", "sport", "sports", "футбол"),
        "persona": (
            "болельщик, которому важны результаты и события, а не слухи. "
            "Интересны: итоги матчей и турниров, рекорды, переходы, травмы "
            "ключевых игроков, допинг и дисквалификации, календарь крупных "
            "соревнований. НЕ интересны: «источник сообщил», разбор слухов о "
            "трансферах, колонки о том, кто величайший, ставки и прогнозы."
        ),
        "keywords": [],
        "feeds": [
            ("bbc-sport",      "https://feeds.bbci.co.uk/sport/rss.xml",     2, "media"),
            ("espn",           "https://www.espn.com/espn/rss/news",         2, "media"),
            ("guardian-sport", "https://www.theguardian.com/sport/rss",      2, "media"),
            ("skysports",      "https://www.skysports.com/rss/12040",        2, "media"),
            ("sports-ru",      "https://www.sports.ru/rss/all_news.xml",     2, "media"),
            ("championat",     "https://www.championat.com/rss/news/",       2, "media"),
            ("cbssports",      "https://www.cbssports.com/rss/headlines/",   2, "media"),
        ],
    },

    "incidents": {
        "title": "Происшествия",
        "emoji": "🚨",
        "aliases": ("происшествия", "чп", "катастрофы", "incidents", "аварии"),
        "persona": (
            "читатель, которому нужна проверенная сводка происшествий. "
            "Интересны: землетрясения, наводнения, извержения, крупные пожары, "
            "аварии на транспорте, техногенные катастрофы, эвакуации — с "
            "масштабом, местом и последствиями. НЕ интересны: криминальная "
            "хроника районного масштаба, «шок-видео», непроверенные сообщения "
            "очевидцев, повтор одной и той же новости через сутки."
        ),
        "keywords": [],
        "feeds": [
            ("gdacs",       "https://www.gdacs.org/xml/rss.xml",                                          1, "policy"),
            ("usgs-quakes", "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.atom",     1, "policy"),
            ("reliefweb",   "https://reliefweb.int/updates/rss.xml",                                      1, "policy"),
            ("nhc-storms",  "https://www.nhc.noaa.gov/index-at.xml",                                      1, "policy"),
            ("volcanoes",   "https://volcano.si.edu/news/WeeklyVolcanoRSS.xml",                           1, "policy"),
            ("ria",         "https://ria.ru/export/rss2/archive/index.xml",                               2, "media"),
            ("lenta",       "https://lenta.ru/rss/news",                                                  2, "media"),
        ],
    },

    "cinema": {
        "title": "Кино и сериалы",
        "emoji": "🎬",
        "aliases": ("кино", "сериалы", "фильмы", "cinema", "movies", "тв"),
        "persona": (
            "зритель, который следит за кино и сериалами. Интересны: даты "
            "выхода и трейлеры значимых проектов, кастинг и смена режиссёров, "
            "сборы и продления/закрытия сериалов, крупные премии, сделки "
            "студий и стримингов. НЕ интересны: «10 фильмов, которые вы "
            "пропустили», пересказ слухов из соцсетей, рецензии на всё подряд, "
            "новости про личную жизнь актёров."
        ),
        "keywords": [],
        "feeds": [
            ("variety",         "https://variety.com/feed/",                  2, "media"),
            ("hollywoodreporter","https://www.hollywoodreporter.com/feed/",   2, "media"),
            ("deadline",        "https://deadline.com/feed/",                 2, "media"),
            ("indiewire",       "https://www.indiewire.com/feed",             2, "media"),
            ("vulture",         "https://www.vulture.com/rss/index.xml",      2, "media"),
            ("guardian-film",   "https://www.theguardian.com/film/rss",       2, "media"),
            ("collider",        "https://collider.com/feed/",                 3, "media"),
        ],
    },

    "games": {
        "title": "Игры",
        "emoji": "🎮",
        "aliases": ("игры", "games", "гейминг", "видеоигры", "gaming", "консоли"),
        "persona": (
            "человек, который играет и следит за индустрией. Интересны: даты "
            "выхода, переносы и отмены, крупные патчи и обновления, техническое "
            "состояние релизов (производительность, баги, требования), железо и "
            "прошивки консолей, покупки студий, закрытия и увольнения, движки и "
            "инструменты разработки, заметные инди. НЕ интересны: «топ-10 игр "
            "месяца», слухи из твитов и «инсайдеры сообщают», гайды и "
            "прохождения, косплей и мерч, рецензии без новости внутри."
        ),
        "keywords": ["game", "gaming", "godot", "unreal engine", "nintendo",
                     "playstation", "xbox", "steam deck", "valve", "roguelike",
                     "speedrun"],
        "feeds": [
            # --- индустрия: цифры, сделки, разработка ---
            ("gamesindustry",   "https://www.gamesindustry.biz/feed",           1, "business"),
            ("gamedeveloper",   "https://www.gamedeveloper.com/rss.xml",        1, "business"),
            # --- издатели и платформы (первоисточники) ---
            ("playstation-blog","https://blog.playstation.com/feed/",           1, "labs"),
            ("xbox-wire",       "https://news.xbox.com/en-us/feed/",            1, "labs"),
            ("gh-godot",        "https://github.com/godotengine/godot/releases.atom", 1, "opensource"),
            # --- профильные СМИ ---
            ("eurogamer",       "https://www.eurogamer.net/feed",               2, "media"),
            ("polygon",         "https://www.polygon.com/rss/index.xml",        2, "media"),
            ("pcgamer",         "https://www.pcgamer.com/rss/",                 2, "media"),
            ("rockpapershotgun","https://www.rockpapershotgun.com/feed",        2, "media"),
            ("vgc",             "https://www.videogameschronicle.com/feed/",    2, "media"),
            ("nintendolife",    "https://www.nintendolife.com/feeds/latest",    2, "media"),
            ("dtf",             "https://dtf.ru/rss/all",                       3, "community"),
        ],
    },

    "crypto": {
        "title": "Криптовалюты",
        "emoji": "₿",
        "aliases": ("крипта", "криптовалюты", "crypto", "блокчейн"),
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
        "title": "Кибербезопасность",
        "emoji": "🛡",
        "aliases": ("безопасность", "инфобез", "кибербез", "security", "cybersec"),
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

    # Свой раздел: скопируйте блок, замените фиды/persona и включите его
    # командой /sections add custom.
    "custom": {
        "title": "Свой раздел",
        "emoji": "📌",
        "aliases": ("свой", "custom"),
        "persona": "внимательный читатель, которому важны факты, а не мнения.",
        "keywords": ["news"],
        "feeds": [
            ("example", "https://news.ycombinator.com/rss", 2, "media"),
        ],
    },
}

#: разделы выпуска по умолчанию и порядок, в котором они идут.
#: Крипта, инфобез и «свой» остаются доступными, но в подборку не лезут:
#: их включают вручную — /sections add crypto.
#:
#: Климат идёт ПЕРЕД наукой намеренно: одно событие показывается один раз, в
#: разделе, который стоит раньше, — иначе климатические новости так и остались
#: бы в «Науке», ради чего раздел и выделяли.
DEFAULT_SECTIONS = [
    "ai", "hardware", "robots", "space", "climate", "science", "medicine",
    "health", "politics", "economy", "sports", "incidents", "cinema", "games",
]

#: то, чем пользуется остальной код. Наполняется встроенными разделами, а
#: поверх них — пользовательскими из ~/.newsdigest/profiles.json (userprofiles).
PROFILES = {name: dict(body) for name, body in BUILTIN.items()}


def profile(topic: str = "") -> dict:
    prof = PROFILES.get(topic or CFG["topic"])
    if not prof:
        sys.exit("Неизвестный раздел %r. Доступны: %s"
                 % (topic or CFG["topic"], ", ".join(PROFILES)))
    return prof


def title(topic: str) -> str:
    """Человеческое название раздела: «Медицина» вместо medicine."""
    return str(PROFILES.get(topic, {}).get("title") or topic)


def emoji(topic: str) -> str:
    return str(PROFILES.get(topic, {}).get("emoji") or "📌")


def label(topic: str) -> str:
    """«🩺 Медицина» — то, что видно в списках и заголовках выпуска."""
    return "%s %s" % (emoji(topic), title(topic))
