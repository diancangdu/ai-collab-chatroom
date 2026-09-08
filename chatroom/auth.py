#!/usr/bin/env python3
"""Local token helper shared by the HTTP server and Python bridges."""

import json
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = Path(__file__).resolve().parent / "data" / "auth_token.txt"


def _config_token():
    try:
        value = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return str(value.get("auth_token") or "")
    except Exception:
        return ""


def token():
    value = _config_token()
    if value:
        return value
    try:
        value = TOKEN_PATH.read_text(encoding="ascii").strip()
    except OSError:
        value = ""
    if not value:
        value = secrets.token_urlsafe(32)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(value, encoding="ascii")
    return value


def headers():
    return {"X-Chatroom-Token": token()}
