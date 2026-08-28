# -*- coding: utf-8 -*-
"""Заслон для секретов: что бы ни случилось, ключи наружу не уходят.

У бота три выхода наружу, и каждый когда-нибудь читает посторонний:

    1) языковая модель — чужой сервис, которому мы отдаём текст запроса;
    2) лог `~/.newsdigest/digest.log` — его вставляют в issue на гитхабе;
    3) вывод команд в терминале — его туда же копируют.

Ни в один из них не должны попадать токен бота, ключ модели, пароль страницы,
ссылка с логином и паролем внутри или фид с ключом в параметрах. Раньше
это держалось на аккуратности: секрет лежит в переменной, в промпт мы его не
кладём — значит, всё хорошо. Но текст в промпт приходит не только от нас:
портрет читателя правится руками в `profiles.json`, источники добавляет
владелец, а содержимое новостей вообще приходит из интернета. Достаточно один
раз вписать ключ в адрес ленты — и он уедет к модели вместе с карточкой.

Поэтому здесь не «не пишите секреты», а фильтр на самом выходе:

    llm.llm_json   — чистит КАЖДОЕ сообщение запроса, к какой бы модели он ни
                     шёл (сменится провайдер — чистка останется на месте);
    config.setup_logging — вешает `SecretFilter` на все обработчики логов;
    cli `scrub`    — прогоняет файл или stdin перед вставкой в issue или PR.

Модуль намеренно ни от чего не зависит (только `re` и `logging`): его
подключают и config, и llm, и web — импортный цикл тут был бы некстати.

Ловим двумя способами. Известные значения (`remember`) — точным совпадением:
свой токен мы знаем и вырежем его в любом виде. Всё остальное — по форме:
пары «имя=значение» с говорящим именем, ссылки с логином и паролем, узнаваемые
ключи (`sk-`, `ghp_`, `AKIA`, JWT, PEM). Общий поиск «длинной строки со
случайными буквами» не делаем сознательно: в новостях такого хватает, а
испорченный заголовок хуже перестраховки.
"""
from __future__ import annotations

import logging
import re
import sys

#: чем заменяем найденное. Ни кавычек, ни слэшей — подстановка не ломает JSON
MASK = "[скрыто]"

#: строка с этой пометкой не считается находкой (`--check`): так помечают
#: заведомо игрушечные примеры в документации и тестах
ALLOW = "nd-redact: allow"

#: уже вычищенное значение второй раз не трогаем: без этого правила
#: наслаиваются друг на друга и от маски остаётся хвост
_DONE = r"(?!%s)" % re.escape(MASK)

#: имена параметров ссылки, за которыми прячется ключ (api_key, sig, token).
#: Список широкий: в адресе `key=` — это всегда ключ, а не переменная
_URL_NAMES = (r"(?:[\w.\-]*[_.\-])?"
              r"(?:api_?key|access_?key|secret_?key|private_?key|secret|token|"
              r"password|passwd|pwd|passphrase|credentials?|session|signature|"
              r"auth|sig|key)s?")

#: ...а вот в паре «имя = значение» список нарочно узкий. Через тот же фильтр
#: идут и куски кода в логах, где `key=lambda ...`, `tokens = ...` и
#: `session = db()` встречаются на каждом шагу, и вырезать их значения нельзя
_ASSIGN_NAMES = (r"(?:[\w.\-]*[_.\-])?"
                 r"(?:api_?key|apikey|access_?key|secret_?key|private_?key|"
                 r"client_?secret|secret|token|password|passwd|passphrase|"
                 r"pwd|credentials?)")

#: `config.TG_TOKEN`, `os.environ.get`, `secrets.token_urlsafe` — это код, а
#: не значение: в исходниках и логах такое встречается рядом с теми же
#: словами, что и настоящий ключ
_CODE = r"(?!(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*(?![\w.=/+-]))"

#: значение, похожее на секрет: латиница с цифрой — или просто длинное.
#: Восемь символов с цифрой — это пароль, семь («python3») — обычное слово
_SECRETISH = (_CODE + r"(?:(?=[A-Za-z0-9._+/=-]*\d)[A-Za-z0-9._+/=-]{8,}"
              r"|[A-Za-z0-9._+/=-]{12,})")

#: (имя правила, что искать, чем заменить). Порядок важен: сначала крупное
#: (PEM-блок целиком), потом точечное
RULES = (
    ("private-key",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
                r"-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
     MASK),
    # ключи узнаваемой формы — их видно и без имени рядом
    ("telegram-token", re.compile(r"\b\d{6,12}:AA[\w-]{30,}\b"), MASK),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), MASK),
    ("github-token",
     re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}\b"), MASK),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), MASK),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), MASK),
    ("google-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"), MASK),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
                       r"\.[A-Za-z0-9_-]{4,}"), MASK),
    # логин и пароль прямо в ссылке: https://user:пароль@host/feed.xml  # nd-redact: allow
    ("url-password",
     re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]{1,64}:)"
                + _DONE + r"[^/\s@]{1,128}(@)"),
     r"\1" + MASK + r"\2"),
    # ...и он же в параметрах: ?api_key=..., &access_token=...  # nd-redact: allow
    ("url-param",
     re.compile(r"(?i)([?&;]%s=)%s[^&\s\"'<>]+" % (_URL_NAMES, _DONE)),
     r"\1" + MASK),
    # Authorization: Bearer ..., basic ...
    ("bearer",
     re.compile(r"(?i)\b(bearer|basic)\s+%s[A-Za-z0-9._\-+/=]{12,}" % _DONE),
     r"\1 " + MASK),
    # «пароль от панели: hunter2», «токен = ...» — по-русски секрет диктуют
    # именно так. Правило нарочно узкое, потому что через тот же фильтр идут
    # новости: нужен явный разделитель, а значение должно быть латиницей И
    # выглядеть как секрет — с цифрой или подлиннее. Иначе «Токен на бирже:
    # Binance объявила...» превратилось бы в «Токен на бирже: [скрыто]»
    ("assignment-ru",
     re.compile(r"(?i)\b((?:пароль|парол[ья]|токен|токена|секрет)\w*"
                r"(?:\s+[\w-]{1,20}){0,3}\s*[:=]\s*)%s%s" % (_DONE, _SECRETISH)),
     r"\1" + MASK),
    # DEEPSEEK_API_KEY=..., "password": "...", token: '...'. Значение обязано
    # выглядеть как секрет (см. _SECRETISH) — иначе под правило попадает
    # `web_token = secrets.token_urlsafe(15)` из наших же исходников
    ("assignment",
     re.compile(r"(?i)\b(%s[\"']?\s*[:=]\s*)([\"']?)%s%s\2"
                % (_ASSIGN_NAMES, _DONE, _SECRETISH)),
     r"\1\2" + MASK + r"\2"),
)

#: известные живые значения: их вырезаем точным совпадением
_KNOWN = []
#: короче этого значение секретом не считаем — «1» вырезать себе дороже
MIN_KNOWN = 8


#: имя переменной окружения, под которым обычно лежит секрет
_SECRET_NAME = re.compile(r"(?i)(?:%s)$" % _URL_NAMES)


def secret_name(name) -> bool:
    """Похоже ли имя на «здесь лежит секрет»: TELEGRAM_BOT_TOKEN, ND_WEB_TOKEN."""
    return bool(_SECRET_NAME.search(str(name or "")))


def remember(*values) -> None:
    """Запомнить живой секрет: токен бота, ключ модели, пароль страницы.

    Вызывается там, где секреты читаются (`config.load_env`, `web.token`).
    Дальше это значение не покажется ни в логе, ни в запросе к модели, в
    каком бы виде туда ни попало.
    """
    for value in values:
        value = str(value or "").strip()
        if len(value) >= MIN_KNOWN and value not in _KNOWN:
            _KNOWN.append(value)
    _KNOWN.sort(key=len, reverse=True)      # длинное режем раньше вложенного


def forget() -> None:
    """Забыть все запомненные значения (нужно тестам)."""
    del _KNOWN[:]


def scrub(text) -> str:
    """Текст без секретов. Не текст (None, число) — возвращаем как есть."""
    if not isinstance(text, str) or not text:
        return text
    for value in _KNOWN:
        if value in text:
            text = text.replace(value, MASK)
    for _name, pattern, repl in RULES:
        text = pattern.sub(repl, text)
    return text


def scan(text) -> list:
    """Имена сработавших правил — без самих находок. Для отчётов и логов."""
    if not isinstance(text, str) or not text:
        return []
    found = []
    if any(value in text for value in _KNOWN):
        found.append("известный ключ")
    for name, pattern, _repl in RULES:
        if pattern.search(text):
            found.append(name)
    return found


def scrub_json(data):
    """То же самое по вложенной структуре: словари, списки, строки."""
    if isinstance(data, dict):
        return {key: scrub_json(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [scrub_json(value) for value in data]
    return scrub(data)


def safe_url(url) -> str:
    """Ссылка, которую не стыдно напечатать: без логина, пароля и ключей."""
    return scrub(str(url or ""))


def clean_messages(payload: dict) -> list:
    """Чистит сообщения запроса к модели ПРЯМО В `payload`. Возвращает находки.

    Работает по факту наличия поля `content`, а не по нашим промптам: добавится
    в запрос новая роль или сменится провайдер — чистка всё равно случится.
    """
    found = []
    for message in payload.get("messages") or ():
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        hits = scan(content)
        if hits:
            found.extend(hits)
            message["content"] = scrub(content)
    return sorted(set(found))


class SecretFilter(logging.Filter):
    """Фильтр логов: сообщение и аргументы проходят через `scrub`.

    Вешается на обработчики в `config.setup_logging`, поэтому чистятся и
    чужие логгеры тоже. Сам фильтр не имеет права упасть: сломанный лог
    хуже, чем лог с секретом, — поэтому любая неожиданность просто
    пропускает запись дальше.
    """

    def filter(self, record) -> bool:        # noqa: A003 — имя от базы
        try:
            if isinstance(record.msg, str):
                record.msg = scrub(record.msg)
            if isinstance(record.args, dict):
                record.args = scrub_json(record.args)
            elif record.args:
                record.args = tuple(scrub(a) if isinstance(a, str) else a
                                    for a in record.args)
        except Exception:                    # noqa: BLE001
            pass
        return True


# --------------------------------------------------------------- командная строка
def check_lines(lines) -> list:
    """[(номер строки, [правила])] — что в тексте выглядит как секрет.

    Строку с пометкой ALLOW пропускаем: в документации и тестах игрушечные
    ключи нужны, и падать на них проверке незачем.
    """
    out = []
    for number, line in enumerate(lines, 1):
        if ALLOW in line:
            continue
        hits = scan(line)
        if hits:
            out.append((number, hits))
    return out


def _read(path) -> str:
    if path in ("-", ""):
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def main(argv=None) -> int:
    """`python3 -m newsdigest.redact [--check] [файл...]`.

    Без `--check` печатает вычищенный текст (им пользуется `digest.py scrub`),
    с `--check` — молча выходит с кодом 1, если в файлах есть похожее на
    секрет. Второе стоит в CI: пусть находит до того, как это уедет в PR.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    paths = [a for a in argv if not a.startswith("-")] or ["-"]
    if not check:
        for path in paths:
            sys.stdout.write(scrub(_read(path)))
        return 0
    bad = 0
    for path in paths:
        for number, hits in check_lines(_read(path).splitlines()):
            print("%s:%d: похоже на секрет (%s)" % (path, number,
                                                    ", ".join(hits)))
            bad += 1
    if bad:
        print("\nНайдено подозрительных строк: %d. Уберите значение или "
              "пометьте строку «%s», если это заведомо игрушечный пример."
              % (bad, ALLOW))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
