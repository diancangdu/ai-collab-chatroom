#!/usr/bin/env python3
"""Hard startup contract: locate and connect to the communication server."""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
SERVICE_STATE_PATH = Path(__file__).resolve().parent / "data" / "background_service.json"
STARTER_PATH = Path(__file__).resolve().parent / "background_service.py"
DEFAULT_PORT = 8787
CREATE_NO_WINDOW = 0x08000000


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def server_url(port=None):
    return "http://127.0.0.1:%s/api/health" % (port or load_config().get("port") or DEFAULT_PORT)


def server_alive(port=None):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(server_url(port), timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def supervisor_running():
    try:
        state = json.loads(SERVICE_STATE_PATH.read_text(encoding="utf-8"))
        pid = int(state.get("pid") or 0)
        return bool(pid) and (os.name != "nt" or _pid_exists(pid))
    except Exception:
        return False


def _pid_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _start_service():
    python = Path(sys.executable).with_name("pythonw.exe")
    if not python.exists():
        python = Path(sys.executable)
    subprocess.Popen(
        [str(python), str(STARTER_PATH), "start"],
        cwd=str(ROOT),
        creationflags=CREATE_NO_WINDOW,
    )


def ensure_server(timeout=20.0):
    """Return the server URL after the hard-rule auto-connection succeeds."""
    port = load_config().get("port") or DEFAULT_PORT
    deadline = time.time() + timeout
    child_of_supervisor = os.environ.get("AICOLLAB_CHATROOM_CHILD") == "1"
    if not server_alive(port) and not supervisor_running() and not child_of_supervisor:
        _start_service()
    while time.time() < deadline:
        if server_alive(port):
            return server_url(port)
        time.sleep(0.25)
    raise RuntimeError("communication server not reachable: %s" % server_url(port))
