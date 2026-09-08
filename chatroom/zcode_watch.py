#!/usr/bin/env python3
"""Lightweight ZCode bridge: chatroom -> Z.ai API -> chatroom + ZCode desktop.

Does NOT use ZCode agent/automation. Calls the Anthropic-compatible API
directly for a short reply, then injects it into ZCode's message DB so
it appears in the desktop app conversation.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import asyncio
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil
import roster
import server_locator

POLL_SECONDS = 0.5
REPLY_TIMEOUT = 20.0
PING_RE = re.compile(r"(?:^|\s)@(?:二哥|zcode)\b", re.IGNORECASE)
INSTANT_RE = re.compile(r"(在吗|在不在|在线吗|桥测)")

ZCODE_CONFIG = Path(os.environ.get("ZCODE_CONFIG", Path.home() / ".zcode" / "v2" / "config.json"))
ZCODE_DB = Path(os.environ.get("ZCODE_DB", Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"))
SESS = ""  # dynamically resolved
RUNTIME = Path(__file__).resolve().parent
LOG_PATH = RUNTIME / "data" / "zcode_watch.log"
SEEN_FILE = RUNTIME / "data" / "zcode_direct_seen.txt"
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
         "--name", "ZCode", "--project", project, "--text", text],
        capture_output=True, text=True, timeout=10, creationflags=0x08000000,
    )


CDP_PORT = 9222

def resolve_session():
    con = sqlite3.connect(str(ZCODE_DB), timeout=10)
    try:
        row = con.execute(
            """
            SELECT input.session_id
            FROM session_input AS input
            JOIN session AS app_session ON app_session.id = input.session_id
            WHERE app_session.time_archived IS NULL
            ORDER BY input.time_updated DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            row = con.execute(
                "SELECT id FROM session WHERE time_archived IS NULL ORDER BY time_updated DESC LIMIT 1"
            ).fetchone()
        if not row:
            raise RuntimeError("No active ZCode session")
        return str(row[0])
    finally:
        con.close()

def inject_session_input(user_text, project):
    prompt = (
        f"聊天室实时桥呼叫（项目 {project}），原文：" + user_text[:500] + "\n"
        "请立即做两件事：\n"
        "1. 在本会话回复一句简短中文。\n"
        "2. 用命令把同一句回复发到聊天室："
        "python chatroom/chatroom.py send "
        f"--name ZCode --project {project} --text \"你的回复\"\n"
        "不要展开分析，不要使用其他工具，只回一句然后执行发送命令。"
    )
    now_ms = int(time.time() * 1000)
    command_id = str(uuid.uuid4())
    queue_id = "queue_" + command_id
    client_id = "client-" + str(uuid.uuid4())
    session_id = resolve_session()
    con = sqlite3.connect(str(ZCODE_DB), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        sequence = int(con.execute(
            "SELECT COALESCE(MAX(admitted_sequence), 0) AS m FROM session_input"
        ).fetchone()["m"]) + 1
        payload = {
            "text": prompt,
            "intent": {
                "sourceCommandId": command_id,
                "queueItemId": queue_id,
                "clientId": client_id,
                "kind": "sendText",
                "admissionSeq": sequence,
                "admittedAt": now_ms,
                "requestedDelivery": "startNow",
                "admittedDelivery": "startNow",
                "attachmentRefs": [],
            },
            "conversationInputIntent": {
                "sourceCommandId": command_id,
                "queueItemId": queue_id,
                "clientId": client_id,
                "kind": "sendText",
                "text": prompt,
                "attachments": [],
                "sourceCommandType": "sendText",
                "delivery": {"requested": "startNow", "admitted": "startNow"},
                "order": {"admissionSeq": sequence},
                "steer": {"state": "notRequested"},
                "dispatch": {"state": "admitted"},
                "admittedAt": now_ms,
            },
            "attachments": [],
            "sourceCommandType": "sendText",
        }
        con.execute(
            "INSERT INTO session_input "
            "(id, session_id, kind, delivery, payload, admitted_sequence, status, time_created, time_updated) "
            "VALUES (?, ?, 'sendText', 'startNow', ?, ?, 'admitted', ?, ?)",
            (queue_id, session_id, json.dumps(payload, ensure_ascii=False), sequence, now_ms, now_ms),
        )
        con.commit()
        return session_id
    finally:
        con.close()

def get_zcode_page_ws():
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2) as resp:
        targets = json.loads(resp.read().decode("utf-8"))
    for target in targets:
        if target.get("type") == "page" and target.get("title") == "ZCode":
            return target["webSocketDebuggerUrl"]
    raise RuntimeError("ZCode CDP page not found")

def inject_via_ui(user_text):
    """Submit through ZCode with trusted CDP input events and a DOM fallback."""
    import websockets
    text = user_text[:500]
    focus_expression = r"""
    (() => {
      const el = [...document.querySelectorAll('[contenteditable="true"]')]
        .find(e => String(e.className || '').includes('min-h-10'));
      if (!el) throw new Error('ZCode input not found');
      el.focus();
      document.execCommand('selectAll', false, null);
      document.execCommand('delete', false, null);
      return { ok: true };
    })()
    """
    state_expression = r"""
    (() => {
      const el = [...document.querySelectorAll('[contenteditable="true"]')]
        .find(e => String(e.className || '').includes('min-h-10'));
      if (!el) return { ok: false, error: 'ZCode input not found' };
      const visible = b => {
        const r = b.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && b.offsetParent !== null;
      };
      const buttons = [...document.querySelectorAll('button')]
        .filter(b => visible(b) && b.getAttribute('type') === 'submit');
      const button = buttons[buttons.length - 1];
      return {
        ok: true,
        textLength: (el.innerText || '').trim().length,
        buttonFound: Boolean(button),
        buttonDisabled: button ? Boolean(button.disabled) : null,
        rect: button ? (() => {
          const r = button.getBoundingClientRect();
          return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        })() : null,
      };
    })()
    """

    async def run_cdp():
        async with websockets.connect(get_zcode_page_ws(), max_size=10 * 1024 * 1024) as ws:
            next_id = 0

            async def call(method, params):
                nonlocal next_id
                next_id += 1
                request_id = next_id
                await ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                    data = json.loads(raw)
                    if data.get("id") != request_id:
                        continue
                    result = data.get("result", {}).get("result", {})
                    if result.get("subtype") == "error":
                        raise RuntimeError(str(result.get("description", "CDP evaluate failed"))[:500])
                    return result.get("value")

            await call("Runtime.evaluate", {
                "expression": focus_expression,
                "returnByValue": True,
            })
            await call("Input.insertText", {"text": text})
            await asyncio.sleep(0.18)
            state = await call("Runtime.evaluate", {
                "expression": state_expression,
                "returnByValue": True,
            })
            if not isinstance(state, dict) or not state.get("ok"):
                raise RuntimeError(str(state)[:500])

            if state.get("buttonFound") and not state.get("buttonDisabled"):
                point = state.get("rect") or {}
                x = float(point.get("x", 0))
                y = float(point.get("y", 0))
                for kind in ("mousePressed", "mouseReleased"):
                    await call("Input.dispatchMouseEvent", {
                        "type": kind,
                        "x": x,
                        "y": y,
                        "button": "left",
                        "clickCount": 1,
                    })
                await asyncio.sleep(0.2)
            elif state.get("textLength"):
                for params in (
                    {"type": "rawKeyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                    {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r", "unmodifiedText": "\r"},
                    {"type": "char", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r", "unmodifiedText": "\r"},
                    {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                ):
                    await call("Input.dispatchKeyEvent", params)
                await asyncio.sleep(0.25)

            final = await call("Runtime.evaluate", {
                "expression": state_expression,
                "returnByValue": True,
            })
            if not isinstance(final, dict) or not final.get("ok"):
                raise RuntimeError(str(final)[:500])
            if final.get("textLength"):
                await call("Runtime.evaluate", {
                    "expression": focus_expression,
                    "returnByValue": True,
                })
                raise RuntimeError("ZCode message not submitted; input cleared")
            return {"ok": True}

    return asyncio.run(run_cdp())


def cdp_evaluate(expression, timeout=4.0):
    import websockets

    async def run_cdp():
        async with websockets.connect(
            get_zcode_page_ws(), max_size=10 * 1024 * 1024
        ) as ws:
            request = {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True},
            }
            await ws.send(json.dumps(request))
            raw = await asyncio.wait_for(ws.recv(), timeout)
            data = json.loads(raw)
            result = data.get("result", {}).get("result", {})
            if result.get("subtype") == "error":
                raise RuntimeError(str(result.get("description", "CDP evaluate failed"))[:500])
            return result.get("value")

    return asyncio.run(run_cdp())


def wait_for_ui_reply(user_text, timeout=75.0, marker=""):
    """Capture the assistant turn following the injected user turn in the UI."""
    marker_match = re.search(r"\b\d{14}\b", user_text or "")
    needle = marker or (marker_match.group(0) if marker_match else user_text[:500])
    expression = r"""
    (() => {
      const needle = %s;
      const rows = [...document.querySelectorAll('[data-testid^="v4-row-"]')]
        .map(el => ({
          role: String(el.className || '').includes('group/assistant-row')
            ? 'assistant'
            : String(el.className || '').includes('group/user-row') ? 'user' : '',
          text: (el.innerText || '').trim()
        }));
      let idx = -1;
      for (let i = rows.length - 1; i >= 0; i--) {
        if (rows[i].role === 'user' && rows[i].text.includes(needle)) { idx = i; break; }
      }
      if (idx < 0) return { found: false, text: '' };
      for (let i = idx + 1; i < rows.length; i++) {
        if (rows[i].role === 'assistant') return { found: true, text: rows[i].text };
        if (rows[i].role === 'user' && rows[i].text !== needle) break;
      }
      return { found: true, text: '' };
    })()
    """ % json.dumps(needle, ensure_ascii=False)

    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            value = cdp_evaluate(expression, timeout=4)
            if isinstance(value, dict) and value.get("found"):
                text = str(value.get("text", "")).strip()
                if text and text == last:
                    return text
                last = text
        except Exception:
            pass
        time.sleep(0.5)
    return ""


def load_seen():
    try:
        return int(SEEN_FILE.read_text(encoding="ascii").strip() or 0)
    except Exception:
        return 0


def save_seen(val):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(str(val), encoding="ascii")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("project_pos", nargs="?")
    parser.add_argument("--project", dest="project_flag")
    args = parser.parse_args()
    project = chatutil.normalize_project(args.project_flag or args.project_pos or chatutil.DEFAULT_PROJECT)
    server_locator.ensure_server()
    paths = chatutil.project_paths(project)

    watermark = load_seen()
    message_file_size = Path(paths["messages"]).stat().st_size
    if watermark > message_file_size:
        watermark = 0
    messages, watermark = chatutil.tail_json_lines(paths["messages"], watermark)
    if messages:
        watermark = chatutil.tail_json_lines(paths["messages"], 0)[1]
    save_seen(watermark)
    log(project, started=True, watermark=watermark)

    while True:
        try:
            new_messages, watermark = chatutil.tail_json_lines(paths["messages"], watermark)
            save_seen(watermark)
            for msg in new_messages:
                if str(msg.get("name", "")) not in {"你", "Codex", "OpenCode"}:
                    continue
                text = str(msg.get("text", "")).strip()
                if not PING_RE.search(text):
                    continue
                if "ZCode" not in roster.load_roster():
                    continue
                try:
                    try:
                        inject_via_ui(text)
                        log(project, injected_ui=True, id=msg.get("id"))
                        reply = wait_for_ui_reply(text, timeout=120)
                        if reply:
                            send(project, reply)
                            log(project, ui_replied=True, id=msg.get("id"), reply_length=len(reply))
                        else:
                            log(project, ui_reply_timeout=True, id=msg.get("id"))
                    except Exception as ui_exc:
                        session_id = inject_session_input(text, project)
                        log(project, injected_session=True, id=msg.get("id"), session_id=session_id, ui_error=str(ui_exc))
                except Exception as exc:
                    log(project, session_error=str(exc), id=msg.get("id"))
                    send(project, "@你 二哥桥触发失败，已记日志。")
        except Exception as exc:
            log(project, loop_error=str(exc))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
