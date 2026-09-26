# -*- coding: utf-8 -*-
"""Кто такой источник и насколько ему верить.

`tier` в profiles.py отвечает на один вопрос: первоисточник или пересказ. По
нему `rank.primary_of` выбирает, на что вести ссылку. Но тем же tier до сих пор
мерялась и достоверность — и получалось, что блог вендора (tier 1) весит 1.0,
а независимый разбор Ars Technica (tier 2) — 0.6. Пресс-релиз выигрывал у
редакции, которая его проверила.

Здесь два свойства разведены:

    kind        класс источника: агентство, первоисточник, редакция, пересказ
    trust       0..1 — насколько материалу можно верить без подтверждений
    publisher   домен издателя: по нему считается консенсус. Одна редакция
                под шестью фидами (Guardian /world, /business, /sport …) — это
                один издатель, а не шесть подтверждений
    wire        быстрая полоса: агентства и службы оповещения, которые имеет
                смысл опрашивать чаще остальных
    strict      узкий фид: всё, что из него приходит, относится к его разделу,
                и классифицировать содержание незачем

Формат кортежа фида `(id, url, tier, category)` при этом не меняется: его
разбирают пять мест в коде и пользовательский profiles.json. Поэтому реестр
живёт отдельной таблицей, а источник, которого в ней нет (свой фид из
`/feed add`), получает разумные значения по умолчанию: издателя из домена,
доверие — из tier.

`state` здесь про собственность и редакционную подчинённость, а не про страну:
это государственные информагентства, редакция которых подчинена учредителю.
Общественные вещатели (BBC, DW), редакционная независимость которых закреплена
законом, идут как `independent`.
"""
from __future__ import annotations

import urllib.parse

from .profiles import PROFILES

#: доверие по классу источника. Диапазон намеренно тот же, что был у tier
#: ({1: 1.0, 2: 0.6, 3: 0.3}), — прескоринг не должен скакнуть на ровном месте.
KIND_TRUST = {
    "wire":        0.95,   # мировое агентство: AP, Reuters, AFP
    "primary":     0.90,   # первоисточник без маркетинга: журнал, регулятор,
                           # служба оповещения, академическая лаборатория
    "independent": 0.80,   # редакция, которая проверяет факты
    "trade":       0.70,   # профильное СМИ
    "pr":          0.55,   # блог вендора, издателя, студии — пресс-релиз
    "community":   0.55,   # блоги, рассылки, форумы
    "aggregator":  0.45,   # пересказ чужого: phys.org, ScienceDaily, Google News
    "state":       0.35,   # государственное информагентство
    "other":       0.60,
}

#: запасной путь для источника, которого нет в реестре
TIER_TRUST = {1: 0.90, 2: 0.60, 3: 0.30}

#: классы, которым нельзя быть первоисточником кластера, когда рядом есть
#: кто-то, кто эту же новость проверял
WEAK_KINDS = ("pr", "state", "aggregator")
STRONG_KINDS = ("wire", "primary", "independent")

#: хост фида не всегда совпадает с издателем: у Guardian это theguardian.com,
#: а у Reuters через витрину Google News — reuters.com, а не news.google.com
PUBLISHER = {
    "feeds.bbci.co.uk":            "bbc.co.uk",
    "feeds.apnews.com":            "apnews.com",
    "feeds.npr.org":               "npr.org",
    "rss.dw.com":                  "dw.com",
    "rssexport.rbc.ru":            "rbc.ru",
    "feeds.content.dowjones.io":   "marketwatch.com",
    "feeds.bloomberg.com":         "bloomberg.com",
    "api.quantamagazine.org":      "quantamagazine.org",
    "rss.politico.com":            "politico.com",
    "hn.algolia.com":              "news.ycombinator.com",
    # лента фронтпейджа HN и поиск по HN — одно сообщество, а не два голоса
    "hnrss.org":                   "news.ycombinator.com",
    # Science X: phys.org, Medical Xpress и TechXplore — одна редакция, и
    # пресс-релиз, разошедшийся по её лентам, — один голос, а не три. Иначе
    # университетский релиз набирал «трёх издателей» фактчека сам с собой
    "medicalxpress.com":           "phys.org",
    "techxplore.com":              "phys.org",
    "feeds.aps.org":               "aps.org",
    "feed.infoq.com":              "infoq.com",
}

#: kind/wire/strict по источникам. trust берётся из KIND_TRUST, если не задан
#: отдельно; publisher — из домена фида, если не задан отдельно.
#:
#: Витрине Google News и зеркалу на GitHub издатель задаётся всегда: хост
#: ленты там news.google.com и raw.githubusercontent.com, а материалы — WHO,
#: AP, Anthropic. Без этого консенсус считал бы их одной редакцией.
SOURCE_META = {
    # --- ИИ: лаборатории и вендоры ---
    "openai":             {"kind": "pr", "strict": True},
    "google-deepmind":    {"kind": "pr", "strict": True},
    "google-research":    {"kind": "pr", "strict": True},
    "googleblog-ai":      {"kind": "pr", "strict": True},
    "anthropic":          {"kind": "pr", "strict": True,
                           "publisher": "anthropic.com"},
    "meta-engineering":   {"kind": "pr"},
    "nvidia-dev":         {"kind": "pr", "strict": True},
    "huggingface":        {"kind": "pr", "strict": True},
    "microsoft-research": {"kind": "pr", "strict": True},
    "apple-ml":           {"kind": "pr", "strict": True},
    "bair-berkeley":      {"kind": "primary", "strict": True},
    # --- ИИ: СМИ и подборки ---
    "techcrunch":         {"kind": "trade", "strict": True},
    "theverge":           {"kind": "trade", "strict": True},
    "arstechnica":        {"kind": "independent", "strict": True},
    "techreview":         {"kind": "independent", "strict": True},
    "theregister":        {"kind": "trade", "strict": True},
    "the-decoder":        {"kind": "trade", "strict": True},
    "simonwillison":      {"kind": "community"},
    "import-ai":          {"kind": "community", "strict": True},
    "interconnects":      {"kind": "community", "strict": True},
    "latent-space":       {"kind": "community", "strict": True},
    "raschka":            {"kind": "community", "strict": True},
    "ieee-spectrum-ai":   {"kind": "trade", "strict": True},
    "gh-vllm":            {"kind": "primary", "strict": True},
    "gh-llama-cpp":       {"kind": "primary", "strict": True},
    "gh-ollama":          {"kind": "primary", "strict": True},
    "gh-transformers":    {"kind": "primary", "strict": True},
    "gh-pytorch":         {"kind": "primary", "strict": True},
    "r-localllama":       {"kind": "community", "trust": 0.35, "strict": True},

    # --- софт и разработка ---
    "lwn":                {"kind": "independent", "strict": True},
    "theregister-dev":    {"kind": "trade", "strict": True},
    "infoworld":          {"kind": "trade", "strict": True},
    "infoq":              {"kind": "trade", "strict": True},
    "opennet":            {"kind": "trade"},
    "kernel-org":         {"kind": "primary", "strict": True},
    "rust-blog":          {"kind": "primary", "strict": True},
    "go-blog":            {"kind": "primary", "strict": True},
    "python-insider":     {"kind": "primary", "strict": True},
    "nodejs-blog":        {"kind": "primary", "strict": True},
    "postgresql":         {"kind": "primary", "strict": True},
    "kubernetes":         {"kind": "primary", "strict": True},
    "debian-news":        {"kind": "primary", "strict": True},
    "github-blog":        {"kind": "pr", "strict": True},
    "hn-front":           {"kind": "community", "trust": 0.4},
    "lobsters":           {"kind": "community", "trust": 0.4},
    "stackoverflow":      {"kind": "community", "strict": True},

    # --- железо ---
    "tomshardware":       {"kind": "trade", "strict": True},
    "techpowerup":        {"kind": "trade", "strict": True},
    "techspot":           {"kind": "trade", "strict": True},
    "phoronix":           {"kind": "trade"},
    "servethehome":       {"kind": "trade", "strict": True},
    "nextplatform":       {"kind": "trade", "strict": True},
    "chipsandcheese":     {"kind": "independent", "strict": True},
    "ars-gadgets":        {"kind": "independent", "strict": True},
    "nvidia-blog":        {"kind": "pr", "strict": True},
    "ixbt":               {"kind": "trade"},
    "3dnews":             {"kind": "trade"},
    "overclockers":       {"kind": "community"},

    # --- роботы ---
    "ieee-robotics":      {"kind": "trade", "strict": True},
    "robotreport":        {"kind": "trade", "strict": True},
    "tc-robotics":        {"kind": "trade", "strict": True},
    "robohub":            {"kind": "community", "strict": True},
    "techxplore-bot":     {"kind": "aggregator", "strict": True},
    "dronelife":          {"kind": "trade", "strict": True},
    "nvidia-robotics":    {"kind": "pr", "strict": True},
    "gh-ros2":            {"kind": "primary", "strict": True},

    # --- космос ---
    "nasa":               {"kind": "primary", "strict": True},
    "esa":                {"kind": "primary", "strict": True},
    "spacenews":          {"kind": "trade", "strict": True},
    "nasaspaceflight":    {"kind": "trade", "strict": True},
    "spaceflightnow":     {"kind": "trade", "strict": True},
    "spacepolicy":        {"kind": "trade", "strict": True},
    "payload":            {"kind": "trade", "strict": True},
    "european-spaceflight": {"kind": "trade", "strict": True},
    "ars-space":          {"kind": "independent", "strict": True},
    "planetary":          {"kind": "trade", "strict": True},
    "phys-space":         {"kind": "aggregator", "strict": True},
    "universetoday":      {"kind": "trade", "strict": True},

    # --- климат ---
    "carbonbrief":        {"kind": "primary", "strict": True},
    "nature-climate":     {"kind": "primary", "strict": True},
    "guardian-environment": {"kind": "independent", "strict": True},
    "insideclimate":      {"kind": "independent", "strict": True},
    "yale-e360":          {"kind": "independent", "strict": True},
    "climatehome":        {"kind": "trade", "strict": True},
    "grist":              {"kind": "trade", "strict": True},
    "mongabay":           {"kind": "independent", "strict": True},
    "canary":             {"kind": "trade", "strict": True},
    # журнал AGU: науки о Земле вообще, раздел — по содержанию
    "eos":                {"kind": "independent"},
    "phys-earth":         {"kind": "aggregator", "strict": True},
    "sd-climate":         {"kind": "aggregator", "strict": True},
    "realclimate":        {"kind": "community", "trust": 0.75, "strict": True},
    "ipcc":               {"kind": "primary", "strict": True, "wire": True},
    # через витрину это уже не служба оповещения: быструю полосу WMO (и
    # Eurostat с МВФ ниже) не занимает, чтобы не дёргать Google News зря
    "wmo":                {"kind": "primary", "strict": True,
                           "publisher": "wmo.int"},
    "copernicus":         {"kind": "primary", "strict": True},
    "noaa-news":          {"kind": "primary"},
    "nasa-earthobs":      {"kind": "primary", "strict": True},
    "berkeley-earth":     {"kind": "primary", "strict": True},

    # --- наука. Широкие ленты: раздел определяется содержанием ---
    "nature":             {"kind": "primary"},
    "science-news":       {"kind": "primary"},
    "physics-aps":        {"kind": "primary", "strict": True},
    "quanta":             {"kind": "independent"},
    "sciencenews":        {"kind": "independent"},
    "ars-science":        {"kind": "independent"},
    "bbc-science":        {"kind": "independent"},
    "phys-all":           {"kind": "aggregator"},
    "sd-science":         {"kind": "aggregator"},
    "newscientist":       {"kind": "trade"},
    "nplus1":             {"kind": "trade"},
    "elementy":           {"kind": "trade"},
    "trv-science":        {"kind": "trade"},
    "ieee-spectrum":      {"kind": "trade"},
    "retractionwatch":    {"kind": "independent", "strict": True},

    # --- медицина ---
    "statnews":           {"kind": "independent", "strict": True},
    "kff-health":         {"kind": "independent"},
    "medpage":            {"kind": "trade", "strict": True},
    "medicalxpress":      {"kind": "aggregator", "strict": True},
    "nature-med":         {"kind": "primary", "strict": True},
    "lancet":             {"kind": "primary", "strict": True},
    "bmj":                {"kind": "primary", "strict": True},
    "jama":               {"kind": "primary", "strict": True},
    "who-news":           {"kind": "primary", "strict": True, "wire": True,
                           "publisher": "who.int"},
    "ecdc":               {"kind": "primary", "strict": True, "wire": True},
    "cidrap":             {"kind": "independent", "strict": True,
                           "publisher": "cidrap.umn.edu"},
    "fda-press":          {"kind": "primary", "strict": True, "wire": True},
    "sd-medicine":        {"kind": "aggregator", "strict": True},
    "ema":                {"kind": "primary", "strict": True, "wire": True},
    "nice":               {"kind": "primary", "strict": True,
                           "publisher": "nice.org.uk"},
    "cochrane":           {"kind": "primary", "strict": True},

    # --- здоровье ---
    "mayo-clinic":        {"kind": "primary", "strict": True},
    "bbc-health":         {"kind": "independent", "strict": True},
    "guardian-health":    {"kind": "independent", "strict": True},
    "npr-health":         {"kind": "independent", "strict": True},
    "conversation-health": {"kind": "independent", "strict": True},
    "sd-nutrition":       {"kind": "aggregator", "strict": True},
    "sd-fitness":         {"kind": "aggregator", "strict": True},

    # --- политика и мир ---
    "bbc-russian":        {"kind": "independent"},
    "bbc-world":          {"kind": "independent", "wire": True},
    "guardian-world":     {"kind": "independent"},
    "aljazeera":          {"kind": "independent", "wire": True},
    "politico":           {"kind": "trade", "strict": True},
    "politico-eu":        {"kind": "trade"},
    "dw-russian":         {"kind": "independent"},
    "npr-news":           {"kind": "independent", "wire": True},
    "pbs-world":          {"kind": "independent"},
    "france24":           {"kind": "independent", "wire": True},
    "un-news":            {"kind": "primary", "wire": True},
    "meduza":             {"kind": "independent"},
    "novaya-europe":      {"kind": "independent"},
    "kommersant-politics": {"kind": "trade", "strict": True},
    "tass":               {"kind": "state"},
    "rbc":                {"kind": "trade"},
    "reuters-world":      {"kind": "wire", "publisher": "reuters.com", "wire": True},
    "ap-topnews":         {"kind": "wire", "publisher": "apnews.com", "wire": True},
    "afp":                {"kind": "wire", "publisher": "afp.com", "wire": True},

    # --- экономика ---
    "kommersant-econ":    {"kind": "trade", "strict": True},
    "vedomosti":          {"kind": "trade", "strict": True},
    "thebell":            {"kind": "independent", "strict": True},
    "interfax":           {"kind": "wire", "trust": 0.7},
    "cbr":                {"kind": "primary", "strict": True, "wire": True},
    "economist-fin":      {"kind": "independent", "strict": True},
    "guardian-business":  {"kind": "independent", "strict": True},
    "marketwatch":        {"kind": "trade", "strict": True},
    "yahoo-finance":      {"kind": "aggregator", "strict": True},
    "ft":                 {"kind": "independent"},
    "bloomberg-markets":  {"kind": "independent", "strict": True, "wire": True},
    "reuters-markets":    {"kind": "wire", "publisher": "reuters.com",
                           "strict": True, "wire": True},
    "eurostat":           {"kind": "primary", "strict": True,
                           "publisher": "ec.europa.eu"},
    "ecb":                {"kind": "primary", "strict": True, "wire": True},
    "fed-press":          {"kind": "primary", "strict": True, "wire": True},
    "boe":                {"kind": "primary", "strict": True, "wire": True},
    "bis":                {"kind": "primary", "strict": True},
    "bls":                {"kind": "primary", "strict": True, "wire": True},
    "imf":                {"kind": "primary", "strict": True,
                           "publisher": "imf.org"},

    # --- спорт ---
    "reuters-sports":     {"kind": "wire", "publisher": "reuters.com", "strict": True},
    "f1":                 {"kind": "primary", "strict": True},
    "wada":               {"kind": "primary", "strict": True,
                           "publisher": "wada-ama.org"},
    "bbc-sport":          {"kind": "independent", "strict": True},
    "the-athletic":       {"kind": "independent", "strict": True},
    "espn":               {"kind": "trade", "strict": True},
    "guardian-sport":     {"kind": "independent", "strict": True},
    "skysports":          {"kind": "trade", "strict": True},
    "sports-ru":          {"kind": "trade", "strict": True},
    "championat":         {"kind": "trade", "strict": True},
    "sport-express":      {"kind": "trade", "strict": True},
    "cbssports":          {"kind": "trade", "strict": True},

    # --- происшествия ---
    "gdacs":              {"kind": "primary", "strict": True, "wire": True},
    "usgs-quakes":        {"kind": "primary", "strict": True, "wire": True},
    "reliefweb":          {"kind": "primary", "strict": True, "wire": True},
    "nhc-storms":         {"kind": "primary", "strict": True, "wire": True},
    "ptwc":               {"kind": "primary", "strict": True, "wire": True},
    "volcanoes":          {"kind": "primary", "strict": True},
    "ria":                {"kind": "state"},
    "lenta":              {"kind": "trade", "trust": 0.45},

    # --- кино ---
    "variety":            {"kind": "trade", "strict": True},
    "hollywoodreporter":  {"kind": "trade", "strict": True},
    "deadline":           {"kind": "trade", "strict": True},
    "indiewire":          {"kind": "trade", "strict": True},
    "thewrap":            {"kind": "trade", "strict": True},
    "screendaily":        {"kind": "trade", "strict": True},
    "vulture":            {"kind": "trade", "publisher": "vulture.com"},
    "guardian-film":      {"kind": "independent", "strict": True},

    # --- игры ---
    "gamesindustry":      {"kind": "trade", "strict": True},
    "gamedeveloper":      {"kind": "trade", "strict": True},
    "playstation-blog":   {"kind": "pr", "strict": True},
    "xbox-wire":          {"kind": "pr", "strict": True},
    "gh-godot":           {"kind": "primary", "strict": True},
    "digitalfoundry":     {"kind": "independent", "strict": True},
    "eurogamer":          {"kind": "trade", "strict": True},
    "aftermath":          {"kind": "independent", "strict": True},
    "gamefile":           {"kind": "independent", "strict": True},
    "pcgamer":            {"kind": "trade", "strict": True},
    "rockpapershotgun":   {"kind": "trade", "strict": True},
    "vgc":                {"kind": "trade", "strict": True},
    "nintendolife":       {"kind": "trade", "strict": True},
    "dtf":                {"kind": "community"},

    # --- криптовалюты ---
    "coindesk":           {"kind": "trade", "strict": True},
    "cointelegraph":      {"kind": "trade", "trust": 0.5, "strict": True},
    "theblock":           {"kind": "trade", "strict": True},
    "decrypt":            {"kind": "trade", "strict": True},
    "ethereum-blog":      {"kind": "pr", "strict": True},
    "bitcoin-optech":     {"kind": "primary", "strict": True},
    "bitcoinmag":         {"kind": "community", "strict": True},
    "protos":             {"kind": "trade", "strict": True},
    "web3igg":            {"kind": "community", "strict": True},

    # --- кибербезопасность ---
    "krebs":              {"kind": "independent", "strict": True},
    "therecord":          {"kind": "independent", "strict": True},
    "risky-biz":          {"kind": "independent", "strict": True},
    "bleepingcomputer":   {"kind": "trade", "strict": True},
    "securityweek":       {"kind": "trade", "strict": True},
    "thehackernews":      {"kind": "trade", "trust": 0.55, "strict": True},
    "schneier":           {"kind": "community", "trust": 0.75, "strict": True},
    "darkreading":        {"kind": "trade", "strict": True},
    "project-zero":       {"kind": "primary", "strict": True},
    "citizenlab":         {"kind": "primary", "strict": True},
    "talos":              {"kind": "primary", "strict": True},
    "unit42":             {"kind": "primary", "strict": True},
    "securelist":         {"kind": "primary", "strict": True},
    "cisa-advisories":    {"kind": "primary", "strict": True, "wire": True},
    "ncsc-uk":            {"kind": "primary", "strict": True},
    "sans-isc":           {"kind": "primary", "strict": True, "wire": True},

    # --- Hacker News: не фид, а поиск по API (sources.fetch_hackernews) ---
    "hackernews":         {"kind": "community", "trust": 0.4},
    # --- образец в разделе «Свой»: место, куда подставляют свою ленту ---
    "example":            {"kind": "community", "trust": 0.4},

    # --- кандидаты (newsdigest/candidates.py). Класс задан заранее, чтобы
    # добавленный через `feeds --candidates --adopt` источник сразу попал в
    # нужную весовую категорию, а не считался незнакомым
    "mistral":            {"kind": "pr", "strict": True, "publisher": "mistral.ai"},
    "anthropic-research": {"kind": "pr", "strict": True,
                           "publisher": "anthropic.com"},
    "meta-ai":            {"kind": "pr", "strict": True, "publisher": "meta.com"},
    "hf-papers":          {"kind": "community", "strict": True},
    "404media":           {"kind": "independent"},
    "golem-changelog":    {"kind": "pr", "strict": True},
    "djangoproject":      {"kind": "primary", "strict": True},
    "acm-queue":          {"kind": "primary", "strict": True},
    "mozilla-hacks":      {"kind": "primary", "strict": True},
    "oss-security":       {"kind": "primary", "strict": True},
    "amd-press":          {"kind": "pr", "strict": True},
    "nejm":               {"kind": "primary", "strict": True},
    "pnas":               {"kind": "primary", "strict": True},
    "oecd":               {"kind": "primary", "strict": True},
    "cnbc":               {"kind": "trade"},
    "sec-press":          {"kind": "primary"},
    "ember":              {"kind": "primary", "strict": True},
    "bafta":              {"kind": "primary", "strict": True},
    "criterion":          {"kind": "trade", "strict": True},
    "valve-steam":        {"kind": "pr", "strict": True},
    "dji":                {"kind": "pr", "strict": True},
    "suasnews":           {"kind": "trade", "strict": True},

    # --- больше не в подборке и не в кандидатах. Запись остаётся ради
    # истории (`sent`, tools/audit.py) и на случай, если лента у кого-то
    # добавлена через `feeds --candidates --adopt` и живёт в profiles.json
    "the-batch":          {"kind": "community", "strict": True},
    "venturebeat":        {"kind": "trade", "strict": True},
    "notebookcheck":      {"kind": "trade", "strict": True},
    "noaa-climate":       {"kind": "primary", "strict": True},
    "nature-news":        {"kind": "primary"},
    "harvard-health":     {"kind": "primary", "strict": True},
    "nih-news":           {"kind": "primary"},
    "nbp":                {"kind": "primary", "strict": True},
    "collider":           {"kind": "aggregator", "strict": True},
    "polygon":            {"kind": "trade", "trust": 0.5, "strict": True},
    "ai2":                {"kind": "primary", "strict": True},
    "sqlite-news":        {"kind": "primary", "strict": True},
    "msrc":               {"kind": "primary", "strict": True, "wire": True},
    "nvd-recent":         {"kind": "primary", "strict": True, "wire": True},
    "googleblog-sec":     {"kind": "primary", "strict": True},
    "semianalysis":       {"kind": "independent", "strict": True},
    "intel-newsroom":     {"kind": "pr", "strict": True},
    "cdc-newsroom":       {"kind": "primary", "strict": True, "wire": True},
    "cern":               {"kind": "primary", "strict": True},
    "worldbank":          {"kind": "primary", "strict": True},
    "jonathan-space":     {"kind": "primary", "strict": True},
    "nasa-blogs":         {"kind": "primary", "strict": True, "wire": True},
    "nsidc":              {"kind": "primary", "strict": True},
    "olympics":           {"kind": "primary", "strict": True},
    "uefa":               {"kind": "primary", "strict": True},
    "nintendo-pr":        {"kind": "pr", "strict": True},
    "medlineplus":        {"kind": "primary", "strict": True},
    "harvard-chan":       {"kind": "primary", "strict": True},
}

_cache = {}


def reset() -> None:
    """Сбросить разбор PROFILES. Зовётся, когда профили пересобраны."""
    _cache.clear()


def _urls() -> dict:
    """Источник -> ссылка на его фид. Нужна, чтобы вывести издателя."""
    index = _cache.get("urls")
    if index is None:
        index = {}
        for body in PROFILES.values():
            for feed in body.get("feeds") or ():
                if len(feed) >= 2:
                    index.setdefault(str(feed[0]), str(feed[1]))
        _cache["urls"] = index
    return index


def _tiers() -> dict:
    """Источник -> tier. Запасной путь для доверия к чужому фиду."""
    index = _cache.get("tiers")
    if index is None:
        index = {}
        for body in PROFILES.values():
            for feed in body.get("feeds") or ():
                if len(feed) >= 3:
                    index.setdefault(str(feed[0]), feed[2])
        _cache["tiers"] = index
    return index


def host_of(url: str) -> str:
    """Домен издателя из ссылки на фид: www и известные поддомены раскрыты."""
    try:
        host = (urllib.parse.urlparse(str(url)).netloc or "").lower().split(":")[0]
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return PUBLISHER.get(host, host)


def meta(source_id: str) -> dict:
    """Всё, что известно об источнике. Незнакомый — с разумными умолчаниями."""
    source_id = str(source_id or "")
    entry = SOURCE_META.get(source_id) or {}
    kind = str(entry.get("kind") or "other")
    if "trust" in entry:
        value = float(entry["trust"])
    elif entry:
        value = KIND_TRUST.get(kind, KIND_TRUST["other"])
    else:
        # чужой фид из /feed add: верим ему по tier, как верили раньше всем
        value = TIER_TRUST.get(_tiers().get(source_id), KIND_TRUST["other"])
    name = str(entry.get("publisher") or "") or host_of(_urls().get(source_id, ""))
    return {
        "kind": kind,
        "trust": value,
        # издателя не вывести — источник сам себе издатель: так два разных
        # чужих фида остаются двумя независимыми подтверждениями
        "publisher": name or source_id,
        "wire": bool(entry.get("wire")),
        "strict": bool(entry.get("strict")),
    }


def kind(source_id: str) -> str:
    return meta(source_id)["kind"]


def trust(source_id: str) -> float:
    return meta(source_id)["trust"]


def publisher(source_id: str) -> str:
    return meta(source_id)["publisher"]


def is_wire(source_id: str) -> bool:
    return meta(source_id)["wire"]


def is_strict(source_id: str) -> bool:
    return meta(source_id)["strict"]


def publishers(group) -> set:
    """Издатели кластера. По ним считается консенсус: шесть лент Guardian —
    это один издатель, а не шесть подтверждений."""
    return {publisher(item["source_id"]) for item in group}


def wire_ids() -> set:
    """Источники быстрой полосы среди тех, что сейчас используются."""
    return {source_id for source_id in _urls() if is_wire(source_id)}


def demoted(item, group) -> bool:
    """Пресс-релиз (или госагентство) рядом с теми, кто это проверял.

    Такой материал не должен становиться лицом кластера: ссылка в карточке
    поведёт на разбор, а не на анонс.
    """
    if kind(item["source_id"]) not in WEAK_KINDS:
        return False
    return any(kind(other["source_id"]) in STRONG_KINDS for other in group)
