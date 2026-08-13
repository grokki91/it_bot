# -*- coding: utf-8 -*-
"""Точка входа для `python3 -m newsdigest`."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
