"""三模型协作战通用工具：聊天室多项目文件命名与项目发现。"""

import os
import re
import json

DEFAULT_PROJECT = "main"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def normalize_project(project):
    p = (project or DEFAULT_PROJECT).strip().lower()
    p = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", p).strip("-") or DEFAULT_PROJECT
    return p


def project_paths(project):
    """返回项目的数据文件路径，每个项目独立文件。"""
    p = normalize_project(project)
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
    projects = set()
    if os.path.isdir(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            if name.startswith("messages.") and name.endswith(".jsonl"):
                projects.add(name[len("messages."):-len(".jsonl")])
    return sorted(projects) or [DEFAULT_PROJECT]


def load_config():
    """读取项目根目录 config.json（不存在则返回空配置）。"""
    cfg = {}
    path = os.path.join(os.path.dirname(BASE_DIR), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass
    return cfg


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
