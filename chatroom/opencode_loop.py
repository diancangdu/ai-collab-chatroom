import argparse
import io
import time

import chatutil

POLL = 5.0


def load_seen(path):
    try:
        return int(io.open(path, encoding="ascii").read().strip())
    except Exception:
        return 0


def save_seen(path, i):
    io.open(path, "w", encoding="ascii").write(str(i))


def fmt(m):
    return "[%s] %s: %s" % (m.get("ts", "?"), m.get("name", "?"), m.get("text", ""))


def main():
    parser = argparse.ArgumentParser(description="三弟 OpenCode 常驻聊天室值守（按项目）")
    parser.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    args = parser.parse_args()
    paths = chatutil.project_paths(args.project)
    print("(opencode_loop) continuous watcher started project=%s" % paths["project"], flush=True)
    seen = load_seen(paths["opencode_seen"])
    pos = 0
    msgs, pos = chatutil.tail_json_lines(paths["messages"], 0)
    if seen == 0 and msgs:
        seen = max(m.get("id", 0) for m in msgs)
        save_seen(paths["opencode_seen"], seen)
        print("(init) watermark=%d" % seen, flush=True)
    else:
        for m in msgs:
            if m.get("id", 0) > seen:
                seen = m["id"]
                if m.get("name") != "OpenCode":
                    print(fmt(m), flush=True)
        if msgs:
            save_seen(paths["opencode_seen"], seen)
    while True:
        msgs, pos = chatutil.tail_json_lines(paths["messages"], pos)
        for m in msgs:
            if m.get("id", 0) <= seen:
                continue
            seen = m["id"]
            if m.get("name") == "OpenCode":
                continue
            print(fmt(m), flush=True)
        if msgs:
            save_seen(paths["opencode_seen"], seen)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
