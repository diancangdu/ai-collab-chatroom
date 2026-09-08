"""三模型协作战通用工具：聊天室多项目文件命名与项目发现。"""

import os
import re
import json

DEFAULT_PROJECT = "cs2"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CHANNELS_DIR = os.path.join(DATA_DIR, "channels")


def normalize_project(project):
    p = (project or DEFAULT_PROJECT).strip().lower()
    p = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", p).strip("-") or DEFAULT_PROJECT
    return p


def project_paths(project):
    """返回项目的数据文件路径。cs2 沿用旧文件名，历史消息与水位零迁移。"""
    p = normalize_project(project)
    channel_dir = os.path.join(CHANNELS_DIR, p)
    os.makedirs(channel_dir, exist_ok=True)
    return {
        "project": p,
        "channel_dir": channel_dir,
        "messages": os.path.join(channel_dir, "messages.jsonl"),
        "transcript": os.path.join(channel_dir, "transcript.md"),
        "transcript_old": os.path.join(channel_dir, "transcript.old.md"),
        "opencode_seen": os.path.join(channel_dir, "opencode_seen.txt"),
        "watchdog_seen": os.path.join(channel_dir, "watchdog_seen.txt"),
        "opencode_flag": os.path.join(channel_dir, "opencode_flag.json"),
    }


def known_projects():
    """扫描数据目录，返回全部已知项目名。"""
    projects = {DEFAULT_PROJECT}
    if os.path.isdir(CHANNELS_DIR):
        for name in os.listdir(CHANNELS_DIR):
            if os.path.isdir(os.path.join(CHANNELS_DIR, name)):
                projects.add(name)
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
