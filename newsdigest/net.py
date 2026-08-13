# -*- coding: utf-8 -*-
"""HTTP-слой: один общий opener, gzip, 308-редиректы, без исключений наружу."""
from __future__ import annotations

import gzip
import json
import ssl
import urllib.error
import urllib.request

from .config import CFG


class _Redirect308(urllib.request.HTTPRedirectHandler):
    """Python до 3.11 не умеет следовать за 308 Permanent Redirect, а на нём
    сидит часть фидов (venturebeat, deeplearning.ai). Учим вручную."""
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_301

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(
            req, fp, 301 if code == 308 else code, msg, headers, newurl)


_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(
            _Redirect308,
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    return _OPENER


def _open(url: str, data=None, headers=None, timeout=30, method=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", CFG["user_agent"])
    req.add_header("Accept", "*/*")
    for key, val in (headers or {}).items():
        req.add_header(key, val)
    with _opener().open(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return 200, raw


def http_get(url: str, timeout=None, ua=None):
    """Возвращает (status, bytes). Исключения сети наружу не выпускает."""
    headers = {"User-Agent": ua} if ua else None
    try:
        return _open(url, headers=headers, timeout=timeout or CFG["http_timeout"])
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return exc.code, body


def post_json(url: str, payload: dict, headers=None, timeout=60):
    """POST с JSON. Возвращает (status, dict|None, текст ошибки)."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdr = {"Content-Type": "application/json"}
    hdr.update(headers or {})
    try:
        status, raw = _open(url, data=body, headers=hdr, timeout=timeout, method="POST")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001
            raw = b""
    except Exception as exc:  # noqa: BLE001 — таймауты, DNS, TLS
        return 0, None, "%s: %s" % (type(exc).__name__, exc)
    try:
        return status, json.loads(raw.decode("utf-8", "replace")), ""
    except (ValueError, UnicodeDecodeError):
        return status, None, raw[:400].decode("utf-8", "replace")
