#!/usr/bin/env python3
"""Backfill missing chatroom messages from a sample/backup messages file.

Merges messages found in --source files into the target project's
messages.jsonl without creating duplicates: a message is considered
already-present when its (ts, name, text) fingerprint matches any existing
entry. Surviving messages are sorted by timestamp and given fresh
sequential ids. The original file is backed up as .bak and the matching
transcript.md is rebuilt atomically.

Usage:
  python backfill.py --source path/to/messages.main.jsonl --project cs2 --dry-run
  python backfill.py --source path/to/messages.main.jsonl --project cs2
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil  # noqa: E402


def load_jsonl(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                    if isinstance(m, dict) and "text" in m:
                        out.append(m)
                except Exception:
                    pass
    except FileNotFoundError:
        print("WARN: source not found: %s" % path)
    except Exception as exc:
        print("WARN: cannot parse %s: %s" % (path, exc))
    return out


def fingerprint(m):
    return (m.get("ts", ""), m.get("name", ""), m.get("text", ""))


def parse_ts(m):
    try:
        return datetime.strptime(m.get("ts", ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Backfill missing chatroom messages (dedupe by ts+name+text, re-id by time)")
    parser.add_argument("--source", action="append", required=True,
                        help="sample/backup messages file(s); repeatable")
    parser.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    parser.add_argument("--dry-run", action="store_true", help="preview only, write nothing")
    args = parser.parse_args()

    paths = chatutil.project_paths(args.project)
    target = paths["messages"]
    existing = load_jsonl(target)
    known = {fingerprint(m) for m in existing}

    candidates = []
    for src in args.source:
        for m in load_jsonl(src):
            # 只补属于目标项目的消息（无 project 字段的旧消息也算通用）
            proj = m.get("project")
            if proj and proj != paths["project"]:
                continue
            if fingerprint(m) in known:
                continue
            candidates.append(m)

    # 时间无法解析的排最后，其余按原时间戳排序；id 重排为 1..N
    candidates.sort(key=lambda m: (parse_ts(m) is None, parse_ts(m) or datetime.max, m.get("ts", "")))

    print("target     : %s" % target)
    print("existing   : %d messages" % len(existing))
    print("candidates : %d missing messages from %d source(s)" % (len(candidates), len(args.source)))
    for m in candidates:
        print("  + [%s] %s: %s" % (m.get("ts", "?"), m.get("name", "?"), (m.get("text", "") or "")[:60]))
    if args.dry_run or not candidates:
        print("dry-run: nothing written" if args.dry_run else "nothing to do")
        return 0

    merged = existing + candidates
    # 按时间稳定重排 id（无时间戳的保持相对顺序排最后，与上面一致）
    merged.sort(key=lambda m: (parse_ts(m) is None, parse_ts(m) or datetime.max, m.get("ts", "")))
    for i, m in enumerate(merged, 1):
        m["id"] = i
        m.setdefault("project", paths["project"])

    # 备份原文件
    bak = target + ".backfill.bak"
    with open(target, "rb") as src, open(bak, "wb") as dst:
        dst.write(src.read())
    print("backup     : %s" % bak)

    # 原子替换写回
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for m in merged:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # 重建 transcript.md（同样先备份再原子替换）
    tp = paths["transcript"]
    if os.path.exists(tp):
        with open(tp, "rb") as src, open(tp + ".backfill.bak", "wb") as dst:
            dst.write(src.read())
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(tp), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for m in merged:
                f.write("[%s] %s: %s\n" % (m.get("ts", "?"), m.get("name", "?"), m.get("text", "")))
        os.replace(tmp, tp)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    print("done       : %d messages total (added %d), transcript rebuilt" % (len(merged), len(candidates)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
