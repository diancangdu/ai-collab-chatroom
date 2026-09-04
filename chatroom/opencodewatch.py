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
    parser = argparse.ArgumentParser(description="三弟 OpenCode 单轮聊天室值守（按项目）")
    parser.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    parser.add_argument("--max-wait", type=float, default=600.0)
    args = parser.parse_args()
    paths = chatutil.project_paths(args.project)
    seen = load_seen(paths["opencode_seen"])
    pos = 0
    msgs, pos = chatutil.tail_json_lines(paths["messages"], 0)
    if seen == 0 and msgs:
        seen = max(m.get("id", 0) for m in msgs)
        save_seen(paths["opencode_seen"], seen)
        print("(init) watermark=%d, %d existing messages skipped" % (seen, len(msgs)))
        return
    else:
        new = [m for m in msgs if m.get("id", 0) > seen]
        if new:
            seen = max(m.get("id", 0) for m in new)
            save_seen(paths["opencode_seen"], seen)
            if any(m.get("name") != "OpenCode" for m in new):
                for m in new:
                    if m.get("name") != "OpenCode":
                        print(fmt(m))
                return
    deadline = time.time() + args.max_wait
    while True:
        msgs, pos = chatutil.tail_json_lines(paths["messages"], pos)
        new = [m for m in msgs if m.get("id", 0) > seen]
        if new:
            spoken = False
            for m in new:
                if m.get("id", 0) > seen:
                    seen = m["id"]
                if m.get("name") == "OpenCode":
                    continue
                print(fmt(m))
                spoken = True
            save_seen(paths["opencode_seen"], seen)
            if spoken:
                return
        if time.time() >= deadline:
            print("(idle) no new messages, watermark=%d" % seen)
            return
        time.sleep(POLL)


if __name__ == "__main__":
    main()
