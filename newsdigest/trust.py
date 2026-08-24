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
}

#: kind/wire/strict по источникам. trust берётся из KIND_TRUST, если не задан
#: отдельно; publisher — из домена фида, если не задан отдельно.
SOURCE_META = {
    # --- ИИ: лаборатории и вендоры ---
    "openai":             {"kind": "pr", "strict": True},
    "google-deepmind":    {"kind": "pr", "strict": True},
    "google-research":    {"kind": "pr", "strict": True},
    "meta-engineering":   {"kind": "pr"},
    "nvidia-dev":         {"kind": "pr", "strict": True},
    "huggingface":        {"kind": "pr", "strict": True},
    "microsoft-research": {"kind": "pr", "strict": True},
    "bair-berkeley":      {"kind": "primary", "strict": True},
    # --- ИИ: СМИ и подборки ---
    "techcrunch":         {"kind": "trade", "strict": True},
    "venturebeat":        {"kind": "trade", "strict": True},
    "theverge":           {"kind": "trade", "strict": True},
    "arstechnica":        {"kind": "independent", "strict": True},
    "techreview":         {"kind": "independent", "strict": True},
    "theregister":        {"kind": "trade", "strict": True},
    "simonwillison":      {"kind": "community"},
    "import-ai":          {"kind": "community", "strict": True},
    "the-batch":          {"kind": "community", "strict": True},
    "interconnects":      {"kind": "community", "strict": True},
    "ieee-spectrum-ai":   {"kind": "trade", "strict": True},
    "gh-vllm":            {"kind": "primary", "strict": True},
    "gh-llama-cpp":       {"kind": "primary", "strict": True},
    "gh-ollama":          {"kind": "primary", "strict": True},
    "gh-transformers":    {"kind": "primary", "strict": True},
    "gh-pytorch":         {"kind": "primary", "strict": True},
    "r-localllama":       {"kind": "community", "trust": 0.35, "strict": True},

    # --- железо ---
    "tomshardware":       {"kind": "trade", "strict": True},
    "techpowerup":        {"kind": "trade", "strict": True},
    "phoronix":           {"kind": "trade"},
    "servethehome":       {"kind": "trade", "strict": True},
    "ars-gadgets":        {"kind": "independent", "strict": True},
    "nvidia-blog":        {"kind": "pr", "strict": True},
    "ixbt":               {"kind": "trade"},
    "3dnews":             {"kind": "trade"},
    "notebookcheck":      {"kind": "trade", "strict": True},
    "overclockers":       {"kind": "community"},

    # --- роботы ---
    "ieee-robotics":      {"kind": "trade", "strict": True},
    "robotreport":        {"kind": "trade", "strict": True},
    "robohub":            {"kind": "community", "strict": True},
    "techxplore-bot":     {"kind": "aggregator", "strict": True},
    "dronelife":          {"kind": "trade", "strict": True},
    "gh-ros2":            {"kind": "primary", "strict": True},

    # --- космос ---
    "nasa":               {"kind": "primary", "strict": True},
    "esa":                {"kind": "primary", "strict": True},
    "spacenews":          {"kind": "trade", "strict": True},
    "nasaspaceflight":    {"kind": "trade", "strict": True},
    "ars-space":          {"kind": "independent", "strict": True},
    "phys-space":         {"kind": "aggregator", "strict": True},
    "universetoday":      {"kind": "trade", "strict": True},

    # --- климат ---
    "carbonbrief":        {"kind": "primary", "strict": True},
    "nature-climate":     {"kind": "primary", "strict": True},
    "noaa-climate":       {"kind": "primary", "strict": True},
    "guardian-environment": {"kind": "independent", "strict": True},
    "insideclimate":      {"kind": "independent", "strict": True},
    "yale-e360":          {"kind": "independent", "strict": True},
    "climatehome":        {"kind": "trade", "strict": True},
    "grist":              {"kind": "trade", "strict": True},
    "phys-earth":         {"kind": "aggregator", "strict": True},
    "sd-climate":         {"kind": "aggregator", "strict": True},
    "ipcc":               {"kind": "primary", "strict": True, "wire": True},
    "wmo":                {"kind": "primary", "strict": True, "wire": True},
    "copernicus":         {"kind": "primary", "strict": True},
    "noaa-news":          {"kind": "primary"},

    # --- наука. Широкие ленты: раздел определяется содержанием ---
    "nature":             {"kind": "primary"},
    "science-news":       {"kind": "primary"},
    "quanta":             {"kind": "independent"},
    "phys-all":           {"kind": "aggregator"},
    "sd-science":         {"kind": "aggregator"},
    "newscientist":       {"kind": "trade"},
    "nplus1":             {"kind": "trade"},
    "nature-news":        {"kind": "primary"},
    "ieee-spectrum":      {"kind": "trade"},

    # --- медицина ---
    "statnews":           {"kind": "independent", "strict": True},
    "medicalxpress":      {"kind": "aggregator", "strict": True},
    "nature-med":         {"kind": "primary", "strict": True},
    "lancet":             {"kind": "primary", "strict": True},
    "who-news":           {"kind": "primary", "strict": True, "wire": True},
    "fda-press":          {"kind": "primary", "strict": True, "wire": True},
    "sd-medicine":        {"kind": "aggregator", "strict": True},
    "ema":                {"kind": "primary", "strict": True, "wire": True},
    "nice":               {"kind": "primary", "strict": True},
    "cochrane":           {"kind": "primary", "strict": True},

    # --- здоровье ---
    "harvard-health":     {"kind": "primary", "strict": True},
    "nih-news":           {"kind": "primary"},
    "guardian-health":    {"kind": "independent", "strict": True},
    "npr-health":         {"kind": "independent", "strict": True},
    "sd-nutrition":       {"kind": "aggregator", "strict": True},
    "sd-fitness":         {"kind": "aggregator", "strict": True},

    # --- политика и мир ---
    "bbc-russian":        {"kind": "independent"},
    "bbc-world":          {"kind": "independent", "wire": True},
    "guardian-world":     {"kind": "independent"},
    "aljazeera":          {"kind": "independent", "wire": True},
    "politico":           {"kind": "trade", "strict": True},
    "dw-russian":         {"kind": "independent"},
    "un-news":            {"kind": "primary", "wire": True},
    "tass":               {"kind": "state"},
    "rbc":                {"kind": "trade"},
    "reuters-world":      {"kind": "wire", "publisher": "reuters.com", "wire": True},
    "ap-topnews":         {"kind": "wire", "wire": True},
    "afp":                {"kind": "wire", "publisher": "afp.com", "wire": True},

    # --- экономика ---
    "kommersant-econ":    {"kind": "trade", "strict": True},
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
    "eurostat":           {"kind": "primary", "strict": True, "wire": True},
    "ecb":                {"kind": "primary", "strict": True, "wire": True},
    "nbp":                {"kind": "primary", "strict": True},
    "imf":                {"kind": "primary", "strict": True, "wire": True},

    # --- спорт ---
    "bbc-sport":          {"kind": "independent", "strict": True},
    "espn":               {"kind": "trade", "strict": True},
    "guardian-sport":     {"kind": "independent", "strict": True},
    "skysports":          {"kind": "trade", "strict": True},
    "sports-ru":          {"kind": "trade", "strict": True},
    "championat":         {"kind": "trade", "strict": True},
    "cbssports":          {"kind": "trade", "strict": True},

    # --- происшествия ---
    "gdacs":              {"kind": "primary", "strict": True, "wire": True},
    "usgs-quakes":        {"kind": "primary", "strict": True, "wire": True},
    "reliefweb":          {"kind": "primary", "strict": True, "wire": True},
    "nhc-storms":         {"kind": "primary", "strict": True, "wire": True},
    "volcanoes":          {"kind": "primary", "strict": True},
    "ria":                {"kind": "state"},
    "lenta":              {"kind": "trade", "trust": 0.45},

    # --- кино ---
    "variety":            {"kind": "trade", "strict": True},
    "hollywoodreporter":  {"kind": "trade", "strict": True},
    "deadline":           {"kind": "trade", "strict": True},
    "indiewire":          {"kind": "trade", "strict": True},
    "vulture":            {"kind": "trade"},
    "guardian-film":      {"kind": "independent", "strict": True},
    "collider":           {"kind": "aggregator", "strict": True},

    # --- игры ---
    "gamesindustry":      {"kind": "trade", "strict": True},
    "gamedeveloper":      {"kind": "trade", "strict": True},
    "playstation-blog":   {"kind": "pr", "strict": True},
    "xbox-wire":          {"kind": "pr", "strict": True},
    "gh-godot":           {"kind": "primary", "strict": True},
    "eurogamer":          {"kind": "trade", "strict": True},
    "polygon":            {"kind": "trade", "strict": True},
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
    "bitcoinmag":         {"kind": "community", "strict": True},

    # --- кибербезопасность ---
    "krebs":              {"kind": "independent", "strict": True},
    "bleepingcomputer":   {"kind": "trade", "strict": True},
    "thehackernews":      {"kind": "trade", "trust": 0.55, "strict": True},
    "schneier":           {"kind": "community", "trust": 0.75, "strict": True},
    "darkreading":        {"kind": "trade", "strict": True},
    "project-zero":       {"kind": "primary", "strict": True},
    "cisa-advisories":    {"kind": "primary", "strict": True, "wire": True},

    # --- Hacker News: не фид, а поиск по API (sources.fetch_hackernews) ---
    "hackernews":         {"kind": "community", "trust": 0.4},
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
