# 📰 News Digest Bot

Собирает новости из RSS, сортирует по 18 разделам с помощью LLM (DeepSeek) и
дважды в день присылает выпуск в Telegram; срочное — вне расписания. Читалка с
поиском, оценками и закладками — веб-страница `http://<vps>:8080/`.
Python 3.8+, без зависимостей, данные — в `~/.newsdigest/`.

## 🚀 Быстрый старт

```bash
python3 digest.py setup           # мастер: токен бота, chat_id, ключ DeepSeek, время
python3 digest.py doctor          # проверить подключения
python3 digest.py run --dry-run   # собрать выпуск и показать в терминале
python3 digest.py daemon          # рабочий режим: сбор, рассылка, страница
```

На VPS — как сервис systemd, с автообновлением после мержа в `main`:

```bash
python3 digest.py service > newsdigest.service && sudo cp newsdigest.service /etc/systemd/system/
python3 digest.py autoupdate      # юнит + таймер: git pull и перезапуск
sudo cp ~/.newsdigest/newsdigest-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now newsdigest newsdigest-update.timer
```

## 📋 Команды

`python3 digest.py <команда>`:

| Команда | Что делает |
|---------|-----------|
| `setup` / `chatid` | Первичная настройка / заново определить chat_id |
| `doctor` | Диагностика: Telegram, DeepSeek, OpenRouter, база, часовой пояс |
| `run [--dry-run] [--section X]` | Собрать и отправить выпуск сейчас |
| `collect` | Только собрать новости |
| `daemon` / `web` | Рабочий режим / только страница |
| `status` / `report` | Состояние и расход / итоги за период |
| `feeds [--candidates\|--broken\|--archive]` | Проверить источники, кандидатов, переезды, архив |
| `topics` | Разделы и их источники |
| `scrub [файл]` | Вычистить секреты из лога перед публикацией |
| `service` / `autoupdate` / `site` | systemd-юнит / таймер автообновления / конфиг nginx с https |

Инструменты разработки:

| Команда | Что делает |
|---------|-----------|
| `python3 tools/prcheck.py` | Готовность к мержу: тесты, секреты, интеграция → YES/NO |
| `python3 tools/feedcheck.py` | Отвечают ли ленты, записанные в коде (идёт в CI) |
| `python3 tools/audit.py --days 14` | Разбор показа: что дошло до читателя и почему |
| `python3 -m unittest discover -s tests` | Тесты (сеть не трогают) |

## ⚙️ Конфиги

| Где | Что |
|-----|-----|
| `~/.newsdigest/env` | Ключи и настройки `ND_*` (права 600); после правки — перезапуск демона |
| `~/.newsdigest/profiles.json` | Свои разделы и источники поверх встроенных |
| `newsdigest/config.py` | Значения по умолчанию, список переменных `ND_*`, веса ранжирования |
| `newsdigest/profiles.py`, `candidates.py` | Встроенные ленты и кандидаты |
| `~/.newsdigest/digest.db`, `digest.log` | База SQLite и лог |

Главные переменные: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DEEPSEEK_API_KEY`,
`ND_SECTIONS`, `ND_SEND_AT`, `ND_PER_DAY`, `ND_TZ`, `ND_WEB_PORT`, `ND_WEB_TOKEN`
(пароль страницы). Полный список — `ENV_MAP` в `newsdigest/config.py`.

## 📊 Структура

```
digest.py                  ← точка входа
newsdigest/
├── cli.py                 ← команды
├── config.py, settings.py ← настройки
├── daemon.py, bot.py      ← расписание, long-poll, кнопки
├── pipeline.py            ← конвейер выпуска
├── sources.py, net.py, feedparse.py ← сбор лент
├── profiles.py, candidates.py, trust.py ← источники и доверие к ним
├── classify.py, rank.py, dedup.py, threads.py ← раздел, отбор, повторы, сюжеты
├── llm.py, translate.py   ← запросы к модели, карточки, перевод
├── safety.py, factcheck.py ← проверка ссылок и заявлений
├── breaking.py            ← срочное
├── render.py, issueview.py, telegram.py ← выпуск в Telegram
├── web.py, newsfeed.py, webpage.py, rss.py ← страница и RSS
├── storage.py, subscribers.py ← SQLite, подписчики
└── redact.py              ← вырезание секретов из логов и промптов
tools/                     ← prcheck, feedcheck, audit, itest
deploy/autoupdate.sh       ← автообновление на сервере
tests/                     ← тесты
```

## 🔒 Секреты: что и куда не уходит

Ключи живут только в `~/.newsdigest/env`. Всё, что уходит к модели и в лог,
проходит через `redact.py`; логи перед вставкой в issue — через `digest.py scrub`.

---

**Python:** 3.8+ | **Лицензия:** MIT
