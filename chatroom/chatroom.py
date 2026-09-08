import argparse
import json
import os
import re
import sys
import threading
import time
import mimetypes
import uuid
import hmac
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import chatutil
import auth
import session_registry
import roster
import commander
import workload

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
SERVICE_STATE_PATH = os.path.join(BASE_DIR, "data", "background_service.json")
SERVICE_CONTROL_PATH = os.path.join(BASE_DIR, "data", "background_service_control.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
CHANNELS_DIR = os.path.join(DATA_DIR, "channels")
TRANSCRIPT_LIMIT = 2 * 1024 * 1024
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(CHANNELS_DIR, "uploads")
MAX_UPLOAD_SIZE = 8 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
HOST = "127.0.0.1"
PORT = 8787

ROLES = {
    "codex": "boss",
    "大哥": "boss",
    "zcode": "second",
    "二哥": "second",
    "opencode": "third",
    "三哥": "third",
    "user": "user",
    "你": "user",
    "system": "system",
    "系统": "system",
}

ROLE_LABELS = {
    "boss": "大哥 Codex",
    "second": "二哥 ZCode",
    "third": "三弟 OpenCode",
    "user": "你",
    "system": "系统",
}

_lock = threading.Lock()
# 按项目缓存 (文件大小, mtime) -> 消息列表，文件没变时 GET 不再重复读盘
_msg_cache = {}


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)


def load_messages(project=None):
    paths = chatutil.project_paths(project)
    if not os.path.exists(paths["messages"]):
        return []
    out = []
    try:
        with open(paths["messages"], "r", encoding="utf-8") as f:
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
    except Exception:
        pass
    return out


def load_messages_cached(project=None):
    paths = chatutil.project_paths(project)
    key = paths["project"]
    try:
        st = os.stat(paths["messages"])
        sig = (st.st_size, st.st_mtime_ns)
    except Exception:
        sig = None
    with _lock:
        cached = _msg_cache.get(key)
        if cached and cached[0] == sig:
            return cached[1]
        msgs = load_messages(paths["project"])
        _msg_cache[key] = (sig, msgs)
        return msgs


def last_message_id(path):
    """只读文件尾部取最后一条消息 id，避免每次发言都全量读文件。"""
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return 0
        # 从文件尾部倒着找最后一条完整 JSON 行，长消息也不会截断
        with open(path, "rb") as f:
            pos = size
            buf = b""
            while pos > 0:
                chunk = max(0, pos - 65536)
                f.seek(chunk)
                buf = f.read(pos - chunk) + buf
                pos = chunk
                if b"\n" in buf or chunk == 0:
                    break
        lines = buf.splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line.decode("utf-8", "ignore"))
                return int(m.get("id") or 0)
            except Exception:
                continue
    except Exception:
        pass
    return 0


def infer_role(name):
    key = (name or "").strip().lower()
    for k, r in ROLES.items():
        if key == k or key.startswith(k):
            return r
    return "user"


def verify_append(project, msg):
    """读回文件尾部确认消息真的落盘；并发写入时在尾部窗口里找到自己的 id 即可。"""
    paths = chatutil.project_paths(project)
    try:
        size = os.path.getsize(paths["messages"])
        with open(paths["messages"], "rb") as f:
            f.seek(max(0, size - 65536))
            chunk = f.read()
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line.decode("utf-8", "ignore"))
                if int(m.get("id") or 0) == int(msg["id"]):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def safe_image_name(name):
    base = os.path.basename(name or "").strip()
    base = re.sub(r"[^a-zA-Z0-9._\u4e00-\u9fff-]+", "_", base).strip("._-")
    if not base or os.path.splitext(base)[1].lower() not in ALLOWED_IMAGE_EXTENSIONS:
        base = "image.png"
    return base[:120]


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(value):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_service_state():
    try:
        with open(SERVICE_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_upload_bytes(project, filename, body):
    original_name = safe_image_name(filename)
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("unsupported image type")
    if not body:
        raise ValueError("empty image")
    if len(body) > MAX_UPLOAD_SIZE:
        raise ValueError("image too large")
    signatures = {
        ".png": body.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": body.startswith(b"\xff\xd8\xff"),
        ".jpeg": body.startswith(b"\xff\xd8\xff"),
        ".gif": body.startswith(b"GIF87a") or body.startswith(b"GIF89a"),
        ".webp": body[:4] == b"RIFF" and body[8:12] == b"WEBP",
    }
    if not signatures[ext]:
        raise ValueError("invalid image content")
    paths = chatutil.project_paths(project)
    project_name = paths["project"]
    out_dir = os.path.join(UPLOAD_DIR, project_name)
    os.makedirs(out_dir, exist_ok=True)
    stored_name = "%s-%s%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8], ext)
    image_path = os.path.join(out_dir, stored_name)
    with open(image_path, "wb") as f:
        f.write(body)
    return {
        "image": "/uploads/%s/%s" % (project_name, stored_name),
        "image_path": image_path,
        "image_name": original_name,
    }


def append_message(name, text, project=None, image=None, image_path=None, image_name=None):
    text = (text or "").strip()
    name = (name or "").strip() or "你"
    if not text and not image:
        return None
    with _lock:
        paths = chatutil.project_paths(project)
        msg = {
            "id": last_message_id(paths["messages"]) + 1,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "name": name,
            "role": infer_role(name),
            "text": text,
            "project": paths["project"],
        }
        if image:
            msg.update({"image": image, "image_path": image_path, "image_name": image_name})
        with open(paths["messages"], "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        _msg_cache.pop(paths["project"], None)
        rotate_transcript(paths["project"])
        with open(paths["transcript"], "a", encoding="utf-8") as f:
            f.write(transcript_line(msg) + "\n")
        return msg

def transcript_line(msg):
    text = msg.get("text", "")
    if msg.get("image"):
        text += " [图片:%s]" % msg.get("image_name", "image")
    return "[%s] %s: %s" % (msg.get("ts", "?"), msg.get("name", "?"), text)


def rotate_transcript(project=None):
    paths = chatutil.project_paths(project)
    try:
        if os.path.exists(paths["transcript"]) and os.path.getsize(paths["transcript"]) > TRANSCRIPT_LIMIT:
            with open(paths["transcript"], "r", encoding="utf-8") as src:
                data = src.read()
            with open(paths["transcript_old"], "w", encoding="utf-8") as dst:
                dst.write(data)
            with open(paths["transcript"], "w", encoding="utf-8") as f:
                f.write("")
    except Exception:
        pass


def ensure_transcript(project=None):
    paths = chatutil.project_paths(project)
    if os.path.exists(paths["transcript"]):
        return
    try:
        with open(paths["transcript"], "w", encoding="utf-8") as f:
            for m in load_messages(paths["project"]):
                f.write(transcript_line(m) + "\n")
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        name = "index.html" if path in ("/", "/index.html") else "desktop.html" if path == "/desktop" else path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not full.startswith(os.path.abspath(STATIC_DIR) + os.sep) and full != os.path.abspath(STATIC_DIR):
            self.send_error(403)
            return
        if not os.path.isfile(full):
            self.send_error(404)
            return
        if full.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif full.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif full.endswith(".webmanifest"):
            ctype = "application/manifest+json; charset=utf-8"
        elif full.endswith(".svg"):
            ctype = "image/svg+xml"
        else:
            ctype = "text/html; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Type", ctype)
        self.send_header(
            "Set-Cookie",
            "aicollab_token=" + auth.token() + "; Path=/; HttpOnly; SameSite=Strict",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        expected = auth.token()
        supplied = self.headers.get("X-Chatroom-Token", "")
        authorization = self.headers.get("Authorization", "")
        if not supplied and authorization.startswith("Bearer "):
            supplied = authorization[len("Bearer "):].strip()
        if not supplied:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "aicollab_token":
                    supplied = unquote(value)
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        project = (query.get("project") or [None])[0]
        if path == "/api/health":
            self._json({"ok": True, "status": "healthy", "auth_required": True})
            return
        if path.startswith("/api/") and not self._authorized():
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return
        if path == "/api/messages":
            since = 0
            try:
                since = int((query.get("since") or ["0"])[0])
            except Exception:
                pass
            msgs = [m for m in load_messages_cached(project) if m.get("id", 0) > since]
            self._json({"ok": True, "project": chatutil.normalize_project(project), "messages": msgs})
        elif path == "/api/sessions":
            self._json({"ok": True, **session_registry.load_registry()})
        elif path == "/api/config":
            config = load_config()
            config.pop("auth_token", None)
            self._json({"ok": True, "config": config, "auto_release_disabled": True})
        elif path == "/api/roster":
            self._json({
                "ok": True,
                "active_agents": roster.load_roster(),
                "all_agents": list(roster.ALL_AGENTS),
            })
        elif path == "/api/workload":
            normalized_project = chatutil.normalize_project(project or load_config().get("project"))
            state = workload.load_state(normalized_project)
            state["last_epoch"] = time.time()
            state["active_agents"] = [name for name in workload.AGENTS if name in workload.active_agents()]
            state["commander"] = commander.load_commander(state["project"])
            workload.refresh_agents(state, state["last_epoch"])
            self._json({"ok": True, "state": state})
        elif path == "/api/commander":
            normalized_project = chatutil.normalize_project(project or load_config().get("project"))
            self._json({
                "ok": True,
                "project": normalized_project,
                "commander": commander.load_commander(normalized_project),
                "all_agents": list(commander.ALL_AGENTS),
            })
        elif path == "/api/service":
            state = load_service_state()
            self._json({
                "ok": True,
                "running": bool(state.get("server_pid")),
                "state": state,
                "auto_release_disabled": True,
            })
        elif path == "/api/projects":
            self._json({"ok": True, "projects": chatutil.known_projects()})
        elif path == "/api/stream":
            since = 0
            try:
                since = int((query.get("since") or ["0"])[0])
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                deadline = time.time() + 300
                while time.time() < deadline:
                    messages = load_messages_cached(project)
                    fresh = [m for m in messages if int(m.get("id", 0)) > since]
                    for message in fresh:
                        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
                        self.wfile.write(b"event: message\n")
                        self.wfile.write(b"data: " + payload + b"\n\n")
                        since = max(since, int(message.get("id", 0)))
                    if fresh:
                        self.wfile.flush()
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        elif path == "/api/transcript":
            paths = chatutil.project_paths(project)
            if os.path.exists(paths["transcript"]):
                with open(paths["transcript"], "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"ok": False, "error": "transcript not found"}, 404)
        elif path.startswith("/uploads/"):
            rel = unquote(path[len("/uploads/"):])
            full = os.path.normpath(os.path.join(UPLOAD_DIR, rel))
            if not full.startswith(os.path.abspath(UPLOAD_DIR) + os.sep):
                self.send_error(403)
                return
            if not os.path.isfile(full):
                self.send_error(404)
                return
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
        else:
            self._static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/send", "/api/upload", "/api/sessions/refresh", "/api/config", "/api/roster", "/api/commander", "/api/shutdown"):
            self._json({"ok": False, "error": "not found"}, 404)
            return
        if not self._authorized():
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return
        if parsed.path == "/api/sessions/refresh":
            self._json({"ok": True, **session_registry.record_once()})
            return
        if parsed.path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                allowed = {
                    "project", "port", "auto_start_apps", "codex_app", "zcode_app", "opencode_app",
                    "commander_rules",
                }
                config = load_config()
                for key in allowed:
                    if key in data:
                        config[key] = data[key]
                save_config(config)
                config.pop("auth_token", None)
                self._json({"ok": True, "config": config, "restart_required": True})
            except Exception as exc:
                self._json({"ok": False, "error": "config failed: %r" % exc}, 400)
            return
        if parsed.path == "/api/commander":
            try:
                query = parse_qs(parsed.query)
                normalized_project = chatutil.normalize_project(
                    (query.get("project") or [load_config().get("project") or chatutil.DEFAULT_PROJECT])[0])
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                name = commander.save_commander(normalized_project, data.get("commander"))
                self._json({"ok": True, "project": normalized_project, "commander": name})
            except Exception as exc:
                self._json({"ok": False, "error": "commander failed: %r" % exc}, 400)
            return
        if parsed.path == "/api/roster":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                active, joined, left = roster.save_roster(data.get("active_agents"))
                self._json({"ok": True, "active_agents": active, "joined": joined, "left": left})
            except Exception as exc:
                self._json({"ok": False, "error": "roster failed: %r" % exc}, 400)
            return
        if parsed.path == "/api/shutdown":
            os.makedirs(os.path.dirname(SERVICE_CONTROL_PATH), exist_ok=True)
            with open(SERVICE_CONTROL_PATH, "w", encoding="utf-8") as f:
                json.dump({"action": "shutdown", "requested_at": datetime.now().isoformat()}, f)
            self._json({"ok": True, "message": "shutdown requested"})
            return
        query = parse_qs(parsed.query)
        project = (query.get("project") or [None])[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_UPLOAD_SIZE:
                raise ValueError("image too large")
            raw = self.rfile.read(length) if length else b"{}"
            if parsed.path == "/api/upload":
                message_name = (query.get("name") or ["你"])[0]
                filename = (query.get("filename") or ["image.png"])[0]
                attachment = save_upload_bytes(project or "cs2", filename, raw)
                caption = (query.get("text") or [""])[0]
                msg = append_message(message_name, caption, project, **attachment)
                if msg is None:
                    self._json({"ok": False, "error": "text required"}, 400)
                    return
                if not verify_append(project, msg):
                    self._json({"ok": False, "error": "write verification failed"}, 500)
                    return
                self._json({"ok": True, "message": msg})
                return
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._json({"ok": False, "error": "append failed: %r" % exc}, 400)
            return
        try:
            msg = append_message(str(data.get("name", "你")), str(data.get("text", "")),
                                 project or data.get("project"))
        except Exception as exc:
            self._json({"ok": False, "error": "append failed: %r" % exc}, 500)
            return
        if msg is None:
            self._json({"ok": False, "error": "text required"}, 400)
            return
        if not verify_append(project or data.get("project"), msg):
            self._json({"ok": False, "error": "write verification failed"}, 500)
            return
        self._json({"ok": True, "message": msg})


def run_server(port=PORT):
    ensure_dirs()
    auth.token()
    ensure_transcript()
    session_registry.start_background_watcher(2.0)
    server = ThreadingHTTPServer((HOST, port), Handler)
    print(f"三模型聊天室已启动: http://{HOST}:{port}")
    print(f"默认项目: {chatutil.DEFAULT_PROJECT}（支持 ?project=项目名 切换频道）")
    print(f"数据目录: {DATA_DIR}")
    print("其他模型发言示例:")
    print(f"  python chatroom.py send --name ZCode --project 项目名 --text \"内容\"")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="三模型聊天室")
    sub = parser.add_subparsers(dest="cmd")
    server_p = sub.add_parser("server", help="启动聊天室服务")
    server_p.add_argument("--port", type=int, default=PORT)
    send = sub.add_parser("send", help="以某个身份发送消息")
    send.add_argument("--name", default="你")
    send.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    send.add_argument("--text", default="")
    send.add_argument("--image", default=None)
    args = parser.parse_args()
    if args.cmd == "server":
        run_server(args.port)
    elif args.cmd == "send":
        ensure_dirs()
        attachment = None
        if args.image:
            if not os.path.isfile(args.image):
                print("图片不存在：%s" % args.image)
                sys.exit(1)
            with open(args.image, "rb") as f:
                attachment = save_upload_bytes(args.project, os.path.basename(args.image), f.read())
        msg = append_message(args.name, args.text, args.project, **(attachment or {}))
        if not msg:
            print("消息为空，未发送")
            sys.exit(1)
        if not verify_append(args.project, msg):
            print("写入校验失败：消息可能未落库，请勿假定已发送")
            sys.exit(2)
        print(f"[{msg['ts']}] {msg['name']}: {msg['text']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
