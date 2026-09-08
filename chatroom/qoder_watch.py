#!/usr/bin/env python3
"""Safe Qoder bridge for chatroom mentions."""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil
import roster
import server_locator


POLL_SECONDS = 1.0
REPLY_TIMEOUT = 90.0
WORK_SEND_TIMEOUT = 30.0
WORK_MAX_WAIT_SECONDS = float(os.environ.get("QODER_WORK_MAX_WAIT_SECONDS", "3600"))
PING_RE = re.compile(r"(?:^|[^A-Za-z0-9])@(?:四哥|qoder)\b", re.IGNORECASE)
TASK_RE = re.compile(r"(?:^|[^A-Za-z0-9])#T[0-9A-Za-z_-]+", re.IGNORECASE)
RUNTIME = Path(__file__).resolve().parent
LOG_PATH = RUNTIME / "data" / "qoder_watch.log"
SEEN_FILE = RUNTIME / "data" / "qoder_direct_seen.txt"
BRIDGE_SESSION_FILE = RUNTIME / "data" / "qoder_bridge_session.txt"
PENDING_TASKS_FILE = RUNTIME / "data" / "qoder_pending_tasks.json"
CDP_PORT = int(os.environ.get("QODER_CDP_PORT", "9223"))
CDP_TIMEOUT_SECONDS = float(os.environ.get("QODER_CDP_TIMEOUT_SECONDS", "90"))
CONFIG_DIR = Path.home() / ".qoder-cn"
QODER_APP_DB = Path.home() / "AppData" / "Roaming" / "com.qodercn.app.stable" / "main.sqlite"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PYTHONW = str(Path(sys.executable).with_name("pythonw.exe"))
if not Path(PYTHONW).exists():
    PYTHONW = sys.executable


def log(project, **data):
    item = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "project": project, **data}
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def send(project, text):
    subprocess.run(
        [PYTHONW, str(RUNTIME / "chatroom.py"), "send", "--name", "Qoder", "--project", project, "--text", text],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=0x08000000,
    )


def node_path():
    if os.environ.get("QODER_NODE"):
        return Path(os.environ["QODER_NODE"])
    fallback = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
    if fallback.exists():
        return fallback
    node = shutil.which("node")
    if node:
        return Path(node)
    return Path("node")


def cli_path():
    override = os.environ.get("QODER_CLI_JS")
    if override:
        return Path(override)
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm:
        try:
            result = subprocess.run(
                [npm, "root", "-g"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=0x08000000,
            )
            candidate = Path((result.stdout or "").strip()) / "@qodercn-ai" / "qoderclicn" / "bundle" / "qoderclicn.js"
            if candidate.exists():
                return candidate
        except Exception:
            pass
    return Path.home() / "AppData" / "Local" / "nvm" / "v18.20.4" / "node_modules" / "@qodercn-ai" / "qoderclicn" / "bundle" / "qoderclicn.js"


def cdp_ready():
    try:
        with urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=1):
            return True
    except Exception:
        return False


def config_dir():
    return Path(os.environ["QODER_CONFIG_DIR"]) if os.environ.get("QODER_CONFIG_DIR") else CONFIG_DIR


def bridge_session_id():
    try:
        value = BRIDGE_SESSION_FILE.read_text(encoding="ascii").strip()
        if value:
            uuid.UUID(value)
            return value
    except Exception:
        pass
    value = str(uuid.uuid4())
    BRIDGE_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    BRIDGE_SESSION_FILE.write_text(value, encoding="ascii")
    return value


def bridge_session_args():
    value = bridge_session_id()
    if BRIDGE_SESSION_FILE.exists():
        return ["--resume", value]
    return ["--session-id", value, "--name", "通信桥（四哥）"]


def inject_gui_projection(session_id, user_text, assistant_text):
    if not QODER_APP_DB.exists():
        return False
    now_ms = int(time.time() * 1000)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    turn_id = str(uuid.uuid4())
    assistant_id = "assistant:" + turn_id
    user_payload = {
        "id": turn_id, "role": "user", "turnId": turn_id, "issueId": None,
        "text": user_text[:4000], "timestamp": timestamp, "tools": [], "attachments": [],
        "selectedSkillNames": [], "selectedPluginIds": [], "selectedConnectorIds": [],
        "selectedAgentNames": [], "selectedCapabilityCommands": [], "referencedChatSessions": [],
    }
    assistant_payload = {
        "id": assistant_id, "role": "assistant", "turnId": turn_id, "requestSetId": turn_id,
        "text": assistant_text[:1200], "timestamp": timestamp, "tools": [],
        "turnStartedAt": timestamp, "durationMs": 0,
        "turnMetrics": {"durationMs": 0, "turnCount": 1},
        "parts": [{"id": turn_id + ":text:0", "type": "text", "text": assistant_text[:1200], "parentToolUseId": None}],
        "finalTextId": turn_id + ":text:0",
    }
    connection = sqlite3.connect(str(QODER_APP_DB), timeout=15)
    try:
        with connection:
            sequence = int(connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM chat_session_messages WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]) + 1
            connection.execute(
                "INSERT INTO chat_session_messages(session_id,message_id,turn_id,sequence,payload_json,status,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, turn_id, turn_id, sequence, json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")), "completed", "host-projection", now_ms, now_ms),
            )
            connection.execute(
                "INSERT INTO chat_session_messages(session_id,message_id,turn_id,sequence,payload_json,status,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, assistant_id, turn_id, sequence + 1, json.dumps(assistant_payload, ensure_ascii=False, separators=(",", ":")), "completed", "sdk-projection", now_ms, now_ms),
            )
            connection.execute("UPDATE chat_sessions SET updated_at=?, unread=1 WHERE session_id=?", (now_ms, session_id))
        return True
    finally:
        connection.close()


def ask_qoder(project, text, image_path=None, work_mode=False):
    if work_mode:
        prompt = (
            "聊天室开发任务（项目 " + project + "）：\n" + text[:8000] + "\n"
            "请实际执行这个任务，不要只停留在 ACK 或设计。可以使用工具和命令；"
            "严格遵守安全与隐私约束，完成后回报改动文件、测试结果和验证方法。"
        )
        timeout_seconds = float(os.environ.get("QODER_WORK_TIMEOUT_SECONDS", "600"))
    else:
        prompt = (
            "聊天室消息（项目 " + project + "）：\n" + text[:4000] + "\n"
            "请用一句中文回复。禁止使用工具，禁止执行命令，禁止输出代码块。"
        )
        timeout_seconds = CDP_TIMEOUT_SECONDS
    if cdp_ready():
        command = [
            str(node_path()),
            str(RUNTIME / "qoder_cdp_send.js"),
            "--port", str(CDP_PORT),
            "--session-file", str(BRIDGE_SESSION_FILE),
            "--cwd", str(RUNTIME.parent),
            "--prompt", prompt,
            *(["--image", str(image_path)] if image_path else []),
            "--timeout-ms", str(int(timeout_seconds * 1000)),
            *(["--return-after-send"] if work_mode else []),
        ]
        wait_seconds = WORK_SEND_TIMEOUT if work_mode else timeout_seconds + 10
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=wait_seconds,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            payload = json.loads(result.stdout)
            if work_mode and payload.get("sent") and payload.get("turnId"):
                return {"pending": True, "turn_id": str(payload["turnId"])}
            return str(payload.get("text", ""))[:1200]
        log(project, cdp_error=(result.stderr or "unknown_cdp_error")[:500])

    if work_mode:
        raise RuntimeError("qoder_work_bridge_unavailable")

    command = [
        str(node_path()),
        str(cli_path()),
        "--config-dir", str(config_dir()),
        "--cwd", str(RUNTIME.parent),
        *bridge_session_args(),
        "--permission-mode", "dont_ask",
        "--tools", "",
        "--output-format", "text",
        "--print", prompt,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=REPLY_TIMEOUT,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        if "Not logged in" in message:
            raise RuntimeError("not_logged_in")
        raise RuntimeError(message[:500] or "qoder_cli_exit_%d" % result.returncode)
    reply = (result.stdout or "").strip()
    if not reply:
        raise RuntimeError("empty_reply")
    return reply[:1200]


def load_seen():
    try:
        return int(SEEN_FILE.read_text(encoding="ascii").strip() or 0)
    except Exception:
        return 0


def save_seen(value):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(str(value), encoding="ascii")


def load_pending_tasks():
    try:
        value = json.loads(PENDING_TASKS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def save_pending_tasks(tasks):
    PENDING_TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PENDING_TASKS_FILE.with_name("." + PENDING_TASKS_FILE.name + ".tmp")
    temporary.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, PENDING_TASKS_FILE)


def register_pending_task(project, message, turn_id):
    tasks = [task for task in load_pending_tasks() if task.get("turn_id") != turn_id]
    tasks.append({
        "project": project,
        "message_id": message.get("id"),
        "turn_id": turn_id,
        "started_at": time.time(),
        "max_wait_seconds": WORK_MAX_WAIT_SECONDS,
    })
    save_pending_tasks(tasks)


def qoder_task_result(session_id, turn_id):
    connection = sqlite3.connect(str(QODER_APP_DB), timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT status, source, payload_json FROM chat_session_messages "
            "WHERE session_id=? AND turn_id=? ORDER BY sequence",
            (session_id, turn_id),
        ).fetchall()
        assistant_rows = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except Exception:
                continue
            if payload.get("role") == "assistant":
                assistant_rows.append((row, payload))
        if not assistant_rows:
            return {"state": "running"}
        row, payload = assistant_rows[-1]
        text = str(payload.get("text") or "").strip()
        if row["status"] == "completed" and text:
            return {"state": "completed", "text": text}
        if row["status"] in ("interrupted", "failed", "error"):
            return {"state": "interrupted", "text": text}
        return {"state": "running", "text": text}
    finally:
        connection.close()


def recover_pending_tasks(session_id):
    tasks = load_pending_tasks()
    remaining = []
    now = time.time()
    for task in tasks:
        project = str(task.get("project") or chatutil.DEFAULT_PROJECT)
        turn_id = str(task.get("turn_id") or "")
        max_wait = float(task.get("max_wait_seconds") or WORK_MAX_WAIT_SECONDS)
        try:
            result = qoder_task_result(session_id, turn_id)
            if result["state"] == "completed":
                reply = result["text"][:12000]
                send(project, reply)
                log(project, async_recovered=True, id=task.get("message_id"), turn_id=turn_id, reply_length=len(reply))
                continue
            if result["state"] == "interrupted":
                send(project, "任务在 Qoder 中被中断，未生成完整回报。请重新派单或让四哥继续。")
                log(project, async_interrupted=True, id=task.get("message_id"), turn_id=turn_id)
                continue
            if now - float(task.get("started_at", now)) > max_wait:
                send(project, "等待 Qoder 任务完成超时；Qoder 可能仍在本地执行，请检查 GUI 后重派。")
                log(project, async_timeout=True, id=task.get("message_id"), turn_id=turn_id)
                continue
            remaining.append(task)
        except Exception as exc:
            log(project, async_recovery_error=str(exc), id=task.get("message_id"), turn_id=turn_id)
            remaining.append(task)
    if len(remaining) != len(tasks):
        save_pending_tasks(remaining)


def main():
    parser = argparse.ArgumentParser(description="Qoder chatroom mention bridge")
    parser.add_argument("project_pos", nargs="?")
    parser.add_argument("--project", dest="project_flag")
    args = parser.parse_args()
    project = chatutil.normalize_project(args.project_flag or args.project_pos or chatutil.DEFAULT_PROJECT)
    server_locator.ensure_server()
    paths = chatutil.project_paths(project)
    watermark = load_seen()
    if watermark > Path(paths["messages"]).stat().st_size:
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
                if str(msg.get("name", "")) == "Qoder":
                    continue
                text = str(msg.get("text", "")).strip()
                if not PING_RE.search(text) or "Qoder" not in roster.load_roster():
                    continue
                try:
                    log(project, message_received=True, id=msg.get("id"))
                    reply = ask_qoder(
                        project,
                        chatutil.image_prompt(text, msg),
                        image_path=msg.get("image_path"),
                        work_mode=bool(TASK_RE.search(text)),
                    )
                    if isinstance(reply, dict) and reply.get("pending"):
                        register_pending_task(project, msg, reply["turn_id"])
                        log(project, async_registered=True, id=msg.get("id"), turn_id=reply["turn_id"])
                        continue
                    log(project, ask_finished=True, id=msg.get("id"))
                    try:
                        inject_gui_projection(bridge_session_id(), text, reply)
                        log(project, gui_projection_finished=True, id=msg.get("id"))
                    except Exception as exc:
                        log(project, gui_projection_error=str(exc), id=msg.get("id"))
                    send(project, reply)
                    log(project, replied=True, id=msg.get("id"), reply_length=len(reply))
                except Exception as exc:
                    log(project, bridge_error=str(exc), id=msg.get("id"))
        except Exception as exc:
            log(project, loop_error=str(exc))
        try:
            recover_pending_tasks(bridge_session_id())
        except Exception as exc:
            log(project, pending_loop_error=str(exc))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
