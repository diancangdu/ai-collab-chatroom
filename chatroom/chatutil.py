"""三模型协作战通用工具：聊天室多项目文件命名与项目发现。"""

import os
import re
import json

DEFAULT_PROJECT = "cs2"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def normalize_project(project):
    p = (project or DEFAULT_PROJECT).strip().lower()
    p = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", p).strip("-") or DEFAULT_PROJECT
    return p


def project_paths(project):
    """返回项目的数据文件路径。cs2 沿用旧文件名，历史消息与水位零迁移。"""
    p = normalize_project(project)
    if p == DEFAULT_PROJECT:
        return {
            "project": p,
            "messages": os.path.join(DATA_DIR, "messages.jsonl"),
            "transcript": os.path.join(DATA_DIR, "transcript.md"),
            "transcript_old": os.path.join(DATA_DIR, "transcript.old.md"),
            "opencode_seen": os.path.join(DATA_DIR, "opencode_seen.txt"),
            "watchdog_seen": os.path.join(DATA_DIR, "watchdog_seen.txt"),
            "opencode_flag": os.path.join(DATA_DIR, "opencode_flag.json"),
        }
    return {
        "project": p,
        "messages": os.path.join(DATA_DIR, "messages.%s.jsonl" % p),
        "transcript": os.path.join(DATA_DIR, "transcript.%s.md" % p),
        "transcript_old": os.path.join(DATA_DIR, "transcript.%s.old.md" % p),
        "opencode_seen": os.path.join(DATA_DIR, "opencode_seen.%s.txt" % p),
        "watchdog_seen": os.path.join(DATA_DIR, "watchdog_seen.%s.txt" % p),
        "opencode_flag": os.path.join(DATA_DIR, "opencode_flag.%s.json" % p),
    }


def known_projects():
    """扫描数据目录，返回全部已知项目名。"""
    projects = {DEFAULT_PROJECT}
    if os.path.isdir(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            # 旧版 messages.jsonl 属于默认项目 cs2，不按分项目文件解析
            if name.startswith("messages.") and name.endswith(".jsonl") and name != "messages.jsonl":
                projects.add(name[len("messages."):-len(".jsonl")])
    return sorted(projects)


def tail_json_lines(path, pos):
    """从 pos 开始增量读取消息行，返回 (新消息列表, 新位置)。文件被截断/轮转时自动回开头。"""
    out = []
    try:
        size = os.path.getsize(path)
        if pos > size:
            pos = 0
        with open(path, "rb") as f:
            f.seek(pos)
            while True:
                line_start = f.tell()
                raw = f.readline()
                if not raw:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line.decode("utf-8"))
                    if isinstance(m, dict) and "text" in m:
                        out.append(m)
                except Exception:
                    # 并发追加导致行未写完时回退到行首，下一轮再读
                    pos = line_start
                    break
                pos = f.tell()
    except FileNotFoundError:
        pos = 0
    except Exception:
        pass
    return out, pos
