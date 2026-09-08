#!/usr/bin/env python3
"""Lightweight workload desk: live status and automatic sibling support."""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil
import auth
import roster
import commander
import server_locator


POLL_SECONDS = 2.0
PRESENCE_SECONDS = 120.0
MANUAL_STATUS_SECONDS = 30 * 60
UNRESPONSIVE_SECONDS = 90.0
BUSY_TASK_THRESHOLD = 2
PROCESS_SCAN_SECONDS = 15.0
CHAT_URL = "http://127.0.0.1:8787/api/send"
LOG_NAME = "workload.log"

AGENTS = ("Codex", "ZCode", "OpenCode")
PROCESS_NAMES = {"codex.exe": "Codex", "zcode.exe": "ZCode", "opencode.exe": "OpenCode"}
ALIASES = {
    "codex": "Codex", "大哥": "Codex",
    "zcode": "ZCode", "二哥": "ZCode",
    "opencode": "OpenCode", "三哥": "OpenCode", "三弟": "OpenCode",
}
MENTION_RE = re.compile(
    r"@?(codex|大哥|zcode|二哥|opencode|三哥|三弟)\b", re.IGNORECASE)
TASK_RE = re.compile(r"^[!！](?:任务|派单|支援|task)\s*(.+)$", re.IGNORECASE)
CLAIM_RE = re.compile(r"^[!！](?:认领|接手)\s*(?:#)?([A-Za-z0-9_-]+)\s*$", re.IGNORECASE)
DONE_RE = re.compile(r"^[!！](?:完成|done)\s*(?:#)?([A-Za-z0-9_-]+)\s*$", re.IGNORECASE)
CANCEL_RE = re.compile(r"^[!！](?:取消|cancel)\s*(?:#)?([A-Za-z0-9_-]+)\s*$", re.IGNORECASE)
BUSY_RE = re.compile(r"^[!！](?:忙|busy)\b.*$", re.IGNORECASE)
IDLE_RE = re.compile(r"^[!！](?:空闲|idle)\b.*$", re.IGNORECASE)


def runtime_dir():
    return Path(__file__).resolve().parent


def data_dir():
    return runtime_dir() / "data"


def active_agents():
    return set(roster.load_roster())


def is_roster_active(name):
    return name in active_agents()


def state_path(project):
    paths = chatutil.project_paths(project)
    stem = Path(paths["opencode_seen"]).with_name("workload.json")
    if project != chatutil.DEFAULT_PROJECT:
        stem = Path(paths["opencode_seen"]).with_name("workload.%s.json" % project)
    return stem


def log_path(project):
    if project == chatutil.DEFAULT_PROJECT:
        return data_dir() / LOG_NAME
    return data_dir() / ("workload.%s.log" % project)


def acquire_instance_lock(project):
    path = data_dir() / (".workload.%s.lock" % project)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("a+b")
    except OSError:
        return None
    try:
        handle.seek(0)
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode("ascii"))
    handle.flush()
    return handle


def stamp(epoch=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch if epoch is not None else time.time()))


def parse_stamp(value):
    try:
        return time.mktime(time.strptime(str(value), "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0.0


def canonical_agent(value):
    return ALIASES.get(str(value or "").strip().lower().lstrip("@"))


def new_state(project):
    return {
        "project": project,
        "updated_at": stamp(),
        "watermark": 0,
        "agents": {
            name: {
                "last_seen": "", "last_seen_epoch": 0, "last_message_id": 0,
                "manual_status": "", "manual_until_epoch": 0,
            } for name in AGENTS
        },
        "tasks": {},
    }


def load_state(project):
    path = state_path(project)
    try:
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("agents") and state.get("tasks") is not None:
            for name in AGENTS:
                state["agents"].setdefault(name, {
                    "last_seen": "", "last_seen_epoch": 0, "last_message_id": 0,
                    "manual_status": "", "manual_until_epoch": 0,
                })
            return state
    except (OSError, ValueError):
        pass
    return new_state(project)


def save_state(project, state):
    state["updated_at"] = stamp()
    path = state_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def log(project, **data):
    item = {"ts": stamp(), "project": project, **data}
    path = log_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def send_chat(project, text):
    sender = commander.load_commander(project)
    payload = json.dumps({"name": sender, "text": text}, ensure_ascii=False).encode("utf-8")
    url = CHAT_URL + "?project=" + urllib.parse.quote(project)
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json; charset=utf-8", **auth.headers()})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as exc:
        log(project, send_error=str(exc), text=text)


def open_tasks_for(state, name):
    return sorted(
        (t for t in state["tasks"].values()
         if t.get("status") in ("open", "claimed", "supporting")
         and (t.get("owner") == name or name in (t.get("supporters") or []))),
        key=lambda t: int(t.get("id_number", 0)),
    )


def agent_status(state, name, now):
    agent = state["agents"][name]
    if agent.get("manual_status") and float(agent.get("manual_until_epoch") or 0) > now:
        return agent["manual_status"]
    return "busy" if open_tasks_for(state, name) else "idle"


def agent_presence(agent, now):
    return "online" if now - float(agent.get("last_seen_epoch") or 0) <= PRESENCE_SECONDS else "offline"


def detect_online_agents():
    if os.name != "nt":
        return set()
    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    try:
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        if snapshot == -1:
            return set()
        found = set()
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                agent = PROCESS_NAMES.get(str(entry.szExeFile).lower())
                if agent:
                    found.add(agent)
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return found
    except Exception:
        return set()


def presence_for(state, name, agent, now):
    online = state.get("online_agents")
    if isinstance(online, (set, list)):
        return "online" if name in online else "offline"
    return "online" if now - float(agent.get("last_seen_epoch") or 0) <= PRESENCE_SECONDS else "offline"


def refresh_agents(state, now):
    active = active_agents()
    for name in AGENTS:
        agent = state["agents"][name]
        if agent.get("manual_status") and float(agent.get("manual_until_epoch") or 0) <= now:
            agent["manual_status"] = ""
            agent["manual_until_epoch"] = 0
        agent["status"] = agent_status(state, name, now)
        agent["presence"] = presence_for(state, name, agent, now)
        agent["open_task_count"] = len(open_tasks_for(state, name))
        agent["roster_active"] = name in active


def next_task_id(state):
    numbers = []
    for task_id in state["tasks"]:
        match = re.match(r"T(\d+)$", task_id, re.IGNORECASE)
        if match:
            numbers.append(int(match.group(1)))
    return "T%d" % ((max(numbers) + 1) if numbers else 1)


def extract_target(text):
    match = MENTION_RE.search(text or "")
    if not match:
        return None, (text or "").strip()
    target = canonical_agent(match.group(1))
    title = (text[:match.start()] + " " + text[match.end():]).strip()
    return target, title


def pick_owner(state, now, preferred=None):
    active = active_agents()
    if preferred and preferred in active:
        return preferred
    candidates = []
    for name in AGENTS:
        if name not in active:
            continue
        candidates.append((len(open_tasks_for(state, name)), name))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], AGENTS.index(item[1])))
    return candidates[0][1]


def pick_supporter(state, now, owner, existing=()):
    ranked = []
    active = active_agents()
    for name in AGENTS:
        if name == owner or name in existing or name not in active:
            continue
        agent = state["agents"][name]
        if agent_status(state, name, now) != "idle":
            continue
        if presence_for(state, name, agent, now) != "online":
            continue
        ranked.append((len(open_tasks_for(state, name)), name))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], AGENTS.index(item[1])))
    return ranked[0][1]


def task_summary(task):
    return "#%s %s" % (task["id"], task.get("title") or "未命名任务")


def add_handoff(task, action, from_name, to_name, at):
    history = task.setdefault("handoff_history", [])
    item = {"action": action, "from": from_name, "to": to_name, "at": stamp(at)}
    if not history or history[-1] != item:
        history.append(item)


def create_task(state, project, msg, now):
    raw = TASK_RE.match(str(msg.get("text") or "")).group(1).strip()
    preferred, title = extract_target(raw)
    owner = pick_owner(state, now, preferred)
    if not owner:
        send_chat(project, "@你 当前没有纳入开工的兄弟，任务已拒绝创建。")
        return
    task_id = next_task_id(state)
    match = re.match(r"T(\d+)$", task_id, re.IGNORECASE)
    task = {
        "id": task_id,
        "id_number": int(match.group(1)),
        "title": title or "未命名任务",
        "owner": owner,
        "supporters": [],
        "status": "open",
        "created_by": msg.get("name", "你"),
        "created_message_id": msg.get("id", 0),
        "created_at": stamp(now),
        "created_epoch": now,
        "claimed_at": "",
        "acknowledged": False,
        "support_reason": "",
        "support_requested_at": "",
    }
    state["tasks"][task_id] = task
    refresh_agents(state, now)
    send_chat(project, "@%s 任务已登记：%s" % (owner, task_summary(task)))
    log(project, task_created=True, task=task_id, owner=owner)


def finish_task(state, project, msg, task_id, status):
    actor = canonical_agent(msg.get("name"))
    task = state["tasks"].get(task_id)
    if not task or task.get("status") not in ("open", "claimed", "supporting"):
        return
    if actor and actor not in (task.get("owner"), *(task.get("supporters") or [])):
        return
    task["status"] = status
    task["finished_at"] = stamp()
    task["finished_by"] = actor or msg.get("name", "你")
    refresh_agents(state, state.get("last_epoch", time.time()))
    send_chat(project, "#%s 已标记为 %s，处理人 @%s。" % (
        task_id, "完成" if status == "done" else "取消", task.get("owner")))
    log(project, task_updated=True, task=task_id, status=status)


def claim_task(state, project, msg, task_id, now):
    actor = canonical_agent(msg.get("name"))
    task = state["tasks"].get(task_id)
    if not task or task.get("status") not in ("open", "supporting"):
        return
    if actor and is_roster_active(actor):
        task["owner"] = actor
        task["status"] = "claimed"
        task["claimed_at"] = stamp(now)
        task["acknowledged"] = True
        refresh_agents(state, now)
        send_chat(project, "@%s 已认领 %s。" % (actor, task_summary(task)))
        log(project, task_claimed=True, task=task_id, owner=actor)


def reconcile_roster(state, project, now):
    active = active_agents()
    state["active_agents"] = [name for name in AGENTS if name in active]
    changed = False
    for task in list(state["tasks"].values()):
        if task.get("status") not in ("open", "claimed", "supporting", "paused"):
            continue
        owner = task.get("owner")
        supporters = list(task.get("supporters") or [])
        if not active:
            if task.get("status") != "paused":
                task["paused_by_roster"] = True
                task["pre_pause_status"] = "open" if task.get("status") in ("open", "claimed") else "supporting"
                task["pre_pause_owner"] = owner
                task["supporters"] = []
                task["status"] = "paused"
                add_handoff(task, "paused_all_inactive", owner, "", now)
                send_chat(project, "所有兄弟均已退出开工，%s 已暂停并保留进度。" % task_summary(task))
                log(project, roster_pause=True, task=task["id"], owner=owner)
                changed = True
            continue

        if task.get("status") == "paused":
            task["status"] = task.get("pre_pause_status") or "open"
            if task.get("status") == "supporting" and not task.get("supporters"):
                task["status"] = "open"
            task["paused_by_roster"] = False
            owner = task.get("pre_pause_owner") or owner
            add_handoff(task, "resumed", "", owner, now)
            task.pop("pre_pause_status", None)
            task.pop("pre_pause_owner", None)
            send_chat(project, "有兄弟重新加入开工，%s 已恢复。" % task_summary(task))
            log(project, roster_resume=True, task=task["id"], owner=owner)
            changed = True

        old_owner = owner
        if owner not in active:
            replacement = pick_owner(state, now)
            if replacement and replacement != owner:
                task["owner"] = replacement
                task["previous_owner"] = owner
                task["acknowledged"] = False
                add_handoff(task, "reassigned", owner, replacement, now)
                send_chat(project, "@%s 接管 %s；原负责人 @%s 已退出开工。" % (
                    replacement, task_summary(task), owner))
                log(project, roster_handoff=True, task=task["id"], from_owner=owner, to_owner=replacement)
                changed = True
            elif not replacement:
                continue

        old_supporters = supporters
        kept = [name for name in supporters if name in active and name != task.get("owner")]
        if kept != old_supporters:
            removed = sorted(set(old_supporters) - set(kept))
            task["supporters"] = kept
            task["status"] = "supporting" if kept else "open"
            if removed:
                add_handoff(task, "supporter_removed", ",".join(removed), "", now)
                send_chat(project, "支援名单已更新：%s；任务 %s。" % (
                    ", ".join("@" + name for name in removed), task_summary(task)))
                log(project, roster_supporter_removed=True, task=task["id"], removed=removed)
            changed = True
        if old_owner != task.get("owner"):
            changed = True
    if changed:
        refresh_agents(state, now)
    return changed


def maybe_support(state, project, now):
    changed = False
    for task in list(state["tasks"].values()):
        if task.get("status") not in ("open", "claimed", "supporting"):
            continue
        owner = task.get("owner")
        if owner not in AGENTS or owner not in active_agents() or (task.get("supporters") or []):
            continue
        refresh_agents(state, now)
        owner_agent = state["agents"][owner]
        owner_tasks = len(open_tasks_for(state, owner))
        unresponsive = (
            not task.get("acknowledged")
            and now - float(task.get("created_epoch") or 0) >= UNRESPONSIVE_SECONDS
        )
        overloaded = owner_tasks >= BUSY_TASK_THRESHOLD
        busy = owner_agent.get("manual_status") == "busy"
        offline = owner_agent.get("presence") == "offline"
        if not (busy or overloaded or unresponsive or offline):
            continue
        supporter = pick_supporter(state, now, owner)
        if not supporter:
            continue
        task["supporters"] = [supporter]
        task["status"] = "supporting"
        task["support_reason"] = "负责人忙" if busy else "负责人积压" if overloaded else "负责人未响应" if unresponsive else "负责人离线"
        task["support_requested_at"] = stamp(now)
        refresh_agents(state, now)
        send_chat(project, "@%s 支援 %s；原因：%s，原负责人 @%s。" % (
            supporter, task_summary(task), task["support_reason"], owner))
        log(project, auto_support=True, task=task["id"], owner=owner,
            supporter=supporter, reason=task["support_reason"])
        changed = True
    return changed


def process_message(state, project, msg, now):
    name = str(msg.get("name") or "")
    sender = canonical_agent(name)
    text = str(msg.get("text") or "").strip()
    if sender:
        agent = state["agents"][sender]
        agent["last_seen"] = str(msg.get("ts") or stamp(now))
        agent["last_seen_epoch"] = now
        agent["last_message_id"] = int(msg.get("id") or 0)
        for task in state["tasks"].values():
            if task.get("owner") == sender and task.get("status") in ("open", "supporting"):
                task["acknowledged"] = True
        if BUSY_RE.match(text):
            agent["manual_status"] = "busy"
            agent["manual_until_epoch"] = now + MANUAL_STATUS_SECONDS
            log(project, status_set=True, agent=sender, status="busy")
        elif IDLE_RE.match(text):
            agent["manual_status"] = ""
            agent["manual_until_epoch"] = 0
            log(project, status_set=True, agent=sender, status="idle")

    claim = CLAIM_RE.match(text)
    if claim:
        claim_task(state, project, msg, claim.group(1).upper(), now)
        return
    done = DONE_RE.match(text)
    if done:
        finish_task(state, project, msg, done.group(1).upper(), "done")
        return
    cancel = CANCEL_RE.match(text)
    if cancel:
        finish_task(state, project, msg, cancel.group(1).upper(), "cancelled")
        return
    if TASK_RE.match(text):
        create_task(state, project, msg, now)


def watch(project, poll):
    paths = chatutil.project_paths(project)
    lock = acquire_instance_lock(project)
    if lock is None:
        log(project, watch_skipped="already_running")
        return
    state = load_state(project)
    reconcile_roster(state, project, time.time())
    messages, pos = chatutil.tail_json_lines(paths["messages"], 0)
    for msg in messages:
        sender = canonical_agent(msg.get("name"))
        if sender:
            agent = state["agents"][sender]
            agent["last_seen"] = str(msg.get("ts") or "")
            agent["last_seen_epoch"] = parse_stamp(msg.get("ts"))
            agent["last_message_id"] = int(msg.get("id") or 0)
    state["watermark"] = max((int(m.get("id") or 0) for m in messages), default=0)
    save_state(project, state)
    log(project, started=True, watermark=state["watermark"])

    try:
        while True:
            now = time.time()
            changed = False
            if now - float(state.get("last_process_scan_epoch") or 0) >= PROCESS_SCAN_SECONDS:
                state["online_agents"] = sorted(detect_online_agents())
                state["last_process_scan_epoch"] = now
                changed = True
            if reconcile_roster(state, project, now):
                changed = True
            messages, pos = chatutil.tail_json_lines(paths["messages"], pos)
            if messages:
                now = time.time()
                state["last_epoch"] = now
                for msg in messages:
                    process_message(state, project, msg, now)
                changed = True
            now = time.time()
            state["last_epoch"] = now
            if maybe_support(state, project, now):
                changed = True
            refresh_agents(state, now)
            if changed or not messages:
                save_state(project, state)
                if messages:
                    state["watermark"] = max(int(m.get("id") or 0) for m in messages)
            time.sleep(max(0.5, float(poll)))
    finally:
        lock.close()


def show_status(project):
    now = time.time()
    state = load_state(project)
    state["last_epoch"] = now
    state["online_agents"] = sorted(detect_online_agents())
    state["last_process_scan_epoch"] = now
    reconcile_roster(state, project, now)
    refresh_agents(state, now)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="三兄弟任务负载与自动支援")
    parser.add_argument("action", nargs="?", default="watch", choices=("watch", "status"))
    parser.add_argument("project_pos", nargs="?")
    parser.add_argument("--project", dest="project_flag")
    parser.add_argument("--poll", type=float, default=POLL_SECONDS)
    args = parser.parse_args()
    project = chatutil.normalize_project(args.project_flag or args.project_pos or chatutil.DEFAULT_PROJECT)
    server_locator.ensure_server()
    if args.action == "status":
        show_status(project)
    else:
        watch(project, args.poll)


if __name__ == "__main__":
    main()
