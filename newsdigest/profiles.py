# -*- coding: utf-8 -*-
"""Темы: набор источников, ключевых слов и портрет читателя.

Сменить тематику = поменять CFG["topic"] и, если надо, дописать фиды.
    tier: 1 = первоисточник, 2 = профильное СМИ, 3 = агрегатор/форум.
Сломанный фид сам отключится на сутки и будет виден в `digest.py status`.
"""
from __future__ import annotations

import sys

from .config import CFG

BUILTIN = {

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

#: то, чем пользуется остальной код. Наполняется встроенными темами, а поверх
#: них — пользовательскими из ~/.newsdigest/profiles.json (см. userprofiles.py).
PROFILES = {name: dict(body) for name, body in BUILTIN.items()}


def profile(topic: str = "") -> dict:
    prof = PROFILES.get(topic or CFG["topic"])
    if not prof:
        sys.exit("Неизвестная тема %r. Доступны: %s"
                 % (topic or CFG["topic"], ", ".join(PROFILES)))
    return prof
