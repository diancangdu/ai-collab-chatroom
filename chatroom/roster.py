#!/usr/bin/env python3
"""Shared active-membership control for the three brothers."""

import json
import os
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
AUDIT_PATH = Path(__file__).resolve().parent / "data" / "roster.log"
ALL_AGENTS = ("Codex", "ZCode", "OpenCode", "Qoder")


def default_roster():
    return list(ALL_AGENTS)


def normalize_roster(value):
    if isinstance(value, dict):
        raw = [name for name, active in value.items() if active]
    else:
        raw = value if isinstance(value, list) else []
    aliases = {
        "codex": "Codex", "大哥": "Codex",
        "zcode": "ZCode", "二哥": "ZCode",
        "opencode": "OpenCode", "三哥": "OpenCode", "三弟": "OpenCode",
        "qoder": "Qoder", "四哥": "Qoder", "四弟": "Qoder",
    }
    seen = set()
    out = []
    for item in raw:
        name = aliases.get(str(item or "").strip().lower())
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def load_roster():
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_roster()
    if "active_agents" not in config:
        return default_roster()
    return normalize_roster(config.get("active_agents"))


def save_roster(active):
    active = normalize_roster(active)
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        config = {}
    previous = normalize_roster(config.get("active_agents")) or default_roster()
    config["active_agents"] = active
    config["roster_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = CONFIG_PATH.with_name("." + CONFIG_PATH.name + ".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
    joined = sorted(set(active) - set(previous))
    left = sorted(set(previous) - set(active))
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "ts": config["roster_updated_at"],
            "active": active,
            "joined": joined,
            "left": left,
        }, ensure_ascii=False) + "\n")
    return active, joined, left


def is_active(name):
    aliases = {
        "codex": "Codex", "大哥": "Codex",
        "zcode": "ZCode", "二哥": "ZCode",
        "opencode": "OpenCode", "三哥": "OpenCode", "三弟": "OpenCode",
        "qoder": "Qoder", "四哥": "Qoder", "四弟": "Qoder",
    }
    canonical = aliases.get(str(name or "").strip().lower())
    return canonical in load_roster()
