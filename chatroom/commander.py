#!/usr/bin/env python3
"""Shared project commander state for the three-brother team."""

import json
import time
from pathlib import Path

import chatutil


DEFAULT_COMMANDER = "Codex"
ALL_AGENTS = ("Codex", "ZCode", "OpenCode", "Qoder")
ALIASES = {
    "codex": "Codex", "大哥": "Codex",
    "zcode": "ZCode", "二哥": "ZCode",
    "opencode": "OpenCode", "三哥": "OpenCode", "三弟": "OpenCode",
    "qoder": "Qoder", "四哥": "Qoder", "四弟": "Qoder",
}


def normalize_commander(value):
    return ALIASES.get(str(value or "").strip().lower())


def commander_path(project):
    paths = chatutil.project_paths(project)
    stem = Path(paths["opencode_seen"]).with_name("commander.json")
    if paths["project"] != chatutil.DEFAULT_PROJECT:
        stem = Path(paths["opencode_seen"]).with_name(
            "commander.%s.json" % paths["project"])
    return stem


def load_commander(project):
    try:
        value = normalize_commander(json.loads(commander_path(project).read_text(
            encoding="utf-8")).get("name"))
        return value or DEFAULT_COMMANDER
    except Exception:
        return DEFAULT_COMMANDER


def save_commander(project, value):
    name = normalize_commander(value)
    if not name:
        raise ValueError("unknown commander")
    path = commander_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    return name
