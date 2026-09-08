#!/usr/bin/env python3
"""Safe Qoder bridge for chatroom mentions."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil
import roster
import server_locator


POLL_SECONDS = 1.0
REPLY_TIMEOUT = 90.0
PING_RE = re.compile(r"(?:^|\s)@(?:四哥|qoder)\b", re.IGNORECASE)
RUNTIME = Path(__file__).resolve().parent
LOG_PATH = RUNTIME / "data" / "qoder_watch.log"
SEEN_FILE = RUNTIME / "data" / "qoder_direct_seen.txt"
NODE_PATH = Path(
    r"C:\Users\64560\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
CONFIG_DIR = Path.home() / ".qoder-cn"
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
    if NODE_PATH.exists():
        return NODE_PATH
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


def config_dir():
    return Path(os.environ["QODER_CONFIG_DIR"]) if os.environ.get("QODER_CONFIG_DIR") else CONFIG_DIR


def ask_qoder(project, text):
    prompt = (
        "聊天室消息（项目 " + project + "）：\n" + text[:4000] + "\n"
        "请用一句中文回复。禁止使用工具，禁止执行命令，禁止输出代码块。"
    )
    command = [
        str(node_path()),
        str(cli_path()),
        "--config-dir", str(config_dir()),
        "--cwd", str(Path.home()),
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
                    reply = ask_qoder(project, text)
                    send(project, reply)
                    log(project, replied=True, id=msg.get("id"), reply_length=len(reply))
                except Exception as exc:
                    log(project, bridge_error=str(exc), id=msg.get("id"))
        except Exception as exc:
            log(project, loop_error=str(exc))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
