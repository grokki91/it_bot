# -*- coding: utf-8 -*-
"""Разделы: набор источников, ключевых слов и портрет читателя.

Раздел (он же «тема») — это единица, по которой бот собирает и выдаёт новости:
ИИ, медицина, политика, спорт, космос и так далее. Утренний выпуск проходит
по всем разделам подписчика, а команда `/news <раздел>` показывает топ одного.

    title/emoji  как раздел выглядит в списках и заголовках выпуска
    short        имя покороче — для кнопки в пол-экрана, если title в неё
                 не влезает («Железо» вместо «Компьютерное железо»)
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
            ("googleblog-ai",     "https://blog.google/technology/ai/rss/",                   1, "labs"),
            # У anthropic.com своей ленты нет: это зеркало страницы новостей,
            # которое раз в час собирает открытый проект Olshansk/rss-feeds.
            # Ссылки в нём ведут на anthropic.com (издатель — в trust.py).
            ("anthropic",         "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml", 1, "labs"),
            # ai.meta.com/blog/rss отдаёт 404 — у Meta публичного RSS нет.
            ("meta-engineering",  "https://engineering.fb.com/feed/",                         1, "labs"),
            ("nvidia-dev",        "https://developer.nvidia.com/blog/feed/",                  1, "labs"),
            ("huggingface",       "https://huggingface.co/blog/feed.xml",                     1, "labs"),
            ("microsoft-research","https://www.microsoft.com/en-us/research/feed/",           1, "labs"),
            ("apple-ml",          "https://machinelearning.apple.com/rss.xml",                1, "labs"),
            ("bair-berkeley",     "https://bair.berkeley.edu/blog/feed.xml",                  1, "research"),
            # --- технологические СМИ ---
            # VentureBeat убран: ни разу не ответил серверу, из CI — 429
            ("techcrunch",        "https://techcrunch.com/category/artificial-intelligence/feed/", 2, "media"),
            ("theverge",          "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 2, "media"),
            ("arstechnica",       "https://arstechnica.com/ai/feed/",                         2, "media"),
            ("techreview",        "https://www.technologyreview.com/topic/artificial-intelligence/feed", 2, "media"),
            ("theregister",       "https://www.theregister.com/software/ai_ml/headlines.atom",2, "media"),
            ("the-decoder",       "https://the-decoder.com/feed/",                            2, "media"),
            # --- экспертные подборки (уже отфильтрованы человеком) ---
            # The Batch убран: серверу не ответил ни разу, из CI — 403
            ("simonwillison",     "https://simonwillison.net/atom/everything/",               2, "community"),
            ("import-ai",         "https://importai.substack.com/feed",                       2, "community"),
            ("interconnects",     "https://www.interconnects.ai/feed",                        2, "community"),
            ("latent-space",      "https://www.latent.space/feed",                            2, "community"),
            ("raschka",           "https://magazine.sebastianraschka.com/feed",               2, "community"),
            ("ieee-spectrum-ai",  "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", 1, "media"),
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

    "dev": {
        "title": "Софт и разработка",
        "short": "Разработка",
        "emoji": "🧑‍💻",
        "aliases": ("софт", "разработка", "dev", "программирование", "software",
                    "код", "опенсорс"),
        "persona": (
            "практикующий разработчик. Интересны: релизы языков, рантаймов и "
            "баз данных с тем, что в них реально изменилось, важные изменения "
            "в ядре Linux и системном софте, инструменты, которые экономят "
            "время, разборы инцидентов и производительности, лицензии и "
            "судьба open-source проектов, уязвимости в том, чем он пользуется. "
            "НЕ интересны: «10 библиотек, которые изменят вашу жизнь», "
            "туториалы уровня hello world, вакансии и карьерные советы, "
            "холивары о языках, анонсы конференций."
        ),
        "keywords": ["linux", "kernel", "open source", "python", "rust", "golang",
                     "javascript", "typescript", "kubernetes", "docker",
                     "postgres", "sqlite", "database", "compiler", "webassembly",
                     "git", "api", "framework"],
        "feeds": [
            # --- независимая техническая пресса ---
            ("lwn",            "https://lwn.net/headlines/newrss",            1, "media"),
            ("theregister-dev","https://www.theregister.com/software/headlines.atom", 2, "media"),
            ("infoworld",      "https://www.infoworld.com/feed/",             2, "media"),
            ("infoq",          "https://feed.infoq.com/",                     2, "media"),
            # по-русски: релизы, уязвимости, ядро — с подробностями и ссылкой
            # на первоисточник. Лента широкая: раздел по содержанию
            ("opennet",        "https://www.opennet.ru/opennews/opennews_all_utf.rss", 2, "media"),
            # --- первоисточники релизов ---
            ("kernel-org",     "https://www.kernel.org/feeds/kdist.xml",      1, "opensource"),
            ("rust-blog",      "https://blog.rust-lang.org/feed.xml",         1, "opensource"),
            ("go-blog",        "https://go.dev/blog/feed.atom",               1, "opensource"),
            ("python-insider", "https://blog.python.org/feeds/posts/default",  1, "opensource"),
            ("nodejs-blog",    "https://nodejs.org/en/feed/blog.xml",         1, "opensource"),
            ("postgresql",     "https://www.postgresql.org/news.rss",         1, "opensource"),
            ("kubernetes",     "https://kubernetes.io/feed.xml",              1, "opensource"),
            ("debian-news",    "https://www.debian.org/News/news",            1, "opensource"),
            ("github-blog",    "https://github.blog/feed/",                   1, "labs"),
            # --- сообщество ---
            ("hn-front",       "https://hnrss.org/frontpage?points=150",      3, "community"),
            ("lobsters",       "https://lobste.rs/rss",                       3, "community"),
            ("stackoverflow",  "https://stackoverflow.blog/feed/",            2, "community"),
        ],
    },

    "hardware": {
        "title": "Компьютерное железо",
        "short": "Железо",
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
            ("techspot",      "https://www.techspot.com/backend.xml",          2, "media"),
            ("phoronix",      "https://www.phoronix.com/rss.php",              2, "media"),
            ("servethehome",  "https://www.servethehome.com/feed/",            2, "media"),
            ("nextplatform",  "https://www.nextplatform.com/feed/",            2, "media"),
            # микроархитектура с замерами — жанр, которого не осталось после
            # закрытия AnandTech
            ("chipsandcheese","https://chipsandcheese.com/feed/",              2, "media"),
            ("ars-gadgets",   "https://arstechnica.com/gadgets/feed/",         2, "media"),
            ("nvidia-blog",   "https://blogs.nvidia.com/feed/",                1, "labs"),
            ("ixbt",          "https://www.ixbt.com/export/news.rss",          2, "media"),
            ("3dnews",        "https://3dnews.ru/news/rss/",                   2, "media"),
            # notebookcheck-ru убран: ленты по старому адресу нет (404), а
            # английская закрыта от роботов
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
            ("tc-robotics",    "https://techcrunch.com/category/robotics/feed/",     2, "media"),
            ("robohub",        "https://robohub.org/feed/",                          2, "community"),
            ("techxplore-bot", "https://techxplore.com/rss-feed/robotics-news/",     2, "media"),
            ("dronelife",      "https://dronelife.com/feed/",                        2, "media"),
            ("nvidia-robotics","https://blogs.nvidia.com/blog/category/robotics/feed/", 1, "labs"),
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
            # пуски и их итоги: редакция, которая ведёт репортажи с космодромов
            ("spaceflightnow",  "https://spaceflightnow.com/feed/",                       2, "media"),
            # бюджеты, законы и решения агентств — то, что в портрете читателя
            ("spacepolicy",     "https://spacepolicyonline.com/feed/",                    2, "media"),
            ("payload",         "https://payloadspace.com/feed/",                         2, "media"),
            ("european-spaceflight", "https://europeanspaceflight.com/feed/",             2, "media"),
            ("ars-space",       "https://arstechnica.com/space/feed/",                    2, "media"),
            ("planetary",       "https://www.planetary.org/rss/articles",                 2, "media"),
            ("phys-space",      "https://phys.org/rss-feed/space-news/",                  2, "media"),
            ("universetoday",   "https://www.universetoday.com/feed",                     2, "media"),
        ],
    },

    "climate": {
        "title": "Климат и экология",
        "short": "Климат",
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
            # climate.gov закрыт летом 2025 года и ведёт на noaa.gov — лента
            # noaa-climate убрана, NOAA осталась своей общей лентой ниже
            ("guardian-environment", "https://www.theguardian.com/environment/rss",     2, "media"),
            ("insideclimate",   "https://insideclimatenews.org/feed/",                  2, "media"),
            ("yale-e360",       "https://e360.yale.edu/feed.xml",                       2, "media"),
            ("climatehome",     "https://www.climatechangenews.com/feed/",              2, "media"),
            ("grist",           "https://grist.org/feed/",                              2, "media"),
            ("mongabay",        "https://news.mongabay.com/feed/",                      2, "media"),
            ("canary",          "https://www.canarymedia.com/rss.rss",                  2, "media"),
            ("eos",             "https://eos.org/feed",                                 2, "media"),
            ("phys-earth",      "https://phys.org/rss-feed/earth-news/",                2, "media"),
            ("sd-climate",      "https://www.sciencedaily.com/rss/earth_climate.xml",   2, "media"),
            # климатологи о новых данных и спорных работах — разбор из первых рук
            ("realclimate",     "https://www.realclimate.org/index.php/feed/",          2, "community"),
            # --- первоисточники климатических данных и докладов ---
            ("ipcc",            "https://www.ipcc.ch/feed/",                            1, "policy"),
            # своей ленты у WMO больше нет (все известные адреса — 404): берём её
            # материалы витриной Google News, как Reuters
            ("wmo",             "https://news.google.com/rss/search?q=when:7d+site:wmo.int&hl=en-US&gl=US&ceid=US:en", 1, "policy"),
            ("copernicus",      "https://climate.copernicus.eu/rss.xml",                1, "research"),
            ("noaa-news",       "https://www.noaa.gov/rss.xml",                         1, "policy"),
            ("nasa-earthobs",   "https://earthobservatory.nasa.gov/feeds/earth-observatory.rss", 1, "research"),
            ("berkeley-earth",  "https://berkeleyearth.org/feed/",                      1, "research"),
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
            # nature-news убран: по его адресу страница сайта, а не лента.
            # Новости Nature и так приходят общей лентой журнала
            ("nature",       "https://www.nature.com/nature.rss",                 1, "research"),
            ("science-news", "https://www.science.org/rss/news_current.xml",      1, "research"),
            ("physics-aps",  "https://feeds.aps.org/rss/recent/physics.xml",      1, "research"),
            ("quanta",       "https://api.quantamagazine.org/feed/",              2, "media"),
            ("sciencenews",  "https://www.sciencenews.org/feed",                  2, "media"),
            ("ars-science",  "https://arstechnica.com/science/feed/",             2, "media"),
            ("bbc-science",  "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", 2, "media"),
            ("phys-all",     "https://phys.org/rss-feed/",                        2, "media"),
            ("sd-science",   "https://www.sciencedaily.com/rss/top/science.xml",  2, "media"),
            ("newscientist", "https://www.newscientist.com/feed/home/",           2, "media"),
            ("nplus1",       "https://nplus1.ru/rss",                             2, "media"),
            # по-русски, и пишут их учёные: «Элементы» и «Троицкий вариант»
            ("elementy",     "https://elementy.ru/rss/news",                      2, "media"),
            ("trv-science",  "https://www.trv-science.ru/feed/",                  2, "media"),
            ("ieee-spectrum","https://spectrum.ieee.org/customfeeds/feed/all-topics/rss", 1, "media"),
            # отзывы статей и подлоги — прямой сигнал НЕдостоверности
            ("retractionwatch", "https://retractionwatch.com/feed/",              1, "research"),
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
            ("kff-health",    "https://kffhealthnews.org/feed/",                            2, "media"),
            ("medpage",       "https://www.medpagetoday.com/rss/headlines.xml",             2, "media"),
            ("medicalxpress", "https://medicalxpress.com/rss-feed/",                        2, "media"),
            # --- журналы первого ряда ---
            ("nature-med",    "https://www.nature.com/nm.rss",                              1, "research"),
            ("lancet",        "https://www.thelancet.com/rssfeed/lancet_current.xml",       1, "research"),
            ("bmj",           "https://www.bmj.com/rss/recent.xml",                         1, "research"),
            ("jama",          "https://jamanetwork.com/rss/site_3/67.xml",                  1, "research"),
            # --- вспышки заболеваний ---
            # собственная лента ВОЗ застыла в феврале 2026 года, хотя сайт
            # обновляется каждый день. Берём его витриной Google News, как Reuters
            ("who-news",      "https://news.google.com/rss/search?q=when:7d+site:who.int&hl=en-US&gl=US&ceid=US:en", 1, "policy"),
            ("ecdc",          "https://www.ecdc.europa.eu/en/taxonomy/term/1307/feed",      1, "policy"),
            # CIDRAP (Университет Миннесоты): вспышки с цифрами и ссылками. Его
            # /rss.xml молчит с 2022 года — тоже через витрину
            ("cidrap",        "https://news.google.com/rss/search?q=when:3d+site:cidrap.umn.edu&hl=en-US&gl=US&ceid=US:en", 2, "media"),
            ("fda-press",     "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", 1, "policy"),
            ("sd-medicine",   "https://www.sciencedaily.com/rss/health_medicine.xml",       2, "media"),
            # --- регуляторы и доказательная медицина ---
            ("ema",           "https://www.ema.europa.eu/en/news.xml",                      1, "policy"),
            # у NICE ленты больше нет (все известные адреса — 404)
            ("nice",          "https://news.google.com/rss/search?q=when:7d+site:nice.org.uk&hl=en-US&gl=US&ceid=US:en", 1, "policy"),
            ("cochrane",      "https://www.cochranelibrary.com/cdsr/table-of-contents/rss.xml", 1, "research"),
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
            # Harvard Health (404 по всем адресам) и NIH (403 — закрыт от
            # роботов) убраны: ни разу не ответили серверу. Первоисточник
            # раздела теперь — клиника Мэйо
            ("mayo-clinic",     "https://newsnetwork.mayoclinic.org/feed/",                     1, "research"),
            ("bbc-health",      "https://feeds.bbci.co.uk/news/health/rss.xml",                 2, "media"),
            ("guardian-health", "https://www.theguardian.com/society/health/rss",               2, "media"),
            ("npr-health",      "https://feeds.npr.org/1128/rss.xml",                           2, "media"),
            # о своих исследованиях пишут сами учёные, редакция проверяет
            ("conversation-health", "https://theconversation.com/uk/health/articles.atom",      2, "media"),
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
            ("politico-eu",    "https://www.politico.eu/feed/",                         2, "media"),
            ("dw-russian",     "https://rss.dw.com/rdf/rss-ru-all",                     2, "media"),
            # общественное вещание: мир без таблоидного уклона
            ("npr-news",       "https://feeds.npr.org/1001/rss.xml",                    2, "media"),
            ("pbs-world",      "https://www.pbs.org/newshour/feeds/rss/world",          2, "media"),
            ("france24",       "https://www.france24.com/en/rss",                       2, "media"),
            ("un-news",        "https://news.un.org/feed/subscribe/ru/news/all/rss.xml", 1, "policy"),
            # --- по-русски. Рядом с госагентствами — независимые редакции:
            # иначе о России в выпуске говорили бы только ТАСС и РИА
            ("meduza",         "https://meduza.io/rss/news",                            2, "media"),
            ("novaya-europe",  "https://novayagazeta.eu/feed/rss",                      2, "media"),
            ("kommersant-politics", "https://www.kommersant.ru/RSS/section-politics.xml", 2, "media"),
            ("tass",           "https://tass.ru/rss/v2.xml",                            2, "media"),
            ("rbc",            "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",     2, "media"),
            # --- мировые агентства. У Reuters, AP и AFP публичного RSS нет:
            # берём их материалы витриной Google News (ссылки ведут на
            # оригинал). У AP лента жила на feeds.apnews.com — домена больше нет
            ("reuters-world",  "https://news.google.com/rss/search?q=when:1d+site:reuters.com/world&hl=en-US&gl=US&ceid=US:en", 1, "media"),
            ("ap-topnews",     "https://news.google.com/rss/search?q=when:1d+site:apnews.com&hl=en-US&gl=US&ceid=US:en", 1, "media"),
            ("afp",            "https://news.google.com/rss/search?q=when:1d+site:afp.com&hl=en-US&gl=US&ceid=US:en", 1, "media"),
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
            ("vedomosti",        "https://www.vedomosti.ru/rss/rubric/economics",         2, "media"),
            # независимая деловая редакция о российской экономике
            ("thebell",          "https://thebell.io/feed",                               2, "media"),
            ("interfax",         "https://www.interfax.ru/rss.asp",                       2, "media"),
            ("cbr",              "https://www.cbr.ru/rss/eventrss",                       1, "policy"),
            ("economist-fin",    "https://www.economist.com/finance-and-economics/rss.xml", 1, "media"),
            ("guardian-business","https://www.theguardian.com/business/rss",              2, "media"),
            ("marketwatch",      "https://feeds.content.dowjones.io/public/rss/mw_topstories", 2, "media"),
            ("yahoo-finance",    "https://finance.yahoo.com/news/rssindex",               3, "media"),
            # --- деловые издания и рынки ---
            ("ft",               "https://www.ft.com/rss/home",                           1, "media"),
            ("bloomberg-markets","https://feeds.bloomberg.com/markets/news.rss",          1, "media"),
            ("reuters-markets",  "https://news.google.com/rss/search?q=when:1d+site:reuters.com/markets&hl=en-US&gl=US&ceid=US:en", 1, "media"),
            # --- статистика и центробанки (первоисточники цифр) ---
            # Eurostat и МВФ своих лент роботам больше не отдают (404 и 403) —
            # их материалы идут витриной Google News. НБП убран: вместо ленты
            # его сайт отдаёт страницу
            ("eurostat",         "https://news.google.com/rss/search?q=when:7d+site:ec.europa.eu/eurostat&hl=en-US&gl=US&ceid=US:en", 1, "policy"),
            ("ecb",              "https://www.ecb.europa.eu/rss/press.html",              1, "policy"),
            ("fed-press",        "https://www.federalreserve.gov/feeds/press_all.xml",    1, "policy"),
            ("boe",              "https://www.bankofengland.co.uk/rss/news",              1, "policy"),
            ("bis",              "https://www.bis.org/doclist/all_pressrels.rss",         1, "policy"),
            ("bls",              "https://www.bls.gov/feed/bls_latest.rss",               1, "policy"),
            ("imf",              "https://news.google.com/rss/search?q=when:7d+site:imf.org&hl=en-US&gl=US&ceid=US:en", 1, "policy"),
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
            # --- первоисточники: без них срочное в спорте держалось только на
            # широком консенсусе. Агентство — через витрину, как в «Политике»
            ("reuters-sports", "https://news.google.com/rss/search?q=when:1d+site:reuters.com/sports&hl=en-US&gl=US&ceid=US:en", 1, "media"),
            ("f1",             "https://www.formula1.com/en/latest/all.xml", 1, "policy"),
            # допинг и дисквалификации из первых рук; своя лента WADA пуста
            ("wada",           "https://news.google.com/rss/search?q=when:7d+site:wada-ama.org&hl=en-US&gl=US&ceid=US:en", 1, "policy"),
            # --- редакции ---
            ("bbc-sport",      "https://feeds.bbci.co.uk/sport/rss.xml",     2, "media"),
            ("the-athletic",   "https://www.nytimes.com/athletic/rss/news/", 2, "media"),
            ("espn",           "https://www.espn.com/espn/rss/news",         2, "media"),
            ("guardian-sport", "https://www.theguardian.com/sport/rss",      2, "media"),
            ("skysports",      "https://www.skysports.com/rss/12040",        2, "media"),
            ("sports-ru",      "https://www.sports.ru/rss/all_news.xml",     2, "media"),
            ("championat",     "https://www.championat.com/rss/news/",       2, "media"),
            ("sport-express",  "https://www.sport-express.ru/services/materials/news/se/", 2, "media"),
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
            ("ptwc",        "https://www.tsunami.gov/events/xml/PHEBAtom.xml",                            1, "policy"),
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
            ("thewrap",         "https://www.thewrap.com/feed/",              2, "media"),
            # международный кинобизнес и фестивали — не только Голливуд
            ("screendaily",     "https://www.screendaily.com/45202.rss",      2, "media"),
            ("vulture",         "https://feeds.feedburner.com/nymag/vulture", 2, "media"),
            ("guardian-film",   "https://www.theguardian.com/film/rss",       2, "media"),
            # Collider убран: сайт Valnet, конвейер «10 фильмов, которые…»
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
            # --- технический разбор релизов: производительность, баги, железо ---
            ("digitalfoundry",  "https://www.digitalfoundry.net/feed",          2, "media"),
            # --- профильные СМИ ---
            # Polygon убран: в мае 2025-го его купил Valnet и уволил большую
            # часть редакции. Независимые редакции — Aftermath и Game File
            ("eurogamer",       "https://www.eurogamer.net/feed",               2, "media"),
            ("aftermath",       "https://aftermath.site/feed",                  2, "media"),
            ("gamefile",        "https://www.gamefile.news/feed",               2, "media"),
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
            # технические изменения протокола Bitcoin из первых рук
            ("bitcoin-optech","https://bitcoinops.org/feed.xml",                 1, "research"),
            ("bitcoinmag",    "https://bitcoinmagazine.com/feed",                2, "media"),
            # --- противовес рекламе бирж: взломы, мошенничество, расследования
            ("protos",        "https://protos.com/feed/",                        2, "media"),
            ("web3igg",       "https://www.web3isgoinggreat.com/feed.xml",       2, "community"),
        ],
    },

    "cybersec": {
        "title": "Кибербезопасность",
        "short": "Кибербез",
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
            # --- редакции ---
            ("krebs",          "https://krebsonsecurity.com/feed/",                       1, "community"),
            ("therecord",      "https://therecord.media/feed/",                           2, "media"),
            ("risky-biz",      "https://news.risky.biz/rss/",                             2, "media"),
            ("bleepingcomputer","https://www.bleepingcomputer.com/feed/",                 2, "media"),
            ("securityweek",   "https://www.securityweek.com/feed/",                      2, "media"),
            ("thehackernews",  "https://thehackernews.com/feeds/posts/default",           2, "media"),
            ("schneier",       "https://www.schneier.com/feed/",                          1, "community"),
            ("darkreading",    "https://www.darkreading.com/rss.xml",                     2, "media"),
            # --- исследователи: разборы кампаний с индикаторами ---
            ("project-zero",   "https://googleprojectzero.blogspot.com/feeds/posts/default", 1, "research"),
            ("citizenlab",     "https://citizenlab.ca/feed/",                             1, "research"),
            ("talos",          "https://blog.talosintelligence.com/rss/",                 1, "research"),
            ("unit42",         "https://unit42.paloaltonetworks.com/feed/",               1, "research"),
            ("securelist",     "https://securelist.com/feed/",                            1, "research"),
            # --- госслужбы и дежурные: что атакуют прямо сейчас ---
            ("cisa-advisories","https://www.cisa.gov/cybersecurity-advisories/all.xml",   1, "policy"),
            ("ncsc-uk",        "https://www.ncsc.gov.uk/api/1/services/v1/all-rss-feed.xml", 1, "policy"),
            ("sans-isc",       "https://isc.sans.edu/rssfeed.xml",                        1, "community"),
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
#: Крипта и «свой» остаются доступными, но в подборку не лезут:
#: их включают вручную — /sections add crypto.
#:
#: Порядок задаёт и вид выпуска, и то, под какой вывеской показывается
#: материал, у которого раздел определить не удалось (`sections.source_map`).
DEFAULT_SECTIONS = [
    "ai", "dev", "cybersec", "hardware", "robots", "space", "climate",
    "science", "medicine", "health", "politics", "economy", "sports",
    "incidents", "cinema", "games",
]

#: Сколько места раздел занимает в выпуске: доля от `per_section`.
#: Шестнадцать разделов по две новости — это тридцать две штуки дважды в день,
#: и «Игры» с «Кино» съедали бы в выпуске столько же, сколько «Политика».
#: Раздел с весом 0.5 при обычной настройке получает одну новость вместо двух.
#: Раздел, которого здесь нет, весит 1.0. На `/news <раздел>` вес не влияет:
#: там читатель просит конкретное число.
SECTION_WEIGHT = {
    "sports": 0.5,
    "cinema": 0.5,
    "games":  0.5,
    "health": 0.5,
}

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


def short(topic: str) -> str:
    """Название для кнопки в пол-экрана: «Железо», а не «Компьютерное железо».

    Кнопки разделов в оглавлении стоят по две в ряд, и длинное название в
    такую не помещается — телефон обрезает его на полуслове. Короткого
    имени нет — берётся обычное.
    """
    body = PROFILES.get(topic, {})
    return str(body.get("short") or body.get("title") or topic)


def emoji(topic: str) -> str:
    return str(PROFILES.get(topic, {}).get("emoji") or "📌")


def label(topic: str) -> str:
    """«🩺 Медицина» — то, что видно в списках и заголовках выпуска."""
    return "%s %s" % (emoji(topic), title(topic))


def weight(topic: str) -> float:
    """Доля выпуска, которую занимает раздел. По умолчанию 1.0.

    Профиль может задать свой вес (в том числе пользовательский из
    profiles.json), иначе берётся встроенный SECTION_WEIGHT.
    """
    body = PROFILES.get(topic) or {}
    try:
        value = float(body.get("weight", SECTION_WEIGHT.get(topic, 1.0)))
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0
