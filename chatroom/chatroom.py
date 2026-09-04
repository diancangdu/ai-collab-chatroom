import argparse
import json
import os
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import chatutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TRANSCRIPT_LIMIT = 2 * 1024 * 1024
STATIC_DIR = os.path.join(BASE_DIR, "static")
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


def append_message(name, text, project=None):
    text = (text or "").strip()
    name = (name or "").strip() or "你"
    if not text:
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
        with open(paths["messages"], "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        _msg_cache.pop(paths["project"], None)
        rotate_transcript(paths["project"])
        with open(paths["transcript"], "a", encoding="utf-8") as f:
            f.write(transcript_line(msg) + "\n")
        return msg

def transcript_line(msg):
    return "[%s] %s: %s" % (msg.get("ts", "?"), msg.get("name", "?"), msg.get("text", ""))


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
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not full.startswith(STATIC_DIR):
            self.send_error(403)
            return
        if not os.path.isfile(full):
            self.send_error(404)
            return
        if full.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif full.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        else:
            ctype = "text/html; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        project = (query.get("project") or [None])[0]
        if path == "/api/messages":
            since = 0
            try:
                since = int((query.get("since") or ["0"])[0])
            except Exception:
                pass
            msgs = [m for m in load_messages_cached(project) if m.get("id", 0) > since]
            self._json({"ok": True, "project": chatutil.normalize_project(project), "messages": msgs})
        elif path == "/api/projects":
            self._json({"ok": True, "projects": chatutil.known_projects()})
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
        else:
            self._static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/send":
            self._json({"ok": False, "error": "not found"}, 404)
            return
        query = parse_qs(parsed.query)
        project = (query.get("project") or [None])[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        msg = append_message(str(data.get("name", "你")), str(data.get("text", "")),
                             project or data.get("project"))
        if msg is None:
            self._json({"ok": False, "error": "text required"}, 400)
        else:
            self._json({"ok": True, "message": msg})


def run_server(host=HOST, port=PORT):
    ensure_dirs()
    ensure_transcript()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"三模型聊天室已启动: http://{host}:{port}")
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
    server_p.add_argument("--host", default=HOST)
    server_p.add_argument("--port", type=int, default=PORT)
    send = sub.add_parser("send", help="以某个身份发送消息")
    send.add_argument("--name", default="你")
    send.add_argument("--project", default=chatutil.DEFAULT_PROJECT)
    send.add_argument("--text", required=True)
    args = parser.parse_args()
    if args.cmd == "server":
        run_server(args.host, args.port)
    elif args.cmd == "send":
        ensure_dirs()
        msg = append_message(args.name, args.text, args.project)
        if msg:
            print(f"[{msg['ts']}] {msg['name']}: {msg['text']}")
        else:
            print("消息为空，未发送")
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
