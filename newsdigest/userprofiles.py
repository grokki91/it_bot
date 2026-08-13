# -*- coding: utf-8 -*-
"""Пользовательские темы и источники: ~/.newsdigest/profiles.json.

Раньше добавить источник значило открыть исходник и перезапустить демон.
Теперь встроенные темы из profiles.BUILTIN — это фундамент, а поверх него
кладётся правка из JSON-файла, который можно менять и руками, и командами
бота (/feed add, /feed rm, /keywords).

Формат файла — словарь тем; в теме любые из ключей:

    {
      "ai": {
        "feeds":        [["my-blog", "https://example.com/rss", 1, "labs"]],
        "remove_feeds": ["theverge"],
        "keywords":     ["mlops"],
        "persona":      "…"                 // заменяет встроенный портрет
      },
      "гаджеты": { "persona": "…", "keywords": [...], "feeds": [...] }
    }

Для встроенной темы правка ДОПОЛНЯЕТ её (а remove_feeds убирает лишнее);
для новой — задаёт целиком. Сломанный файл не роняет бота: он пишет
ошибку в лог и работает на встроенных темах.
"""
from __future__ import annotations

import json
import re
import urllib.parse

from .config import PROFILES_FILE, log
from .profiles import BUILTIN, PROFILES

#: служебные домены второго уровня — в имени источника они бесполезны
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne"}

TIERS = (1, 2, 3)
CATEGORIES = ("labs", "research", "opensource", "media", "community",
              "business", "policy", "other")


# ------------------------------------------------------------------ чтение
def read() -> dict:
    """Содержимое profiles.json. Пусто или сломано — пустой словарь."""
    if not PROFILES_FILE.exists():
        return {}
    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        log.error("Не читается %s (%s) — работаю на встроенных темах",
                  PROFILES_FILE, exc)
        return {}
    if not isinstance(data, dict):
        log.error("%s: ожидался объект с темами — игнорирую", PROFILES_FILE)
        return {}
    return data


def write(data: dict) -> None:
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _feed_tuple(raw):
    """Терпимо читаем строку фида: список, кортеж или просто URL."""
    if isinstance(raw, str):
        return (source_id_for(raw), raw, 2, "media")
    if isinstance(raw, dict):
        url = str(raw.get("url") or "")
        return (str(raw.get("id") or source_id_for(url)), url,
                int(raw.get("tier") or 2), str(raw.get("category") or "media"))
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        source_id, url = str(raw[0]), str(raw[1])
        tier = int(raw[2]) if len(raw) > 2 else 2
        category = str(raw[3]) if len(raw) > 3 else "media"
        return (source_id, url, tier, category)
    raise ValueError("не понимаю запись источника: %r" % (raw,))


def apply() -> dict:
    """Пересобирает PROFILES: встроенные темы плюс пользовательская правка."""
    user = read()
    merged = {}
    for name, body in BUILTIN.items():
        merged[name] = {"persona": body["persona"],
                        "keywords": list(body["keywords"]),
                        "feeds": list(body["feeds"])}

    for name, patch in user.items():
        if not isinstance(patch, dict):
            log.error("profiles.json: тема %r описана неправильно — пропускаю", name)
            continue
        base = merged.setdefault(name, {"persona": "внимательный читатель.",
                                        "keywords": [], "feeds": []})
        if patch.get("persona"):
            base["persona"] = str(patch["persona"])

        drop = {str(x) for x in (patch.get("remove_feeds") or [])}
        if drop:
            base["feeds"] = [f for f in base["feeds"] if f[0] not in drop]

        known = {f[0] for f in base["feeds"]}
        for raw in (patch.get("feeds") or []):
            try:
                feed = _feed_tuple(raw)
            except (ValueError, TypeError) as exc:
                log.error("profiles.json, тема %r: %s", name, exc)
                continue
            if feed[0] in known:
                base["feeds"] = [feed if f[0] == feed[0] else f for f in base["feeds"]]
            else:
                base["feeds"].append(feed)
                known.add(feed[0])

        extra = [str(k).lower() for k in (patch.get("keywords") or [])]
        base["keywords"] = list(dict.fromkeys(list(base["keywords"]) + extra))
        for word in {str(k).lower() for k in (patch.get("remove_keywords") or [])}:
            base["keywords"] = [k for k in base["keywords"] if k != word]

    PROFILES.clear()
    PROFILES.update(merged)
    return PROFILES


# ------------------------------------------------------------------- правка
def source_id_for(url: str) -> str:
    """Короткое имя источника из ссылки: theverge, rust-lang, gh-vllm."""
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return "source"
    host = (parts.netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    path = [p for p in (parts.path or "").split("/") if p]
    if host == "github.com" and len(path) >= 2:      # релизы репозитория
        return ("gh-" + re.sub(r"[^a-z0-9-]+", "-", path[1].lower()))[:28]
    labels = [p for p in host.split(".") if p]
    if not labels:
        return "source"
    name = labels[-2] if len(labels) >= 2 else labels[0]
    # example.co.uk: второй уровень тут служебный, брать надо третий
    if name in SECOND_LEVEL and len(labels) >= 3:
        name = labels[-3]
    return (re.sub(r"[^a-z0-9-]+", "-", name).strip("-") or "source")[:28]


def unique_id(topic: str, wanted: str) -> str:
    taken = {f[0] for f in PROFILES.get(topic, {}).get("feeds", [])}
    if wanted not in taken:
        return wanted
    for n in range(2, 50):
        candidate = "%s-%d" % (wanted, n)
        if candidate not in taken:
            return candidate
    return wanted + "-x"


def _patch(data: dict, topic: str) -> dict:
    return data.setdefault(topic, {})


def add_feed(topic: str, url: str, tier=2, category="media", source_id="") -> tuple:
    """Добавляет источник в тему. Возвращает готовый кортеж фида."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("нужна ссылка на фид, начинающаяся с http:// или https://")
    tier = int(tier) if int(tier) in TIERS else 2
    category = category if category in CATEGORIES else "media"
    for feed in PROFILES.get(topic, {}).get("feeds", []):
        if feed[1] == url:
            raise ValueError("такой источник уже есть под именем %s" % feed[0])
    source_id = unique_id(topic, source_id.strip() or source_id_for(url))

    data = read()
    patch = _patch(data, topic)
    feeds = [list(_feed_tuple(f)) for f in (patch.get("feeds") or [])]
    feeds.append([source_id, url, tier, category])
    patch["feeds"] = feeds
    patch["remove_feeds"] = [x for x in (patch.get("remove_feeds") or [])
                             if x != source_id]
    write(data)
    apply()
    return (source_id, url, tier, category)


def remove_feed(topic: str, source_id: str) -> bool:
    """Убирает источник — и добавленный руками, и встроенный."""
    source_id = (source_id or "").strip()
    known = {f[0] for f in PROFILES.get(topic, {}).get("feeds", [])}
    if source_id not in known:
        return False
    data = read()
    patch = _patch(data, topic)
    patch["feeds"] = [list(_feed_tuple(f)) for f in (patch.get("feeds") or [])
                      if _feed_tuple(f)[0] != source_id]
    if source_id in {f[0] for f in BUILTIN.get(topic, {}).get("feeds", [])}:
        drop = list(patch.get("remove_feeds") or [])
        if source_id not in drop:
            drop.append(source_id)
        patch["remove_feeds"] = drop
    write(data)
    apply()
    return True


def edit_keywords(topic: str, add=(), remove=()) -> list:
    data = read()
    patch = _patch(data, topic)
    words = [str(k).lower() for k in (patch.get("keywords") or [])]
    for word in add:
        word = str(word).lower().strip()
        if word and word not in words:
            words.append(word)
    patch["keywords"] = words
    drop = {str(k).lower().strip() for k in remove if str(k).strip()}
    if drop:
        patch["keywords"] = [w for w in words if w not in drop]
        builtin_hit = drop & {k.lower() for k in BUILTIN.get(topic, {}).get(
            "keywords", [])}
        if builtin_hit:
            patch["remove_keywords"] = sorted(
                {str(k).lower() for k in (patch.get("remove_keywords") or [])}
                | builtin_hit)
    write(data)
    apply()
    return PROFILES[topic]["keywords"]


def set_persona(topic: str, text: str) -> None:
    data = read()
    _patch(data, topic)["persona"] = text.strip()
    write(data)
    apply()


def is_custom(topic: str, source_id: str) -> bool:
    """True — источник добавлен пользователем, а не встроен в тему."""
    return source_id not in {f[0] for f in BUILTIN.get(topic, {}).get("feeds", [])}
