#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
digest.py — ежедневный дайджест новостей в Telegram. LLM: DeepSeek.

Это точка входа. Сам код живёт в пакете newsdigest/ рядом с этим файлом:
запускать по-прежнему нужно `python3 digest.py <команда>`, ничего в systemd
и в привычных командах менять не надо.

Принципы:
  * ТОЛЬКО стандартная библиотека Python 3.8+ — pip ставить не нужно вообще,
    значит нечего сломать в системном Python вашего VPS;
  * НИКОГДА не требует root, ничего не пишет вне своего каталога,
    не трогает apt/docker/nginx/postgres/VPN;
  * все данные в одном месте: ~/.newsdigest (база, настройки, логи);
  * один процесс-демон сам знает, когда собирать, когда отправлять
    и как отвечать на команды в чате.

Команды:
  python3 digest.py setup          мастер настройки (токены, чат, тема, время)
  python3 digest.py doctor         проверка Telegram / DeepSeek / базы / источников
  python3 digest.py run --dry-run  собрать и показать дайджест в терминале
  python3 digest.py run            собрать и отправить прямо сейчас
  python3 digest.py daemon         фоновый режим: расписание + ответы на команды
  python3 digest.py status         прогоны, расход, здоровье источников
  python3 digest.py feeds          проверить все источники по одному
  python3 digest.py service        напечатать unit-файл systemd (по желанию)
  python3 digest.py autoupdate     таймер: сам git pull и перезапуск демона
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from newsdigest.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
