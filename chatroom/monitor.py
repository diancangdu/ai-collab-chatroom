"""三兄弟协作战最小占用监控：单进程合并 opencode/watchdog/idle 值守。"""

import argparse
import io
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
POLL = 5.0
MENTIONS = ("@opencode", "@三弟", "@三哥")


def load_int(path, default=0):
    try:
        return int(io.open(path, encoding="ascii").read().strip())
    except Exception:
        return default


def save_int(path, value):
    io.open(path, "w", encoding="ascii").write(str(int(value)))


def fmt(m):
    return "[%s] %s: %s" % (m.get("ts", "?"), m.get("name", "?"), m.get("text", ""))


def toast(title, body):
    try:
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null; "
            "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$t = $x.GetElementsByTagName('text'); "
            "$t.Item(0).AppendChild($x.CreateTextNode('%s')) | Out-Null; "
            "$t.Item(1).AppendChild($x.CreateTextNode('%s')) | Out-Null; "
            "$n = [Windows.UI.Notifications.ToastNotification]::new($x); "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('三模型聊天室').Show($n)"
        ) % (title.replace("'", "''"), body.replace("'", "''"))
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000)
    except Exception:
        pass


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


def process_msgs(msgs, paths, project, seen, wd_seen, idle_seconds, last_activity):
    max_id = max((m.get("id", 0) for m in msgs), default=0)
    # opencode 常驻角色：打印新消息并推进水位
    for m in msgs:
        if m.get("id", 0) > seen and m.get("name") != "OpenCode":
            print(fmt(m), flush=True)
    if max_id > seen:
        seen = max_id
        save_int(paths["opencode_seen"], seen)

    # watchdog 角色：点名 -> flag + toast
    hits = [m for m in msgs
            if m.get("id", 0) > wd_seen
            and m.get("name") != "OpenCode"
            and any(k in (m.get("text") or "").lower() for k in MENTIONS)]
    if max_id > wd_seen:
        wd_seen = max_id
        save_int(paths["watchdog_seen"], wd_seen)
    if hits:
        flag = {
            "project": project,
            "pending": [h["id"] for h in hits],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "messages": [{"id": h["id"], "ts": h.get("ts"), "name": h.get("name"), "text": h.get("text")} for h in hits],
        }
        io.open(paths["opencode_flag"], "w", encoding="utf-8").write(json.dumps(flag, ensure_ascii=False, indent=1))
        first = hits[0]
        toast("三弟 OpenCode 被点名 [%s]" % project,
              "%s %s: %s" % (first.get("ts", ""), first.get("name", ""), (first.get("text") or "")[:60]))
        print("[%s] MENTION(%s): %s" % (time.strftime("%H:%M:%S"), project,
              "; ".join("#%d by %s" % (h["id"], h.get("name")) for h in hits)), flush=True)

    if msgs and idle_seconds is not None:
        last_activity = time.monotonic()
    return seen, wd_seen, last_activity


def main():
    parser = argparse.ArgumentParser(description="三兄弟协作战最小占用监控（单进程）")
    parser.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    parser.add_argument("--idle-minutes", type=float, default=15.0)
    parser.add_argument("--no-auto-release", action="store_true")
    parser.add_argument("--poll", type=float, default=POLL)
    args = parser.parse_args()
    paths = chatutil.project_paths(args.project)
    project = paths["project"]
    idle_seconds = None if args.no_auto_release else max(5.0, args.idle_minutes * 60.0)

    seen = load_int(paths["opencode_seen"])
    wd_seen = load_int(paths["watchdog_seen"])
    pos = 0
    msgs, pos = chatutil.tail_json_lines(paths["messages"], 0)
    if msgs:
        max_id = max(m.get("id", 0) for m in msgs)
        if seen == 0:
            seen = max_id
            save_int(paths["opencode_seen"], seen)
        if wd_seen == 0:
            wd_seen = max_id
            save_int(paths["watchdog_seen"], wd_seen)
    last_activity = time.monotonic()
    seen, wd_seen, last_activity = process_msgs(
        msgs, paths, project, seen, wd_seen, idle_seconds, last_activity)
    print("(monitor) project=%s started, watermark=%d, idle=%s"
          % (project, seen, "%.1fmin" % args.idle_minutes if idle_seconds else "off"), flush=True)

    while True:
        time.sleep(max(1.0, args.poll))
        msgs, pos = chatutil.tail_json_lines(paths["messages"], pos)
        if not msgs:
            if idle_seconds is not None and time.monotonic() - last_activity >= idle_seconds:
                print("(monitor) idle %.1fmin reached, auto release" % args.idle_minutes, flush=True)
                post(project, "检测到本频道 %s 分钟无活动，自动解除调动。" % ("%g" % args.idle_minutes))
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", DISPATCHER, "-Action", "stop", "-Project", project],
                    creationflags=0x08000000,
                )
                return
            continue
        seen, wd_seen, last_activity = process_msgs(
            msgs, paths, project, seen, wd_seen, idle_seconds, last_activity)


if __name__ == "__main__":
    main()
