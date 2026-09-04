"""按项目监听聊天室活动：静默超过 idle_minutes 后自动解除三兄弟调动。"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import chatutil

BASE = os.path.dirname(os.path.abspath(__file__))
DISPATCHER = os.path.join(os.path.dirname(BASE), "scripts", "dispatch.ps1")
POLL = 15.0


def post(project, text):
    try:
        port = int(chatutil.load_config().get("port", 8787))
        payload = json.dumps({"name": "Codex", "text": text}, ensure_ascii=False).encode("utf-8")
        url = "http://127.0.0.1:%d/api/send?project=%s" % (port, urllib.parse.quote(project))
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="三兄弟调度空闲自动解除（按项目）")
    parser.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    parser.add_argument("--idle-minutes", type=float, default=15.0)
    args = parser.parse_args()
    paths = chatutil.project_paths(args.project)
    idle_seconds = max(5.0, args.idle_minutes * 60.0)
    pos = 0
    msgs, pos = chatutil.tail_json_lines(paths["messages"], 0)
    last_id = max((m.get("id", 0) for m in msgs), default=0)
    last_activity = time.monotonic()
    print("(dispatch_idle) project=%s idle=%.1fmin started, last_id=%d"
          % (args.project, args.idle_minutes, last_id), flush=True)
    while True:
        time.sleep(POLL)
        msgs, pos = chatutil.tail_json_lines(paths["messages"], pos)
        cur = max((m.get("id", 0) for m in msgs), default=last_id)
        if cur > last_id:
            last_id = cur
            last_activity = time.monotonic()
            print("(dispatch_idle) activity id=%d, timer reset" % cur, flush=True)
            continue
        if time.monotonic() - last_activity >= idle_seconds:
            print("(dispatch_idle) idle %.1fmin reached, auto release" % args.idle_minutes, flush=True)
            post(args.project, "检测到本频道 %s 分钟无活动，自动解除调动。" % ("%g" % args.idle_minutes))
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", DISPATCHER, "-Action", "stop", "-Project", args.project],
                creationflags=0x08000000,
            )
            return


if __name__ == "__main__":
    main()
