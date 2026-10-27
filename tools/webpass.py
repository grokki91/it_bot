# -*- coding: utf-8 -*-
"""Пароль владельца страницы: показать его, не открывая код и лог.

    python3 tools/webpass.py                  # ~/.newsdigest/env (или ND_HOME)
    python3 tools/webpass.py --home /путь     # если демон работает из другого каталога

Пароль создаётся сам при первом запуске страницы (`newsdigest/web.py`, token())
и лежит в env-файле строкой ND_WEB_TOKEN. В лог он не пишется — искать его в
digest.log бесполезно. Скрипт только читает файл: ни сети, ни записи, демон
останавливать не нужно.

Читает так же, как `newsdigest/config.py` (load_env): первое вхождение ключа
побеждает, кавычки вокруг значения снимаются.
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Optional

KEY = "ND_WEB_TOKEN"


def env_file(home: Optional[str] = None) -> Path:
    base = home or os.environ.get("ND_HOME") or Path.home() / ".newsdigest"
    return Path(base) / "env"


def read_token(path: Path) -> Optional[str]:
    """Значение ND_WEB_TOKEN из env-файла или None, если строки нет или она пустая."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == KEY:
            return value.strip().strip('"').strip("'") or None
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Показать пароль страницы newsdigest")
    ap.add_argument("--home", help="каталог данных; по умолчанию ND_HOME или ~/.newsdigest")
    args = ap.parse_args(argv)

    path = env_file(args.home)
    if not path.exists():
        print("Файла нет: %s. Пароль появляется при первом запуске страницы — "
              "запустите демон (`python3 digest.py daemon`) или укажите --home."
              % path, file=sys.stderr)
        return 1
    token = read_token(path)
    if not token:
        print("В %s нет %s. Запустите демон со страницей (ND_WEB=1 по умолчанию) — "
              "пароль создастся и запишется туда сам." % (path, KEY), file=sys.stderr)
        return 1

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        print("Внимание: файл читается не только владельцем (права %o). "
              "Исправить: chmod 600 %s" % (mode, path), file=sys.stderr)
    print("Файл   :", path)
    print("Пароль :", token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
