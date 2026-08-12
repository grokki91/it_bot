# 📰 News Digest Bot — Инструкция

Автоматический сбор и рассылка актуальных IT-новостей в Telegram ежедневно.  
Работает на Python 3.8+, без внешних зависимостей, хранит всё в `~/.newsdigest/`.

---

## 🚀 Быстрый старт

### 1. Первичная настройка

```bash
python3 digest.py setup
```

Интерактивный мастер попросит:
- **Токен Telegram-бота** (от [@BotFather](https://t.me/BotFather))
- **Chat ID** (определит автоматически, если вы напишете боту сообщение)
- **API ключ DeepSeek** (platform.deepseek.com)
- **Тему** новостей: `ai` | `crypto` | `cybersec` | `custom`
- **Время отправки** (ваше локальное время, например 09:00)
- **Часовой пояс** (например `Europe/Riga`, `Europe/Moscow`)
- **Кол-во новостей** в выпуске (5–10)

Все данные сохранятся в `~/.newsdigest/env` с правами `600`.

### 2. Проверка подключений

```bash
python3 digest.py doctor
```

Проверяет:
- ✅ Telegram-бот и chat_id
- ✅ DeepSeek API и баланс
- ✅ Базу данных
- ✅ Часовой пояс

### 3. Тестовый прогон

```bash
python3 digest.py run --dry-run
```

Собирает и показывает дайджест в терминале, но **не отправляет** в Telegram.  
Видно примерную стоимость запроса к LLM.

### 4. Запуск демона

```bash
python3 digest.py daemon
```

Фоновый процесс:
- Собирает новости каждые 4 часа (настраивается: `ND_COLLECT_EVERY`)
- Отправляет дайджест ровно в `send_at` (если за день ещё не отправлял)
- Автоматически перезагружается при ошибках

**Как поднять на VPS:**

```bash
# Локально: сгенерировать unit-файл для systemd
python3 digest.py service > newsdigest.service

# На VPS (с sudo):
sudo cp newsdigest.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now newsdigest

# Проверить статус:
systemctl status newsdigest
```

Или проще (без root):
```bash
nohup python3 digest.py daemon --log-file >/dev/null 2>&1 &
```

---

## 📋 Все команды

| Команда | Что делает |
|---------|-----------|
| `setup` | Первичная настройка (токены, тема, время) |
| `chatid` | Переопределить chat_id без повторной настройки |
| `doctor` | Диагностика: проверить все подключения |
| `feeds` | Проверить каждый источник по отдельности |
| `run` | Собрать и отправить дайджест сейчас |
| `run --dry-run` | То же, но показать в терминале, не отправлять |
| `run --no-collect` | Отправить из уже собранных новостей (без повторного сбора) |
| `collect` | Только собрать новости, не ранжировать и не отправлять |
| `daemon` | Фоновый режим: сбор по расписанию |
| `status` | Последние прогоны, расход DeepSeek, проблемные источники |
| `service` | Вывести systemd unit-файл для установки |

### Глобальные флаги

```bash
python3 digest.py -v daemon          # Verbose режим (DEBUG логи)
python3 digest.py --log-file daemon  # Писать логи в ~/.newsdigest/digest.log
```

---

## ⚙️ Настройка (файл `~/.newsdigest/env`)

Все переменные также можно задать через переменные окружения:

```bash
export ND_TOPIC=ai                    # Тема: ai | crypto | cybersec | custom
export ND_SEND_AT=09:00               # Время отправки
export ND_TZ=Europe/Moscow            # Часовой пояс (с автоматическим переходом на летнее время)
export ND_COLLECT_EVERY=4             # Сбор каждые N часов
export ND_MIN_ITEMS=5                 # Минимум новостей в выпуске
export ND_MAX_ITEMS=8                 # Максимум новостей в выпуске
export ND_MIN_SCORE=5.5               # Порог важности (1–10)
export ND_LANGUAGE=русский            # Язык дайджеста
export ND_SILENT=1                    # Отправлять без звука (true/false)
export TELEGRAM_BOT_TOKEN=...         # Токен бота
export TELEGRAM_CHAT_ID=...           # Chat ID
export DEEPSEEK_API_KEY=...           # API ключ DeepSeek
```

---

## 🔍 Мониторинг на VPS

### Логи

```bash
# Последние 20 строк логов
tail -20 ~/.newsdigest/digest.log

# Постоянно смотреть новые логи
tail -f ~/.newsdigest/digest.log

# Все ошибки за сегодня
grep ERROR ~/.newsdigest/digest.log
```

### Статус работы

```bash
python3 digest.py status
```

Показывает:
- Последние 12 прогонов с временем и статусом
- Кол-во материалов за сутки
- Расход DeepSeek за неделю (в долларах)
- Проблемные источники (которые упали)
- Размер базы данных

### Проверка демона (systemd)

```bash
# Статус
sudo systemctl status newsdigest

# Логи за последний час
sudo journalctl -u newsdigest -n 30

# Перезагрузить
sudo systemctl restart newsdigest

# Остановить
sudo systemctl stop newsdigest

# Удалить из автозагрузки
sudo systemctl disable newsdigest
```

### Проверка процесса (если запущен без systemd)

```bash
ps aux | grep "digest.py daemon"
```

---

## 🧩 Расширение и кастомизация

### Замена LLM (например, на OpenAI, Claude API и т.д.)

1. Отредактируйте раздел **DeepSeek** в `digest.py` (~1500 строка):

```python
# Текущее:
"llm_base": "https://api.deepseek.com",
"model_rank": "deepseek-v4-flash",

# Замените на:
"llm_base": "https://api.openai.com/v1",
"model_rank": "gpt-4-mini",
```

2. Переименуйте функцию `llm_json()` или создайте обёртку:

```python
def llm_json(system, user, model, max_tokens=3000):
    # Замените логику на вызов нужного API
    # Ключ из переменной окружения: os.environ.get("OPENAI_API_KEY")
    ...
```

3. Обновите переменные окружения:
```bash
export OPENAI_API_KEY=...
python3 digest.py setup
```

### Добавление новой темы

1. В секции `PROFILES` (`digest.py`, ~200 строка) скопируйте блок:

```python
"my_tech": {
    "persona": "разработчик, интересуют фреймворки, DevOps, инструменты...",
    "keywords": ["docker", "kubernetes", "rust", "go"],
    "feeds": [
        ("source_name", "https://example.com/feed.xml", 1, "labs"),
        ("another",     "https://another.com/rss",     2, "media"),
        # tier: 1=первоисточник, 2=СМИ, 3=агрегатор
        # category: labs, research, media, community, opensource, etc.
    ],
}
```

2. Запустите:
```bash
export ND_TOPIC=my_tech
python3 digest.py run --dry-run
```

### Изменение веса критериев ранжирования

В `WEIGHTS` (~150 строка):

```python
WEIGHTS = {
    "source_tier":   0.30,   # Первоисточник vs пересказ (↑ = доверие источникам)
    "corroboration": 0.25,   # Сколько сайтов пишут об этом (↑ = ищем консенсус)
    "social":        0.20,   # Баллы Hacker News (↑ = сообщество важнее)
    "freshness":     0.25,   # Свежесть новости (↑ = только горячее)
}
```

### Чистка базы и логов

```bash
# Удалить материалы старше 10 дней
sqlite3 ~/.newsdigest/digest.db "DELETE FROM items WHERE fetched_at < datetime('now', '-10 days');"

# Очистить всю базу (⚠️ потеряются история и дедупликация!)
rm ~/.newsdigest/digest.db

# Очистить логи
rm ~/.newsdigest/digest.log*
```

---

## 🛠️ Troubleshooting

### Бот не отправляет новости

1. Проверьте диагностику:
   ```bash
   python3 digest.py doctor
   ```

2. Проверьте логи:
   ```bash
   tail -50 ~/.newsdigest/digest.log | grep -i error
   ```

3. Проверьте, не отключены ли источники:
   ```bash
   python3 digest.py status  # Смотрите "Проблемные источники"
   ```

### Неправильное время отправки

- Проверьте часовой пояс: `python3 digest.py doctor`
- Пересохраните `ND_TZ`: `python3 digest.py setup`
- Проверьте время на VPS: `timedatectl` (или `date`)

### Высокие расходы на DeepSeek

```bash
python3 digest.py status  # Видно расход за неделю
```

Чтобы снизить:
- Уменьшите `ND_MAX_ITEMS` (меньше новостей = меньше токенов)
- Используйте `deepseek-v4-flash` вместо `deepseek-v4-pro`
- Снизьте `llm_candidates` в `CFG` (~1100 строка)

### Исчезли старые новости

Нормально — удаляются через 10 дней (настраивается `keep_items_days` в `CFG`).

---

## 📊 Архитектура (для разработчиков)

```
digest.py
├── CFG & PROFILES        ← Основные настройки
├── load_env()            ← Читает ~/.newsdigest/env
├── collect()             ← Сбор новостей из RSS
│   ├── fetch_source()    ← Параллельная загрузка фидов
│   └── fetch_hackernews()← Добавляет топ HN
├── build_and_send()      ← Главный конвейер
│   ├── cluster()         ← Группирует дубли (один event = один кластер)
│   ├── prescore()        ← Детерминированный скоринг
│   ├── rank_clusters()   ← LLM ранжирует кандидатов
│   ├── summarize()       ← LLM пишет саммари
│   ├── select()          ← Выбирает в выпуск с лимитами
│   └── tg_send()         ← Отправляет в Telegram
└── daemon()              ← Основной цикл (расписание)
```

**Данные:**
- `~/.newsdigest/digest.db` — SQLite база (материалы, история, здоровье источников, логи прогонов)
- `~/.newsdigest/digest.log` — Логи (ротация: 3 файла по 2 МБ)
- `~/.newsdigest/env` — Секреты и настройки (права 600)

---

## 💡 Советы

1. **Для начала используйте `--dry-run`:** смотрите какой выпуск будет, без отправки.

2. **Логируйте всё:** `python3 digest.py -v --log-file daemon` даст максимум информации.

3. **Мониторьте баланс DeepSeek:** он может закончиться. Проверяйте через `doctor`.

4. **Резервируйте базу:** `cron` копирует `~/.newsdigest/digest.db` раз в неделю.

5. **Один бот = один демон:** если нужна другая тема, создайте отдельный скрипт или используйте несколько chat_id в одном боте.

---

## 📞 Частые вопросы

**Q: Можно ли несколько чатов?**  
A: Отредактируйте `tg_send()` или запустите несколько независимых процессов с разными `env` файлами.

**Q: Как добавить новый источник?**  
A: В `PROFILES[вашатема]["feeds"]` добавьте кортеж `("id", "url_фида", tier, "category")` и перезагрузитесь.

**Q: Что если источник упал?**  
A: Система автоматически отключит его на 24 часа после 5 сбоев подряд. Видно в `status`.

**Q: Как экспортировать новости?**  
A: Напрямую из базы: `sqlite3 ~/.newsdigest/digest.db "SELECT * FROM items WHERE fetched_at > '2024-01-01';"` → CSV или JSON.

---

**Версия:** 2.0 | **Python:** 3.8+ | **Лицензия:** MIT