#!/usr/bin/env python3
"""Fast direct bridge for OpenCode chatroom mentions.

The watcher does not rely on OpenCode executing a chatroom command.  It
submits the mention to OpenCode's local API, waits for the assistant text,
then posts that text back to the chatroom.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil
import rate_limit
import roster
import server_locator


POLL_SECONDS = 0.1
REPLY_TIMEOUT = 120.0
PRIMARY_WAIT_SECONDS = 90.0
OPENCODE_MIN_INTERVAL_SECONDS = 300
PING_RE = re.compile(r"(?:^|[^A-Za-z0-9])@(?:三哥|三弟|opencode)\b", re.IGNORECASE)
INSTANT_RE = re.compile(r"(在吗|在不在|在线吗|桥测)")
SEEN_NAME = "third_direct_seen.txt"
RUNTIME = Path(__file__).resolve().parent
LOG_PATH = RUNTIME / "data" / "third_watch.log"
BRIDGE_SESSION_FILE = RUNTIME / "data" / "third_bridge_session.txt"
FALLBACK_MODEL = {"providerID": "opencode", "id": "ling-3.0-flash-fin-free"}
PYTHONW = str(Path(sys.executable).with_name("pythonw.exe"))
if not Path(PYTHONW).exists():
    PYTHONW = sys.executable


def log(project, **data):
    item = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "project": project, **data}
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def send(project, text):
    subprocess.run(
        [PYTHONW, str(RUNTIME / "chatroom.py"), "send",
         "--name", "OpenCode", "--project", project, "--text", text],
        capture_output=True, text=True, timeout=10, creationflags=0x08000000,
    )


def recent_duplicate(project, reply):
    """OpenCode's own bridge may post the same assistant turn a moment earlier."""
    paths = chatutil.project_paths(project)
    try:
        messages, _ = chatutil.tail_json_lines(paths["messages"], 0)
        wanted = re.sub(r"^@你\s*", "", str(reply).strip())
        now = time.time()
        for msg in reversed(messages[-12:]):
            if msg.get("name") != "OpenCode":
                continue
            try:
                sent_at = time.mktime(time.strptime(str(msg.get("ts", "")), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if now - sent_at <= 8:
                text = re.sub(r"^@你\s*", "", str(msg.get("text", "")).strip())
                if text == wanted:
                    return True
    except Exception:
        pass
    return False


def send_once(project, reply):
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if recent_duplicate(project, reply):
            return False
        time.sleep(0.2)
    if recent_duplicate(project, reply):
        return False
    send(project, "@你 " + reply)
    return True


def credentials():
    result = subprocess.run(
        [PYTHONW, str(RUNTIME / "server_key.py"), "--json"],
        capture_output=True, text=True, timeout=20, creationflags=0x08000000,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "server_key.py failed").strip()[:500])
    key = json.loads(result.stdout)
    auth = base64.b64encode(
        ("%s:%s" % (key["username"], key["password"])).encode("utf-8")
    ).decode("ascii")
    return key, {"Authorization": "Basic " + auth,
                 "Content-Type": "application/json; charset=utf-8"}


def request_json(url, headers, timeout=5, data=None, method=None):
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status == 204:
            return None
        return json.loads(response.read().decode("utf-8"))


def project_sessions(sessions, project):
    root = str(Path(r"E:/CS2Additions")).lower()
    candidates = []
    for session in sessions if isinstance(sessions, list) else []:
        location = session.get("location") if isinstance(session.get("location"), dict) else {}
        directory = str(session.get("directory") or session.get("path")
                        or location.get("directory") or location.get("subpath") or "").lower()
        if root.rstrip("\\/") in (directory.rstrip("\\/"),) or root in directory:
            candidates.append(session)
    return candidates


def source_session(sessions, project):
    candidates = [
        session for session in project_sessions(sessions, project)
        if str(session.get("title", "")) != "AI Chatroom Third Bridge"
    ] or project_sessions(sessions, project)
    if not candidates:
        raise RuntimeError("no OpenCode session for model sync")
    updated = candidates[0]
    for session in candidates[1:]:
        if (session.get("time", {}).get("updated") or 0) > (updated.get("time", {}).get("updated") or 0):
            updated = session
    return updated


def desired_model(base, headers, project):
    sessions = request_json(base + "/session", headers)
    if isinstance(sessions, dict):
        sessions = sessions.get("data") or sessions.get("sessions") or []
    model = source_session(sessions, project).get("model")
    providers = request_json(base + "/provider", headers)
    if isinstance(providers, dict):
        providers = providers.get("data") or providers.get("providers") or []
    provider_ids = {
        item.get("id")
        for item in providers if isinstance(item, dict)
    }
    if not isinstance(model, dict) or model.get("providerID") not in provider_ids:
        return FALLBACK_MODEL
    return model


def new_bridge_session(base, headers, project):
    model = desired_model(base, headers, project)
    body = json.dumps({
        "title": "AI Chatroom Third Bridge",
        "model": model,
    }, ensure_ascii=False).encode("utf-8")
    session = request_json(
        base + "/session?directory=" + urllib.parse.quote(os.environ.get("AICOLLAB_WORKSPACE", os.getcwd())),
        headers, timeout=10, data=body, method="POST",
    )
    if isinstance(session, dict) and isinstance(session.get("data"), dict):
        session = session["data"]
    sid = session["id"]
    BRIDGE_SESSION_FILE.write_text(sid, encoding="ascii")
    return sid


def target_session(base, headers, project):
    sessions = request_json(base + "/session", headers)
    if isinstance(sessions, dict):
        sessions = sessions.get("data") or sessions.get("sessions") or []
    if not isinstance(sessions, list):
        raise RuntimeError("unexpected session list response")
    source_model = desired_model(base, headers, project)
    if BRIDGE_SESSION_FILE.exists():
        bridge_sid = BRIDGE_SESSION_FILE.read_text(encoding="ascii").strip()
        for session in sessions:
            if session.get("id") == bridge_sid:
                current_model = session.get("model")
                if isinstance(current_model, dict) and current_model.get("providerID") == source_model.get("providerID") and current_model.get("id") == source_model.get("id"):
                    return bridge_sid
                break
    return new_bridge_session(base, headers, project)


def inject_into_opencode_app(text):
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    script = f"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$root = [System.Windows.Automation.AutomationElement]::RootElement
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children,
    (New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, 'OpenCode')))
if (-not $win) {{ throw 'OpenCode window not found' }}
$edit = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, '提示词')))
if (-not $edit) {{ throw 'OpenCode input not found' }}
$edit.SetFocus()
Start-Sleep -Milliseconds 100
$msg = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
$value = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
$value.SetValue($msg)
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('{{ENTER}}')
"""
    run = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=10, creationflags=0x08000000,
    )
    if run.returncode != 0:
        raise RuntimeError((run.stderr or run.stdout).strip()[:500])


def run_cli_prompt(user_text, project):
    """Use OpenCode 2.0 CLI as the primary local bridge."""
    npm_exec = shutil.which("npx.cmd") or shutil.which("npx")
    if not npm_exec:
        raise RuntimeError("OpenCode CLI is not installed")
    prompt = (
        "你是三哥。用户在聊天室呼叫："
        f"{user_text[:500]}。"
        "只回一句中文，不超过 30 字，自然、有判断，不要计划，不要动作。"
    )
    command = [
        npm_exec,
        "--yes",
        "@opencode-ai/cli",
        "run",
        "--format",
        "json",
        "--auto",
        "--model",
        "opencode_01/deepseek-v4-flash",
    ]
    bridge_sid = ""
    if BRIDGE_SESSION_FILE.exists():
        bridge_sid = BRIDGE_SESSION_FILE.read_text(encoding="ascii").strip()
        if bridge_sid.startswith("ses_"):
            command.extend(["--session", bridge_sid])
    command.append(prompt)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=REPLY_TIMEOUT,
        cwd=str(RUNTIME.parent),
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "OpenCode CLI failed").strip()[:500])
    reply = ""
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("type") == "text":
            part = event.get("part") or {}
            reply = str(part.get("text", "")).strip()
        if event.get("sessionID"):
            session_id = str(event["sessionID"])
            if session_id.startswith("ses_") and session_id != bridge_sid:
                BRIDGE_SESSION_FILE.write_text(session_id, encoding="ascii")
    if not reply:
        raise RuntimeError("OpenCode CLI returned no text")
    return reply


def submit_prompt(base, headers, session_id, user_text, project=None):
    if not rate_limit.try_acquire(
        provider="opencode",
        min_interval_seconds=OPENCODE_MIN_INTERVAL_SECONDS,
    ):
        log(project, rate_limit_skipped=True)
        return False
    prompt = (
        "你是三哥。用户在聊天室呼叫："
        f"{user_text[:500]}。"
        "只回一句中文，不超过 30 字，自然、有判断，不要计划，不要动作。"
    )
    body = json.dumps({
        "prompt": {"text": prompt},
        "system": "只输出最终中文回复，禁止工具。",
        "tools": {"*": False},
    }, ensure_ascii=False).encode("utf-8")
    url = base + "/session/" + session_id + "/prompt"
    request_json(url, headers, timeout=8, data=body, method="POST")
    return True


class ProviderError(RuntimeError):
    pass


def wait_for_reply(base, headers, session_id, started_at):
    deadline = time.time() + REPLY_TIMEOUT
    while time.time() < deadline:
        try:
            messages = request_json(
                base + "/session/" + session_id + "/message?limit=5", headers)
            if isinstance(messages, dict):
                messages = messages.get("data") or messages.get("messages") or []
            if not isinstance(messages, list):
                continue
            best = None
            for item in messages:
                info = item if isinstance(item, dict) else {}
                embedded = item.get("info", {}) if isinstance(item.get("info", {}), dict) else {}
                role = info.get("role") or item.get("role") or item.get("type")
                if role != "assistant":
                    continue
                timing = info.get("time", {}) if isinstance(info.get("time", {}), dict) else {}
                item_timing = item.get("time", {}) if isinstance(item.get("time", {}), dict) else {}
                created = timing.get("created") or item_timing.get("created") or 0
                if created / 1000 < started_at - 1:
                    continue
                if item.get("finish") == "error":
                    error = item.get("error") if isinstance(item.get("error"), dict) else {}
                    raise ProviderError(str(error.get("message") or "provider error")[:300])
                text = "".join(
                    part.get("text", "")
                    for part in (item.get("parts") or item.get("content") or [])
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
                if not text and isinstance(item.get("text"), str):
                    text = item["text"].strip()
                if text:
                    best = text
            if best:
                return best
        except ProviderError:
            raise
        except Exception as exc:
            log("poll_error", error=str(exc))
        time.sleep(POLL_SECONDS)
    return ""


def seen_path(project):
    paths = chatutil.project_paths(project)
    if project == chatutil.DEFAULT_PROJECT:
        return Path(paths["opencode_seen"]).with_name(SEEN_NAME)
    return Path(paths["opencode_seen"]).with_name("third_direct.%s.txt" % project)


def load_seen(path):
    try:
        return int(path.read_text(encoding="ascii").strip() or 0)
    except Exception:
        return 0


def save_seen(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="ascii")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("project_pos", nargs="?")
    parser.add_argument("--project", dest="project_flag")
    args = parser.parse_args()
    project = chatutil.normalize_project(args.project_flag or args.project_pos or chatutil.DEFAULT_PROJECT)
    server_locator.ensure_server()
    paths = chatutil.project_paths(project)
    seen = seen_path(project)
    watermark = load_seen(seen)
    messages, watermark = chatutil.tail_json_lines(paths["messages"], watermark)
    if messages:
        watermark = chatutil.tail_json_lines(paths["messages"], 0)[1]
    save_seen(seen, watermark)
    log(project, started=True, watermark=watermark)
    pending_backup = {}

    while True:
        try:
            new_messages, watermark = chatutil.tail_json_lines(paths["messages"], watermark)
            save_seen(seen, watermark)
            for msg in new_messages:
                if str(msg.get("name", "")) == "OpenCode":
                    try:
                        sent_at = time.mktime(time.strptime(str(msg.get("ts", "")), "%Y-%m-%d %H:%M:%S"))
                    except ValueError:
                        sent_at = time.time()
                    oldest = min(
                        pending_backup.items(),
                        key=lambda item: item[1]["ts"],
                        default=None,
                    )
                    if oldest:
                        trigger_id, pending = oldest
                        if not pending["replied"] and sent_at >= pending["ts"] - 1:
                            pending_backup.pop(trigger_id, None)
                            log(project, primary_replied=True, trigger_id=trigger_id, reply_id=msg.get("id"))
                if str(msg.get("name", "")) not in {"你", "Codex", "ZCode"}:
                    continue
                text = str(msg.get("text", "")).strip()
                if not PING_RE.search(text):
                    continue
                if "OpenCode" not in roster.load_roster():
                    continue
                if INSTANT_RE.search(text):
                    instant_reply = "@你 在，三弟直达桥在线。"
                    send(project, instant_reply)
                    log(project, replied=True, instant=True, id=msg.get("id"))
                    continue
                trigger_id = msg.get("id") or "ts_%d" % time.time_ns()
                pending_backup[trigger_id] = {"ts": time.time(), "text": text, "replied": False, "original": msg}
                log(project, primary_wait=True, id=msg.get("id"), wait_seconds=PRIMARY_WAIT_SECONDS)
            now = time.time()
            for trigger_id, pending in list(pending_backup.items()):
                try:
                    reply = run_cli_prompt(pending["text"], project)
                    if reply:
                        send_once(project, reply)
                        pending_backup.pop(trigger_id, None)
                        log(project, cli_replied=True, id=pending["original"].get("id"))
                    else:
                        log(project, cli_replied=False, id=pending["original"].get("id"), timeout=True)
                        pending_backup.pop(trigger_id, None)
                except Exception as exc:
                    log(project, cli_error=str(exc), id=pending["original"].get("id"))
                    pending_backup.pop(trigger_id, None)
        except Exception as exc:
            log(project, loop_error=str(exc))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
