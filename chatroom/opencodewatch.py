import argparse
import io
import time

import chatutil

POLL = 2.0
MENTIONS = ("@opencode", "@三弟", "@三哥", "集合令")


def load_seen(path):
    try:
        return int(io.open(path, encoding="ascii").read().strip())
    except Exception:
        return 0


def save_seen(path, i):
    io.open(path, "w", encoding="ascii").write(str(i))


def mentions_me(m):
    text = (m.get("text") or "").lower()
    return any(k in text for k in MENTIONS)


def fmt(m, addressed=False):
    tag = "[点名你]" if addressed else "[勿应]"
    return "%s [%s] %s: %s" % (tag, m.get("ts", "?"), m.get("name", "?"), m.get("text", ""))


def deliver(new):
    """投递规则：点名我的才回应，别人的点名/普通群消息严禁抢答。"""
    mine = [m for m in new if mentions_me(m)]
    others = [m for m in new if not mentions_me(m)]
    print("== 值守投递规则：只回应[点名你]；[勿应]消息禁止回复、禁止报到、禁止抢答 ==")
    for m in others:
        print(fmt(m, False))
    for m in mine:
        print(fmt(m, True))
    if mine:
        print("(含[点名你]消息：请处理点名内容并在聊天室回复)")
    else:
        print("(本轮没有点名你的消息：请保持沉默，不要在聊天室发言)")


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
                deliver([m for m in new if m.get("name") != "OpenCode"])
                return
    deadline = time.time() + args.max_wait
    while True:
        msgs, pos = chatutil.tail_json_lines(paths["messages"], pos)
        new = [m for m in msgs if m.get("id", 0) > seen]
        if new:
            seen = max(seen, max(m.get("id", 0) for m in new))
            save_seen(paths["opencode_seen"], seen)
            others = [m for m in new if m.get("name") != "OpenCode"]
            if others:
                deliver(others)
                return
        if time.time() >= deadline:
            print("(idle) no new messages, watermark=%d" % seen)
            return
        time.sleep(POLL)


if __name__ == "__main__":
    main()
