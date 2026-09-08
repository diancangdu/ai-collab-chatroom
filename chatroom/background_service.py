#!/usr/bin/env python3
"""Hidden supervisor for the sibling communication service."""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent / "data"
STATE_PATH = DATA_DIR / "background_service.json"
CONTROL_PATH = DATA_DIR / "background_service_control.json"
CHATROOM = Path(__file__).resolve().parent / "chatroom.py"
MONITOR = Path(__file__).resolve().parent / "monitor.py"
WAKE_RELAY = Path(__file__).resolve().parent / "wake_relay.py"
THIRD_WATCH = Path(__file__).resolve().parent / "third_watch.py"
ZCODE_WATCH = Path(__file__).resolve().parent / "zcode_watch.py"
QODER_WATCH = Path(__file__).resolve().parent / "qoder_watch.py"
WORKLOAD = Path(__file__).resolve().parent / "workload.py"
CREATE_NO_WINDOW = 0x08000000


def load_config():
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(value):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def server_alive(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:%s/api/health" % port, timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def hidden_python():
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def launch(script, args):
    python = hidden_python()
    command = [python, str(script)] + [str(item) for item in args]
    environment = dict(os.environ)
    environment["AICOLLAB_CHATROOM_CHILD"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parent),
        env=environment,
        creationflags=CREATE_NO_WINDOW,
    )
    return {
        "name": Path(script).stem,
        "pid": process.pid,
        "command": command,
    }


def start_app(path):
    if not path or not Path(path).exists():
        return None
    try:
        name = Path(path).name
        check = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq %s" % name],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        if name.lower() in (check.stdout or "").lower():
            return None
        args = [str(path)]
        if "qoder" in name.lower():
            args.append("--remote-debugging-port=9223")
        subprocess.Popen(args, creationflags=CREATE_NO_WINDOW)
        return str(path)
    except Exception:
        return None


def configured_apps(config):
    return {
        "Codex": str(config.get("codex_app") or ""),
        "ZCode": str(config.get("zcode_app") or ""),
        "OpenCode": str(config.get("opencode_app") or ""),
        "Qoder": str(config.get("qoder_app") or ""),
    }


def start():
    config = load_config()
    port = int(config.get("port") or 8787)
    project = str(config.get("project") or "AllAgentStudy")
    state = load_state()
    if state.get("server_pid") and pid_alive(state["server_pid"]) and server_alive(port):
        print("background service already running")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.unlink(missing_ok=True)
    CONTROL_PATH.unlink(missing_ok=True)

    children = []
    children.append(launch(CHATROOM, ["server", "--port", port]))
    children.append(launch(MONITOR, ["--project", project, "--no-auto-release"]))
    children.append(launch(WAKE_RELAY, ["--project", project]))
    children.append(launch(THIRD_WATCH, ["--project", project]))
    children.append(launch(ZCODE_WATCH, ["--project", project]))
    children.append(launch(QODER_WATCH, ["--project", project]))
    children.append(launch(WORKLOAD, ["watch", "--project", project]))

    apps = configured_apps(config)
    started_apps = []
    if config.get("auto_start_apps"):
        for name in ("Codex", "ZCode", "OpenCode", "Qoder"):
            result = start_app(apps[name])
            if result:
                started_apps.append(result)

    state = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pid": os.getpid(),
        "server_pid": children[0]["pid"],
        "project": project,
        "port": port,
        "communication_root": str(config.get("communication_root") or ROOT),
        "server_url": str(config.get("server_url") or ("http://127.0.0.1:%s" % port)),
        "auto_connect_on_start": bool(config.get("auto_connect_on_start", True)),
        "auto_release": False,
        "children": children,
        "configured_apps": apps,
        "started_apps": started_apps,
    }
    write_state(state)
    print("background service started")

    server_process = None
    while True:
        if CONTROL_PATH.exists():
            break
        state = load_state()
        if not state.get("server_pid") or not server_alive(int(state.get("port") or port)):
            break
        time.sleep(1)

    for child in children:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(child["pid"]), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass
    CONTROL_PATH.unlink(missing_ok=True)
    STATE_PATH.unlink(missing_ok=True)


def stop():
    state = load_state()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTROL_PATH.write_text(
        json.dumps({"action": "shutdown", "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S")}),
        encoding="utf-8",
    )
    if state.get("server_pid") and not pid_alive(state["server_pid"]):
        CONTROL_PATH.unlink(missing_ok=True)
        STATE_PATH.unlink(missing_ok=True)
    print("shutdown requested")


def status():
    state = load_state()
    port = int(state.get("port") or load_config().get("port") or 8787)
    alive = server_alive(port)
    print(json.dumps({
        "ok": True,
        "running": alive,
        "state": state,
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status"), nargs="?", default="status")
    args = parser.parse_args()
    if args.action == "start":
        start()
    elif args.action == "stop":
        stop()
    else:
        status()


if __name__ == "__main__":
    main()
