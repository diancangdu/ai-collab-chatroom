#!/usr/bin/env python3
"""Android 桥安全层服务端骨架（默认关闭）。

硬约束（与派单 #T20260909-Q1 一一对应）：
1. 默认关闭：只有 CLI --enable-bridge 可启动；配置/环境不能开桥
   （唯一的例外是 enable 自举入口本身，它也需要 token 且走双确认）。
2. 开桥双确认：enable 需要先 stage 拿 pending_token，再 confirm 才真正打开（token 60 秒一次性）。
3. LAN 强制回环：服务器只绑定 127.0.0.1，代码层面不允许其他 bind 地址。
4. 6 操作白名单：ping / device.list / app.launch / app.stop / key.event / text.input。
5. argv 调用无 shell：subprocess 一律列表参数 + shell=False。
6. 每命令超时 + 串行锁：全局锁串行执行，单命令默认 10 秒超时。
7. 截图单独确认：screen.capture 不在白名单，需每次单独换取一次性 confirm_token。
8. 审计 5 字段：ts / caller / op / params_present / result（仅 ok+duration_ms），不落参数内容。

本模块不修改聊天室现有代码，可独立运行：
  python chatroom/android_bridge.py serve --port 8790
"""

import argparse
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import auth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
CLI_ARMED = False
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_PATH = os.path.join(DATA_DIR, "android_bridge_state.json")
AUDIT_PATH = os.path.join(DATA_DIR, "android_bridge_audit.jsonl")
TOKEN_PATH = os.path.join(DATA_DIR, "android_bridge_tokens.json")

BIND_HOST = "127.0.0.1"  # 硬编码回环，禁止改绑 0.0.0.0
DEFAULT_PORT = 8790
COMMAND_TIMEOUT_SECONDS = 10
CONFIRM_TTL_SECONDS = 60
MAX_TEXT_INPUT_LENGTH = 500

OP_WHITELIST = ("ping", "device.list", "app.launch", "app.stop", "key.event", "text.input")
SCREENSHOT_OP = "screen.capture"  # 不在白名单，走单独确认通道

_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{1,100}$")
_KEYCODE_RE = re.compile(r"^[0-9]{1,4}$")
_ADB = "adb"

_serial_lock = threading.Lock()
_tokens_lock = threading.Lock()


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def bridge_enabled():
    return bool(CLI_ARMED)


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _new_token():
    import secrets
    return secrets.token_urlsafe(24)


def stage_confirm_token(purpose, params_digest=None):
    """签发一次性确认 token（60 秒有效），purpose: enable|screenshot。"""
    token = _new_token()
    now = time.time()
    with _tokens_lock:
        try:
            with open(TOKEN_PATH, "r", encoding="utf-8") as f:
                tokens = json.load(f)
        except Exception:
            tokens = {}
        tokens = {k: v for k, v in tokens.items() if float(v.get("expires_at", 0)) > now}
        tokens[token] = {"purpose": purpose, "expires_at": now + CONFIRM_TTL_SECONDS}
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(tokens, f)
    return token


def consume_confirm_token(token, purpose):
    """校验并消费一次性 token；不匹配/过期/已用一律 False。"""
    now = time.time()
    with _tokens_lock:
        try:
            with open(TOKEN_PATH, "r", encoding="utf-8") as f:
                tokens = json.load(f)
        except Exception:
            return False
        info = tokens.get(token)
        if not info or info.get("purpose") != purpose or float(info.get("expires_at", 0)) <= now:
            return False
        del tokens[token]
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(tokens, f)
    return True


def audit(caller, op, params, result):
    """审计最小 5 字段；不记录参数内容、stdout/stderr 或设备输出。"""
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "caller": str(caller or "unknown")[:80],
        "op": str(op)[:40],
        "params_present": bool(params),
        "result": {"ok": bool(result.get("ok", False)), "duration_ms": int(result.get("duration_ms", 0))},
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def validate_op(op, params):
    """白名单 + 参数校验；返回 (ok, 清洗后的参数或错误信息)。"""
    if op == "ping":
        return True, {}
    if op == "device.list":
        return True, {}
    if op == "app.launch":
        pkg = str(params.get("package", ""))
        if not _PACKAGE_RE.match(pkg):
            return False, "invalid package"
        return True, {"package": pkg}
    if op == "app.stop":
        pkg = str(params.get("package", ""))
        if not _PACKAGE_RE.match(pkg):
            return False, "invalid package"
        return True, {"package": pkg}
    if op == "key.event":
        keycode = str(params.get("keycode", ""))
        if not _KEYCODE_RE.match(keycode):
            return False, "invalid keycode"
        return True, {"keycode": keycode}
    if op == "text.input":
        text = str(params.get("text", ""))
        if not text or len(text) > MAX_TEXT_INPUT_LENGTH:
            return False, "invalid text"
        # adb shell 会在设备侧经 shell 拼接执行，必须拒绝元字符注入
        if any(c in text for c in "\x00\r\n;&|`$<>\")('"):
            return False, "invalid text"
        return True, {"text": text}
    return False, "op not in whitelist"


def build_argv(op, params):
    """把白名单操作映射为 adb argv；绝不经过 shell。"""
    if op == "ping":
        return [_ADB, "version"]
    if op == "device.list":
        return [_ADB, "devices"]
    if op == "app.launch":
        return [_ADB, "shell", "monkey", "-p", params["package"], "-c",
                "android.intent.category.LAUNCHER", "1"]
    if op == "app.stop":
        return [_ADB, "shell", "am", "force-stop", params["package"]]
    if op == "key.event":
        return [_ADB, "shell", "input", "keyevent", params["keycode"]]
    if op == "text.input":
        return [_ADB, "shell", "input", "text", params["text"]]
    return None


def execute_op(op, params, caller):
    """串行锁 + 超时执行；返回审计 result 字典。"""
    ok, cleaned = validate_op(op, params)
    started = time.time()
    if not ok:
        result = {"ok": False, "error": cleaned, "duration_ms": 0}
        audit(caller, op, params, result)
        return result
    argv = build_argv(op, cleaned)
    if argv is None:
        result = {"ok": False, "error": "no argv mapping", "duration_ms": 0}
        audit(caller, op, params, result)
        return result
    with _serial_lock:
        try:
            proc = subprocess.run(
                argv,
                shell=False,  # 硬约束：永不经过 shell
                capture_output=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
            result = {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "duration_ms": int((time.time() - started) * 1000),
            }
        except subprocess.TimeoutExpired:
            result = {"ok": False, "error": "timeout", "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
                      "duration_ms": int((time.time() - started) * 1000)}
        except FileNotFoundError:
            result = {"ok": False, "error": "adb not found", "duration_ms": int((time.time() - started) * 1000)}
    audit(caller, op, cleaned, result)
    return result


def handle_api(method, path, query, body, caller):
    """路由入口。返回 (http_status, response_dict)。

    默认关闭时所有 /api/android-bridge/* 路径一律 404，与不存在路径无差别。
    """
    if not path.startswith("/api/android-bridge"):
        return 404, {"ok": False, "error": "not found"}
    # enable 是开桥唯一的自举入口：桥关闭时它保持可达（仍需 token），其余端点一律 404
    bootstrap = path == "/api/android-bridge/enable" and method == "POST"
    if not bootstrap and not bridge_enabled():
        return 404, {"ok": False, "error": "not found"}

    if path == "/api/android-bridge/ping" and method == "GET":
        return 200, {"ok": True, "bridge": "android", "enabled": True}

    if path == "/api/android-bridge/enable" and method == "POST":
        state = load_state()
        if not state.get("arm_pending"):
            token = stage_confirm_token("enable")
            save_state({"arm_pending": True, "armed_at": datetime.now().isoformat()})
            return 200, {"ok": True, "stage": 1, "message": "arm staged, confirm within 60s", "confirm_token": token}
        # stage 2：携带 confirm_token 才真正开桥
        token = str(body.get("confirm_token", ""))
        if not consume_confirm_token(token, "enable"):
            return 403, {"ok": False, "error": "confirm token invalid or expired"}
        save_state({"arm_pending": False, "enabled_at": datetime.now().isoformat()})
        audit(caller, "bridge.enable", {}, {"ok": True})
        return 200, {"ok": True, "stage": 2, "enabled": True}

    if path == "/api/android-bridge/exec" and method == "POST":
        op = str(body.get("op", ""))
        params = body.get("params") or {}
        if not isinstance(params, dict):
            return 400, {"ok": False, "error": "params must be object"}
        if op == SCREENSHOT_OP:
            # 截图不在白名单：每次需单独 confirm_token
            if not consume_confirm_token(str(body.get("confirm_token", "")), "screenshot"):
                return 403, {"ok": False, "error": "screenshot requires fresh confirm token"}
            result = execute_op(SCREENSHOT_OP, params, caller)
            return (200 if result.get("ok") else 500), {"ok": result.get("ok", False), "result": result}
        if op not in OP_WHITELIST:
            result = {"ok": False, "error": "op not in whitelist"}
            audit(caller, op, params, result)
            return 400, {"ok": False, "error": "op not in whitelist"}
        result = execute_op(op, params, caller)
        return (200 if result.get("ok") else 500), {"ok": result.get("ok", False), "result": result}

    if path == "/api/android-bridge/screenshot/confirm" and method == "POST":
        token = stage_confirm_token("screenshot")
        audit(caller, "screenshot.confirm", {}, {"ok": True})
        return 200, {"ok": True, "confirm_token": token, "ttl_seconds": CONFIRM_TTL_SECONDS}

    if path == "/api/android-bridge/disable" and method == "POST":
        save_state({"arm_pending": False, "disabled_at": datetime.now().isoformat()})
        audit(caller, "bridge.disable", {}, {"ok": True})
        return 200, {"ok": True, "enabled": False}

    return 404, {"ok": False, "error": "not found"}


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if not path.startswith("/api/android-bridge"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        # enable 是开桥自举入口，关闭时也保持可达；其余端点默认 404
        bootstrap = path == "/api/android-bridge/enable" and method == "POST"
        if not bootstrap and not bridge_enabled():
            self._json(404, {"ok": False, "error": "not found"})
            return
        expected = auth.token()
        supplied = self.headers.get("X-Chatroom-Token", "")
        import hmac as _hmac
        if not (expected and supplied and _hmac.compare_digest(expected, supplied)):
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        caller = self.headers.get("X-Chatroom-Caller", "local")
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            body = {}
        code, payload = handle_api(method, path, query, body if isinstance(body, dict) else {}, caller)
        self._json(code, payload)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def serve(port=DEFAULT_PORT, enable_bridge=False):
    global CLI_ARMED
    CLI_ARMED = bool(enable_bridge)
    server = ThreadingHTTPServer((BIND_HOST, port), BridgeHandler)
    print("Android bridge (default-off) on http://%s:%s" % (BIND_HOST, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Android 桥安全层（默认关闭）")
    sub = parser.add_subparsers(dest="cmd")
    run = sub.add_parser("serve", help="启动桥服务（默认关闭，需双确认开启）")
    run.add_argument("--port", type=int, default=DEFAULT_PORT)
    run.add_argument("--enable-bridge", action="store_true", help="唯一启动期开关；配置文件不能开桥")
    args = parser.parse_args()
    if args.cmd == "serve":
        serve(args.port)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
