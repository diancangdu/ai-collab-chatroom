#!/usr/bin/env python3
"""Live registry for each brother's current app session."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


RUNTIME = Path(__file__).resolve().parent
DATA_DIR = RUNTIME / "data"
REGISTRY_PATH = DATA_DIR / "session_registry.json"
MARKER_DIR = DATA_DIR / "session_markers"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
CODEX_SESSIONS = CODEX_HOME / "sessions"
CODEX_SESSION_INDEX = CODEX_HOME / "session_index.jsonl"
ZCODE_DB = Path(os.environ.get("ZCODE_DB", Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"))
SERVER_KEY = RUNTIME / "server_key.py"


def utc_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def atomic_write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_registry():
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def marker_path(agent):
    return MARKER_DIR / (agent.lower() + "_session.txt")


def set_markers(agents):
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    for agent, item in agents.items():
        sid = str(item.get("session_id") or "")
        if sid:
            marker_path(agent).write_text(sid, encoding="ascii")


def title_from_codex_index(session_id):
    try:
        with CODEX_SESSION_INDEX.open("r", encoding="utf-8", errors="ignore") as handle:
            last = None
            for line in handle:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if item.get("id") == session_id:
                    last = item
            return (last or {}).get("thread_name")
    except Exception:
        return None


def detect_codex():
    files = list(CODEX_SESSIONS.glob("*/**/rollout-*.jsonl"))
    if not files:
        raise RuntimeError("no Codex rollout files")
    rollout = max(files, key=lambda path: path.stat().st_mtime)
    meta = {}
    context = {}
    with rollout.open("r", encoding="utf-8", errors="ignore") as handle:
        for line_number, line in enumerate(handle):
            if line_number >= 2:
                break
            try:
                item = json.loads(line)
            except Exception:
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if item.get("type") == "session_meta":
                meta = payload
            elif item.get("type") == "turn_context":
                context = payload
    session_id = meta.get("session_id") or meta.get("id")
    if not session_id:
        raise RuntimeError("Codex session_meta missing id")
    st = rollout.stat()
    return {
        "session_id": str(session_id),
        "title": title_from_codex_index(str(session_id)),
        "cwd": context.get("cwd") or meta.get("cwd"),
        "workspace_roots": context.get("workspace_roots") or [],
        "model": context.get("model") or meta.get("model"),
        "originator": meta.get("originator"),
        "cli_version": meta.get("cli_version"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
        "source": "codex_rollout",
    }


def opencode_credentials():
    python = sys.executable
    result = subprocess.run(
        [python, str(SERVER_KEY), "--json"],
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=0x08000000,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "server_key failed").strip()[:300])
    key = json.loads(result.stdout)
    import base64
    auth = base64.b64encode(
        ("%s:%s" % (key["username"], key["password"])).encode("utf-8")
    ).decode("ascii")
    return "http://127.0.0.1:" + str(key["port"]) + "/api", {
        "Authorization": "Basic " + auth,
        "Content-Type": "application/json; charset=utf-8",
    }


def detect_opencode():
    base, headers = opencode_credentials()
    request = urllib.request.Request(base + "/session", headers=headers)
    with urllib.request.urlopen(request, timeout=4) as response:
        body = json.loads(response.read().decode("utf-8"))
    sessions = body.get("data") if isinstance(body, dict) else body
    sessions = sessions.get("sessions") if isinstance(sessions, dict) else sessions
    if not isinstance(sessions, list) or not sessions:
        raise RuntimeError("no OpenCode sessions")
    updated = sessions[0]
    for session in sessions:
        if (session.get("time", {}).get("updated") or 0) > (
            updated.get("time", {}).get("updated") or 0
        ):
            updated = session
    location = updated.get("location") if isinstance(updated.get("location"), dict) else {}
    directory = (
        updated.get("directory")
        or updated.get("path")
        or location.get("directory")
        or location.get("subpath")
    )
    model = updated.get("model") if isinstance(updated.get("model"), dict) else {}
    return {
        "session_id": str(updated.get("id") or ""),
        "title": updated.get("title"),
        "cwd": directory,
        "model": model.get("providerID") + "/" + model.get("id")
        if model.get("providerID") and model.get("id")
        else None,
        "updated_at": updated.get("time", {}).get("updated"),
        "source": "opencode_api",
    }


def detect_zcode():
    con = sqlite3.connect(str(ZCODE_DB), timeout=5)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """
            SELECT id, directory, path, title, time_created, time_updated
            FROM session
            WHERE time_archived IS NULL
            ORDER BY time_updated DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            raise RuntimeError("no active ZCode session")
        input_row = con.execute(
            """
            SELECT status, time_updated
            FROM session_input
            WHERE session_id = ?
            ORDER BY time_updated DESC
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        return {
            "session_id": str(row["id"]),
            "title": row["title"],
            "cwd": row["directory"] or row["path"],
            "input_status": input_row["status"] if input_row else None,
            "updated_at": row["time_updated"],
            "source": "zcode_sqlite",
        }
    finally:
        con.close()


def record_once():
    detectors = {
        "Codex": detect_codex,
        "OpenCode": detect_opencode,
        "ZCode": detect_zcode,
    }
    registry = load_registry()
    agents = registry.get("agents", {}) if isinstance(registry, dict) else {}
    for agent, detector in detectors.items():
        try:
            agents[agent] = detector()
            agents[agent]["status"] = "ok"
            agents[agent]["error"] = None
        except Exception as exc:
            previous = agents.get(agent) if isinstance(agents.get(agent), dict) else {}
            agents[agent] = {
                **previous,
                "status": "error",
                "error": str(exc)[:500],
            }
    registry = {
        "updated_at": utc_ts(),
        "interval_seconds": 2,
        "agents": agents,
    }
    atomic_write_json(REGISTRY_PATH, registry)
    set_markers(agents)
    return registry


def watch(interval=2.0):
    while True:
        try:
            record_once()
        except Exception:
            pass
        time.sleep(interval)


def start_background_watcher(interval=2.0):
    thread = threading.Thread(target=watch, args=(interval,), daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    print(json.dumps(record_once(), ensure_ascii=False))
