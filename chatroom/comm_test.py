#!/usr/bin/env python3
"""Project startup handshake for the sibling bridges."""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil


PYTHONW = str(Path(sys.executable).with_name("pythonw.exe"))
if not Path(PYTHONW).exists():
    PYTHONW = sys.executable
LOG_PATH = Path(__file__).resolve().parent / "data" / "comm_test.log"
TIME_RE = re.compile(
    r"ACK\b.*?\b(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", re.IGNORECASE
)


def log(project, **data):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    item = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "project": project, **data}
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write("%s\n" % item["ts"])
        handle.write(str(data) + "\n")


def send(project, text, name="Codex"):
    subprocess.run(
        [PYTHONW, str(Path(__file__).resolve().parent / "chatroom.py"),
         "send", "--name", name, "--project", project, "--text", text],
        capture_output=True, text=True, timeout=15, creationflags=0x08000000,
    )


def parse_reply_time(text):
    match = TIME_RE.search(str(text or ""))
    if not match:
        return None
    try:
        stamp = "%s %s" % (match.group(1), match.group(2))
        return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def run(project, timeout=180.0):
    paths = chatutil.project_paths(project)
    _, watermark = chatutil.tail_json_lines(paths["messages"], 0)
    marker = time.strftime("%Y%m%d%H%M%S")
    prompt = (
        "开机通信测试 @ZCode @OpenCode：请各自回复一行，格式为 "
        "ACK %s <当前时间YYYY-MM-DD HH:MM:SS>。时间误差须小于2分钟。"
        % marker
    )
    send(project, prompt)
    expected = {"ZCode", "OpenCode"}
    received = {}
    deadline = time.time() + timeout
    while time.time() < deadline and len(received) < len(expected):
        messages, watermark = chatutil.tail_json_lines(paths["messages"], watermark)
        for message in messages:
            name = str(message.get("name", ""))
            if name not in expected or name in received or marker not in str(message.get("text", "")):
                continue
            reply_time = parse_reply_time(message.get("text"))
            if reply_time is None or abs(reply_time - time.time()) >= 120:
                log(project, rejected=True, name=name, text=message.get("text"))
                continue
            received[name] = message
            log(project, acknowledged=True, name=name, message_id=message.get("id"))
        time.sleep(1)

    if len(received) == len(expected):
        send(project, "通信门禁通过：%s 已按格式回复实时时间。" % "、".join(sorted(received)), "系统")
        log(project, passed=True, agents=sorted(received))
        return 0

    missing = sorted(expected - set(received))
    send(project, "通信门禁失败：@ZCode @OpenCode 有兄弟未在 %d 秒内回复有效时间（缺：%s）。"
         % (int(timeout), "、".join(missing)), "系统")
    log(project, failed=True, missing=missing)
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    raise SystemExit(run(chatutil.normalize_project(args.project), args.timeout))


if __name__ == "__main__":
    main()
