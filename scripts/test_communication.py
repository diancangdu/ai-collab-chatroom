#!/usr/bin/env python3
"""Smoke-test the local chatroom HTTP surface and shell assets."""

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chatroom"))
import auth

BASE = "http://127.0.0.1:8787"
PROJECT = "comm-smoke-%s" % time.strftime("%Y%m%d-%H%M%S")


def request(path, method="GET", data=None, headers=None):
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        BASE + path, data=body, method=method,
        headers={"X-Chatroom-Token": auth.token(), **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        raw = response.read()
        ctype = response.headers.get("Content-Type", "")
        decoded = json.loads(raw.decode("utf-8")) if ctype.startswith("application/json") else raw
        return response.status, ctype, decoded


CHECKS = []


def check(name, condition, detail=""):
    CHECKS.append((name, bool(condition), detail))


def main():
    shells = [
        ("/desktop", "四模型协作"),
        ("/desktop.css", ".desktop"),
        ("/desktop.js", "serviceWorker"),
        ("/manifest.webmanifest", '"start_url": "/desktop"'),
        ("/icon.svg", "<svg"),
        ("/sw.js", 'const CACHE = "chatroom-desktop-v1"'),
    ]
    for path, expected in shells:
        status, ctype, body = request(path)
        text = body.decode("utf-8", errors="ignore")
        check("shell %s" % path, status == 200 and expected in text, ctype)

    apis = [
        ("/api/config", "config"),
        ("/api/roster", "active_agents"),
        ("/api/projects", "projects"),
        ("/api/sessions", "agents"),
        ("/api/service", "state"),
        ("/api/workload?project=" + PROJECT, "state"),
        ("/api/commander?project=" + PROJECT, "commander"),
    ]
    for path, key in apis:
        status, _, body = request(path)
        check("api %s" % path, status == 200 and key in body, str(body)[:120])

    status, _, body = request("/api/health")
    check("public health", status == 200 and body["status"] == "healthy", str(body)[:120])
    try:
        urllib.request.urlopen(BASE + "/api/config", timeout=5)
        check("unauthorized rejected", False, "API accepted request without token")
    except urllib.error.HTTPError as exc:
        check("unauthorized rejected", exc.code == 401, str(exc.code))

    _, _, roster = request("/api/roster")
    status, _, body = request("/api/roster", "POST", {"active_agents": roster["active_agents"]})
    check("roster roundtrip", status == 200 and body["ok"], str(body)[:120])

    _, _, commander = request("/api/commander?project=" + PROJECT)
    status, _, body = request(
        "/api/commander?project=" + PROJECT, "POST", {"commander": commander["commander"]}
    )
    check("commander roundtrip", status == 200 and body["ok"], str(body)[:120])

    status, _, body = request(
        "/api/send?project=" + PROJECT, "POST", {"name": "Codex", "text": "COMM_SMOKE_OK"}
    )
    check("send message", status == 200 and body["ok"] and body["message"]["text"] == "COMM_SMOKE_OK", str(body)[:160])
    message_id = body["message"]["id"]

    status, _, body = request("/api/messages?project=%s&since=%d" % (PROJECT, message_id - 1))
    check("incremental read", status == 200 and any(m["id"] == message_id for m in body["messages"]), str(body)[:160])

    status, _, body = request("/api/transcript?project=" + PROJECT)
    check("transcript", status == 200 and b"COMM_SMOKE_OK" in body, "")

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    query = "project=%s&name=Codex&filename=dot.png&text=COMM_SMOKE_IMAGE" % PROJECT
    status, _, body = request(
        "/api/upload?" + query, "POST", png, {"Content-Type": "application/octet-stream"}
    )
    check("image upload", status == 200 and body["ok"] and body["message"]["image"], str(body)[:160])

    status, _, body = request("/api/sessions/refresh", "POST", {})
    check("session refresh", status == 200 and body["ok"], str(body)[:120])

    failed = [item for item in CHECKS if not item[1]]
    for name, passed, detail in CHECKS:
        print("PASS" if passed else "FAIL", name, "" if passed else detail)
    print("SUMMARY %d/%d passed" % (len(CHECKS) - len(failed), len(CHECKS)))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("FAIL setup", repr(exc), file=sys.stderr)
        raise SystemExit(2)
