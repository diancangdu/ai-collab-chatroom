import argparse
import io
import json
import subprocess
import time

import chatutil

POLL = 10.0
MENTIONS = ("@opencode", "@三弟", "@三哥")


def load_int(path, default=0):
    try:
        return int(io.open(path, encoding="ascii").read().strip())
    except Exception:
        return default


def save_int(path, i):
    io.open(path, "w", encoding="ascii").write(str(i))


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


def main():
    parser = argparse.ArgumentParser(description="三弟 OpenCode 点名监控（按项目）")
    parser.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    args = parser.parse_args()
    paths = chatutil.project_paths(args.project)
    project = paths["project"]
    seen = load_int(paths["watchdog_seen"])
    pos = 0
    msgs, pos = chatutil.tail_json_lines(paths["messages"], 0)
    if seen == 0 and msgs:
        seen = max(m.get("id", 0) for m in msgs)
        save_int(paths["watchdog_seen"], seen)
        print("(init) watchdog watermark=%d project=%s" % (seen, project))
    elif msgs:
        # 重启时补处理停机期间的新消息
        new = [m for m in msgs if m.get("id", 0) > seen]
        if new:
            seen = max(seen, max(m.get("id", 0) for m in new))
            save_int(paths["watchdog_seen"], seen)
            hits = [m for m in new if m.get("name") != "OpenCode"
                    and any(k in (m.get("text") or "").lower() for k in MENTIONS)]
            if hits:
                flag = {"project": project, "pending": [h["id"] for h in hits],
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "messages": [{"id": h["id"], "ts": h.get("ts"), "name": h.get("name"), "text": h.get("text")} for h in hits]}
                io.open(paths["opencode_flag"], "w", encoding="utf-8").write(json.dumps(flag, ensure_ascii=False, indent=1))
                first = hits[0]
                toast("三弟 OpenCode 被点名 [%s]" % project,
                      "%s %s: %s" % (first.get("ts", ""), first.get("name", ""), (first.get("text") or "")[:60]))
                print("[%s] MENTION(%s): %s" % (time.strftime("%H:%M:%S"), project,
                      "; ".join("#%d by %s" % (h["id"], h.get("name")) for h in hits)))
    print("(running) project=%s polling every %.0fs, Ctrl+C to stop" % (project, POLL))
    while True:
        msgs, pos = chatutil.tail_json_lines(paths["messages"], pos)
        new = [m for m in msgs if m.get("id", 0) > seen]
        hits = []
        for m in new:
            seen = max(seen, m.get("id", 0))
            text = (m.get("text") or "").lower()
            if m.get("name") != "OpenCode" and any(k in text for k in MENTIONS):
                hits.append(m)
        if seen != load_int(paths["watchdog_seen"]):
            save_int(paths["watchdog_seen"], seen)
        if hits:
            flag = {"project": project, "pending": [h["id"] for h in hits],
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "messages": [{"id": h["id"], "ts": h.get("ts"), "name": h.get("name"), "text": h.get("text")} for h in hits]}
            io.open(paths["opencode_flag"], "w", encoding="utf-8").write(json.dumps(flag, ensure_ascii=False, indent=1))
            first = hits[0]
            toast("三弟 OpenCode 被点名 [%s]" % project,
                  "%s %s: %s" % (first.get("ts", ""), first.get("name", ""), (first.get("text") or "")[:60]))
            print("[%s] MENTION(%s): %s" % (time.strftime("%H:%M:%S"), project,
                  "; ".join("#%d by %s" % (h["id"], h.get("name")) for h in hits)))
        time.sleep(POLL)


if __name__ == "__main__":
    main()
