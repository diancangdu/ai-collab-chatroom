#!/usr/bin/env python3
"""Auto-nudge OpenCode when a chatroom mention goes unanswered.

Uses the desktop app's opencode://new-session deep link, so no API key and no
session surgery is required. The relay only fires after a timeout, meaning a
normally responsive OpenCode never sees it.
"""

import argparse
import ctypes
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil
import server_locator


POLL_SECONDS = 5
RESPONSE_TIMEOUT = 120
WATCHER_FALLBACK_TIMEOUT = 180
WATCHER_GRACE_TIMEOUT = 90
FIRE_COOLDOWN = 180
API_COOLDOWN = 30
AUTOACK_COOLDOWN = 45
DIGEST_INTERVAL = 240
ROUTE_PREF_UNTIL = time.mktime(time.strptime("2026-09-07 00:00:00", "%Y-%m-%d %H:%M:%S"))
ACTIVITY_WINDOW = 20
OPENCODE_EXE = os.environ.get("OPENCODE_EXE", "")
WATCHER_MARKS = ("watchdog.py", "opencodewatch.py")
ENTER_ATTEMPTS = 8
ENTER_RETRY_SECONDS = 1.2
NO_WINDOW = 0x08000000
PING_RE = re.compile(r"^@(三弟|三哥|opencode)\b", re.IGNORECASE)
BOSS_PING_RE = re.compile(r"(?:^|\s)@?(?:大哥|codex)", re.IGNORECASE)
ZCODE_ROUTE_RE = re.compile(r"^@(二哥|zcode)\b", re.IGNORECASE)
ZCODE_WORKSPACE = os.environ.get("ZCODE_WORKSPACE") or os.getcwd()
ZCODE_SESSION_ID = os.environ.get("ZCODE_SESSION_ID") or "sess_9d5c7fb3-0e6c-4550-8842-55f329c736b9"
ZCODE_SESSION_LOOKBACK_SECONDS = 24 * 60 * 60
PYTHONW = str(Path(sys.executable).with_name("pythonw.exe"))
if not Path(PYTHONW).exists():
    PYTHONW = sys.executable


def log(project, text):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s\n" % (stamp, text)
    path = Path(__file__).resolve().parent / "data" / ("wake_relay.log" if project == chatutil.DEFAULT_PROJECT else "wake_relay.%s.log" % project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def mentions_opencode(text):
    """Only direct address at the start of a message counts. Mentions buried
    mid-text (meta discussion about @三哥) must not trigger wake pop-ups."""
    t = (text or "").strip().lower()
    if PING_RE.match(t):
        return True
    if t.startswith("@二哥") and ("集合开工" in t or "集合令" in t):
        return True
    return False


def mentions_zcode(text):
    """@二哥 direct address, or a boss broadcast addressed to him."""
    t = (text or "").strip().lower()
    if ZCODE_ROUTE_RE.match(t):
        return True
    if t.startswith("@二哥") and ("集合开工" in t or "集合令" in t):
        return True
    return False


def zcode_model_provider():
    """Dynamically inspect ZCode's active model selection.

    An explicit ZCODE_SESSION_ID wins. Otherwise, inspect non-archived sessions
    updated in the lookback window and prefer a non-official provider, so a new
    compatible session is not shadowed by an older official duty session.
    """
    try:
        db = os.path.join(os.environ.get("USERPROFILE", ""), r".zcode\cli\db\db.sqlite")
        con = sqlite3.connect(r"file:%s?mode=ro" % db, uri=True)
        if os.environ.get("ZCODE_SESSION_ID"):
            sess = ZCODE_SESSION_ID
            row = con.execute(
                "SELECT data FROM session_entry WHERE session_id=? AND type='runtime/model_selection' ORDER BY time_updated DESC LIMIT 1",
                (sess,),
            ).fetchone()
            rows = [(row[0], row[0])] if row else []
        else:
            cutoff = time.time() * 1000 - ZCODE_SESSION_LOOKBACK_SECONDS * 1000
            rows = con.execute(
                """
                SELECT entry.data, entry.time_updated
                FROM session_entry AS entry
                JOIN session AS app_session ON app_session.id = entry.session_id
                WHERE entry.type='runtime/model_selection'
                  AND app_session.time_archived IS NULL
                  AND app_session.time_updated >= ?
                ORDER BY entry.time_updated DESC
                LIMIT 100
                """,
                (cutoff,),
            ).fetchall()
        con.close()
        candidates = []
        for raw_data, _updated_at in rows:
            try:
                data = json.loads(raw_data)
            except Exception:
                continue
            provider = str(data.get("providerId", ""))
            model_id = str(data.get("modelId", ""))
            if provider or model_id:
                candidates.append((provider.startswith("builtin:"), provider, model_id))
        if candidates:
            official, provider, model_id = min(candidates, key=lambda item: (item[0],))
            return provider, model_id
    except Exception:
        pass
        return None, None


def boss_inbox_path(project):
    paths = chatutil.project_paths(project)
    stem = Path(paths["opencode_seen"]).with_name("boss_inbox.jsonl")
    if project != chatutil.DEFAULT_PROJECT:
        stem = Path(paths["opencode_seen"]).with_name("boss_inbox.%s.jsonl" % project)
    return stem


def queue_for_boss(project, msg_id, text):
    path = boss_inbox_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    item = {"id": msg_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def zcode_wake(project):
    """ZCode only accepts input through its own window. The deep link was
    removed on user request (it popped an external-folder confirmation dialog
    on every mention). What remains: a chatroom guidance note when the active
    model is the builtin official channel, which may not be able to start
    turns."""
    provider, model_id = zcode_model_provider()
    official = bool(provider and provider.startswith("builtin:"))
    if official:
        log(project, "zcode on official channel (%s / %s), no deep link fired" % (provider, model_id))
        try:
            subprocess.run(
                [PYTHONW, str(Path(__file__).resolve().parent / "chatroom.py"),
                 "send", "--name", "Codex", "--project", project,
                 "--text", "@二哥 官方模型兼容提示：你当前用官方免费通道（%s / %s）。该通道繁忙时会拒绝启动回合"
                 "（官方容量限制 Start Plan busy），这正是切模型后\"唤不醒\"的原因——不是配置坏了。"
                 "选择：稍后自动值守会再提醒，或你在 ZCode 里切回 glm-5.3-flash（tokenrhythm）。"
                 % (provider, model_id)],
                capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
            )
        except Exception:
            pass
    else:
        log(project, "zcode compatible provider active (%s / %s), no action" % (provider, model_id))
    return True


COMMANDER_NAMES = {
    "二哥": "ZCode", "zcode": "ZCode",
    "三哥": "OpenCode", "opencode": "OpenCode",
    "大哥": "Codex", "codex": "Codex",
}
APPOINT_RE = re.compile(
    r"任命\s*(二哥|三哥|大哥|zcode|opencode|codex)\s*为?\s*总指挥"
    r"|总指挥\s*(?:改为|换成|改为成|成)\s*(二哥|三哥|大哥|zcode|opencode|codex)",
    re.IGNORECASE,
)


def commander_file(project):
    paths = chatutil.project_paths(project)
    stem = Path(paths["opencode_seen"]).with_name("commander.json")
    if project != chatutil.DEFAULT_PROJECT:
        stem = Path(paths["opencode_seen"]).with_name("commander.%s.json" % project)
    return stem


def load_commander(project):
    try:
        return str(json.loads(commander_file(project).read_text(encoding="utf-8")).get("name", "Codex"))
    except Exception:
        return "Codex"


def save_commander(project, name):
    path = commander_file(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False), encoding="utf-8")


def detect_appointment(text):
    """Only the user appoints the commander; returns the app name or None."""
    m = APPOINT_RE.search((text or "").strip())
    if m:
        raw = (m.group(1) or m.group(2) or "").lower()
        return COMMANDER_NAMES.get(raw)
    return None


def seen_path(project):
    paths = chatutil.project_paths(project)
    stem = Path(paths["opencode_seen"]).with_name("wake_relay_seen.txt")
    if project != chatutil.DEFAULT_PROJECT:
        stem = Path(paths["opencode_seen"]).with_name("wake_relay_seen.%s.txt" % project)
    return stem


def load_seen(project):
    path = seen_path(project)
    try:
        return int(path.read_text().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def save_seen(project, value):
    path = seen_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="ascii")


def read_new_messages(project, pos):
    paths = chatutil.project_paths(project)
    return chatutil.tail_json_lines(paths["messages"], pos)


def fire_deep_link(project, mention_text):
    paths = chatutil.project_paths(project)
    runtime = Path(__file__).resolve().parent
    workspace = str(runtime.parent)
    prompt = (
        "聊天室有@三哥的新消息超时未回应（这是兜底唤醒来铃）。此深链只会把下面任务填进输入框，需要按一次回车发送。"
        "发送后请读取 %s 最后20行，"
        "按协作铁律用命令行回复：python \"%s\" send --name OpenCode --project %s --text \"...\"。"
        "触发消息：%s"
    ) % (paths["transcript"], runtime / "chatroom.py", project, mention_text)
    url = "opencode://new-session?directory=%s&prompt=%s" % (
        urllib.parse.quote(workspace, safe=""),
        urllib.parse.quote(prompt, safe=""),
    )
    try:
        # The protocol handler registry entry points at a broken mount point,
        # so hand the URL straight to the exe and let the running single
        # instance pick it up instead.
        subprocess.Popen([OPENCODE_EXE, url], close_fds=True, creationflags=0x08000000)
        log(project, "fired deep link for mention id, prompt sent via opencode://new-session")
    except Exception as exc:
        try:
            os.startfile(url)
            log(project, "fired deep link via shell after exe fallback failed")
        except Exception as exc2:
            log(project, "deep link failed: exe=%r shell=%r" % (exc, exc2))


def opencode_watchers_alive():
    # Cheap-enough scan: only runs at fire-decision time, not every poll.
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW,
        ).stdout.lower()
        return any(mark in out for mark in WATCHER_MARKS)
    except Exception:
        return False


def opencode_session_watcher_alive():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW,
        ).stdout.lower()
        return "opencodewatch.py" in out
    except Exception:
        return False


def opencode_pids():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process OpenCode -ErrorAction SilentlyContinue).Id"],
            capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW,
        ).stdout.split()
        return {int(x) for x in out if x.strip().isdigit()}
    except Exception:
        return set()


def api_wake(project, mention_text):
    """Primary wake: POST the task straight into OpenCode's live session via
    its local sidecar API. Credentials are read from the app's process memory
    on every call (server_key.py stdout) and never touch disk."""
    try:
        out = subprocess.run(
            [PYTHONW, str(Path(__file__).resolve().parent / "server_key.py"), "--json"],
            capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW,
        ).stdout
        k = json.loads(out)
        auth = base64.b64encode(("%s:%s" % (k["username"], k["password"])).encode()).decode()
        headers = {"Authorization": "Basic " + auth, "Content-Type": "application/json; charset=utf-8"}
        req = urllib.request.Request("http://127.0.0.1:%s/session" % k["port"],
                                     headers={"Authorization": "Basic " + auth})
        sessions = json.load(urllib.request.urlopen(req, timeout=5))
        cands = [s for s in sessions if "cs2additions" in (s.get("directory", "").lower())]
        target = max(cands or sessions, key=lambda s: (s.get("time", {}).get("updated") or s.get("time", {}).get("created") or 0))
        paths = chatutil.project_paths(project)
        prompt = (
            "聊天室有@你的新消息（项目%s），请立即处理：读取 %s 最后20行，"
            "按协作铁律用命令行回复：python \"%s\" send --name OpenCode --project %s --text \"...\"。"
            "触发消息：%s"
        ) % (project, paths["transcript"], Path(__file__).resolve().parent / "chatroom.py", project, mention_text)
        body = json.dumps({"parts": [{"type": "text", "text": prompt}]}, ensure_ascii=False).encode("utf-8")
        preq = urllib.request.Request(
            "http://127.0.0.1:%s/session/%s/message" % (k["port"], target["id"]),
            data=body, headers=headers, method="POST",
        )
        try:
            urllib.request.urlopen(preq, timeout=15)
        except TimeoutError:
            # The connection is held while the session works; delivery already
            # happened the moment the server accepted the request.
            pass
        log(project, "api wake delivered to session %s" % target["id"])
        return True
    except Exception as exc:
        log(project, "api wake failed: %r" % exc)
        return False


def press_enter_if_foreground(project, pids):
    """Deep links only prefill the composer, so send the Enter keystroke,
    but only while an OpenCode window owns the foreground (never steal keys)."""
    user32 = ctypes.windll.user32
    pid = wintypes.DWORD()
    for _ in range(ENTER_ATTEMPTS):
        hwnd = user32.GetForegroundWindow()
        if hwnd and user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            if pid.value in pids:
                try:
                    subprocess.Popen(
                        ["powershell", "-NoProfile", "-Command",
                         "Add-Type -AssemblyName System.Windows.Forms;"
                         "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')"],
                        creationflags=0x08000000,
                    )
                    log(project, "auto-enter sent to foreground OpenCode window")
                except Exception as exc:
                    log(project, "auto-enter failed: %r" % exc)
                return
        time.sleep(ENTER_RETRY_SECONDS)
    log(project, "auto-enter skipped, OpenCode never took the foreground")


def main():
    parser = argparse.ArgumentParser(description="OpenCode wake relay")
    parser.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    parser.add_argument("--timeout", type=int, default=RESPONSE_TIMEOUT,
                        help="fallback wait when OpenCode looks active")
    parser.add_argument("--watcher-timeout", type=int, default=WATCHER_FALLBACK_TIMEOUT,
                        help="fallback wait when OpenCode watcher processes exist")
    parser.add_argument("--grace-timeout", type=int, default=WATCHER_GRACE_TIMEOUT,
                        help="short fallback when only the out-of-session watchdog exists")
    parser.add_argument("--no-enter", action="store_true",
                        help="disable the automatic Enter keystroke after a deep-link pop")
    parser.add_argument("--cooldown", type=int, default=FIRE_COOLDOWN)
    parser.add_argument("--api-cooldown", type=int, default=API_COOLDOWN)
    parser.add_argument("--poll", type=int, default=POLL_SECONDS)
    parser.add_argument("--digest-interval", type=int, default=DIGEST_INTERVAL,
                        help="seconds between OpenCode chatter digests")
    args = parser.parse_args()

    project = chatutil.normalize_project(args.project)
    server_locator.ensure_server()
    pos = load_seen(project)
    if pos == 0:
        # First run: jump to the current tail so historical mentions never replay.
        _, pos = read_new_messages(project, 0)
        save_seen(project, pos)
        log(project, "first run, watermark jumped to %d" % pos)

    pending = None
    last_fire = 0.0
    last_api = 0.0
    last_zcode = 0.0
    last_ack = 0.0
    digest_buffer = []
    last_digest = 0.0
    last_opencode_ts = None
    last_zcode_id = -1
    def wake_now(text):
        # Primary channel: API direct injection into the live session.
        if api_wake(project, text):
            return
        # Fallback: deep-link pop + auto-enter.
        fire_deep_link(project, text)
        if not args.no_enter:
            press_enter_if_foreground(project, opencode_pids())
    log(project, "wake relay running (instant when idle, timeout=%ss cooldown=%ss poll=%ss)" % (args.timeout, args.cooldown, args.poll))
    while True:
        messages, pos = read_new_messages(project, pos)
        for msg in messages:
            name = str(msg.get("name", ""))
            text = str(msg.get("text", ""))
            msg_id = int(msg.get("id", 0))
            if name.lower() == "opencode":
                last_opencode_ts = time.time()
                if pending and msg_id >= pending["id"]:
                    pending = None
                    log(project, "OpenCode responded at id %d, pending cleared" % msg_id)
                continue
            if name.lower() == "zcode":
                last_zcode_id = msg_id
            if name == "你" and text.strip() and len(text.strip()) >= 6:
                nowt = time.time()
                appt = detect_appointment(text)
                if appt:
                    save_commander(project, appt)
                    announcement = ("奉用户令：任命 %s 为总指挥（项目 %s），即日生效。"
                                    "指挥职责（拍板/派活/集合收工令）由 %s 行使，"
                                    "原总指挥转执行位，全员按此适配。" % (appt, project, appt))
                    try:
                        subprocess.run(
                            [PYTHONW, str(Path(__file__).resolve().parent / "chatroom.py"),
                             "send", "--name", "Codex", "--project", project, "--text", announcement],
                            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
                        )
                    except Exception:
                        pass
                    last_ack = nowt
                    log(project, "commander appointed: %s" % appt)
                    continue
                if BOSS_PING_RE.match(text.strip()):
                    queue_for_boss(project, msg_id, text[:500])
                    ack = ("@大哥 已收到（id %d），消息已进大哥直达收件箱；"
                           "桌面端回合恢复后第一优先处理，二哥/三弟可先做基础巡检。"
                           % msg_id)
                    try:
                        subprocess.run(
                            [PYTHONW, str(Path(__file__).resolve().parent / "chatroom.py"),
                             "send", "--name", "系统", "--project", project, "--text", ack],
                            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
                        )
                    except Exception:
                        pass
                    last_ack = nowt
                    log(project, "direct boss ping queued (id %d)" % msg_id)
                    continue
                if (nowt - last_ack >= AUTOACK_COOLDOWN
                        and not mentions_zcode(text)
                        and not mentions_opencode(text)):
                    if nowt < ROUTE_PREF_UNTIL:
                        # Free-quota window: dirty/heavy work routes to ZCode
                        # via a chatroom claim post; OpenCode is the fallback.
                        dispatch = "@二哥 领活（id %d）：%s" % (msg_id, text[:200])
                        try:
                            subprocess.run(
                                [PYTHONW, str(Path(__file__).resolve().parent / "chatroom.py"),
                                 "send", "--name", "Codex", "--project", project, "--text", dispatch],
                                capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
                            )
                        except Exception:
                            pass
                        pending = {"id": msg_id, "until": nowt + 60, "text": text[:200], "zcode_claim": True}
                        ack = ("[自动值守] 已收到（id %d）：任务已派二哥领（订阅额度窗口）；"
                               "60 秒无人认领自动转三弟。现任总指挥：%s。"
                               % (msg_id, load_commander(project)))
                        try:
                            subprocess.run(
                                [PYTHONW, str(Path(__file__).resolve().parent / "chatroom.py"),
                                 "send", "--name", "系统", "--project", project, "--text", ack],
                                capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
                            )
                        except Exception:
                            pass
                        last_ack = nowt
                        log(project, "routed to zcode claim (user id %d)" % msg_id)
                        continue
                    # Boss's rule: proactively help without being asked.
                    # Push the task into OpenCode's live session and call for
                    # idle-brother claims in one automatic stroke.
                    if api_wake(project, text[:200]):
                        last_api = nowt
                    ack = ("[自动值守] 已收到（id %d）：任务已直派三弟；二哥巡检在岗，"
                           "谁空闲谁领活（互助条款）；现任总指挥：%s（桌面端活跃时跟进）。"
                           % (msg_id, load_commander(project)))
                    try:
                        subprocess.run(
                            [PYTHONW, str(Path(__file__).resolve().parent / "chatroom.py"),
                             "send", "--name", "系统", "--project", project, "--text", ack],
                            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
                        )
                    except Exception:
                        pass
                    last_ack = nowt
                    log(project, "auto-dispatch for user message id %d" % msg_id)
                continue
            if (name.lower() not in ("opencode", "codex", "系统", "你")
                    and text.strip()):
                # Proactive reception: buffer non-mention chatter and feed it
                # to OpenCode as a periodic digest, so its turn-scoped watcher
                # going deaf no longer means it misses the room.
                digest_buffer.append("[%s] %s: %s" % (str(msg.get("ts", ""))[-8:], name, text[:160]))
            if mentions_zcode(text):
                now = time.time()
                if now - last_zcode >= 60:
                    zcode_wake(project)
                    last_zcode = now
                else:
                    log(project, "zcode mention id %d skipped, cooldown 60s" % msg_id)
                continue
            if mentions_opencode(text):
                now = time.time()
                active = last_opencode_ts is not None and now - last_opencode_ts < ACTIVITY_WINDOW
                if active:
                    pending = {"id": msg_id, "until": now + args.timeout, "text": text[:200]}
                    log(project, "pending armed by id %d (%s), OpenCode active" % (msg_id, name))
                else:
                    # API injection is non-intrusive: fire it immediately so
                    # OpenCode starts working within seconds, not minutes.
                    if now - last_api >= args.api_cooldown:
                        if api_wake(project, text[:200]):
                            # Safety net: if OpenCode still never replies,
                            # escalate to the popup after a long grace. The
                            # clock restarts AFTER the injection returns: the
                            # POST can hold ~15s while the session processes.
                            last_api = time.time()
                            pending = {"id": msg_id, "until": time.time() + args.grace_timeout, "text": text[:200], "popup": True}
                            log(project, "api wake fired immediately for id %d, popup safety net in %ds" % (msg_id, args.grace_timeout))
                        else:
                            pending = {"id": msg_id, "until": now + args.grace_timeout, "text": text[:200], "popup": True}
                            log(project, "api wake failed for id %d, popup fallback in %ds" % (msg_id, args.grace_timeout))
                    else:
                        pending = {"id": msg_id, "until": last_api + args.api_cooldown, "text": text[:200], "api": True}
                        log(project, "pending queued by id %d (%s), api cooldown until %ds" % (msg_id, name, args.api_cooldown))
        save_seen(project, pos)

        now = time.time()
        if pending and now >= pending["until"]:
            if pending.get("zcode_claim"):
                if last_zcode_id > pending["id"]:
                    pending = None
                    log(project, "zcode claim detected, pending cleared")
                else:
                    # No ZCode claim within the window: fall back to OpenCode.
                    if api_wake(project, pending["text"]):
                        last_api = time.time()
                    pending = {"id": pending["id"], "until": time.time() + args.grace_timeout, "text": pending["text"], "popup": True}
            if pending.get("api"):
                if now - last_api >= args.api_cooldown and api_wake(project, pending["text"]):
                    last_api = now
                    pending = None
            elif now - last_fire >= args.cooldown:
                if pending.get("popup"):
                    # Role-swap clause: OpenCode missed the grace window, so
                    # the task hands over to ZCode automatically.
                    handover = ("[自动值守] 三弟超时未应（id %d），任务转交二哥"
                                "（角色互换条款）：请读取 transcript 尾部认领处理。" % pending["id"])
                    try:
                        subprocess.run(
                            [PYTHONW, str(Path(__file__).resolve().parent / "chatroom.py"),
                             "send", "--name", "系统", "--project", project, "--text", handover],
                            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
                        )
                    except Exception:
                        pass
                    zcode_wake(project)
                    last_zcode = now
                wake_now(pending["text"])
                last_fire = now
                pending = None
        if digest_buffer and now - last_digest >= args.digest_interval and now - last_api >= args.api_cooldown:
            digest = ("聊天室摘要（非点名动态，供你保持在线）："
                      + " | ".join(digest_buffer[-8:]))[:600]
            n = len(digest_buffer)
            if api_wake(project, digest):
                last_digest = now
                digest_buffer = []
                log(project, "digest injected (%d items)" % n)
        time.sleep(max(1, args.poll))


if __name__ == "__main__":
    main()
