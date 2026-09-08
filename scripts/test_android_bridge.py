#!/usr/bin/env python3
"""Android 桥安全层测试套件（#T20260909-Q1 安全测试清单）。

不依赖真实 adb 设备：通过 monkeypatch 替换 subprocess，只验证安全层行为。
运行：python scripts/test_android_bridge.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "chatroom"))

import android_bridge  # noqa: E402


class BridgeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.tmp.name, "config.json")
        android_bridge.CONFIG_PATH = self.config_path
        android_bridge.DATA_DIR = self.tmp.name
        android_bridge.STATE_PATH = os.path.join(self.tmp.name, "bridge_state.json")
        android_bridge.AUDIT_PATH = os.path.join(self.tmp.name, "audit.jsonl")
        android_bridge.TOKEN_PATH = os.path.join(self.tmp.name, "tokens.json")
        android_bridge.audit_entries = []

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"android_bridge": {"enabled": False}}, f)

        android_bridge.auth.TOKEN_PATH = Path(os.path.join(self.tmp.name, "auth_token.txt"))
        android_bridge.auth.TOKEN_PATH.write_text("test-token-123", encoding="ascii")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), android_bridge.BridgeHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def request(self, method, path, body=None, token="test-token-123"):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url(path), data=data, method=method)
        if token:
            req.add_header("X-Chatroom-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def enable_bridge(self):
        android_bridge.CLI_ARMED = True

    def read_audit(self):
        with open(android_bridge.AUDIT_PATH, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


class TestDefaultOff(BridgeTestBase):
    def test_all_endpoints_404_when_disabled(self):
        for method, path in [
            ("GET", "/api/android-bridge/ping"),
            ("POST", "/api/android-bridge/exec"),
            ("POST", "/api/android-bridge/screenshot/confirm"),
        ]:
            status, _ = self.request(method, path, {})
            self.assertEqual(status, 404, "%s %s should be 404 when disabled" % (method, path))

    def test_enable_is_only_bootstrap_and_bridge_stays_dark(self):
        status, body = self.request("POST", "/api/android-bridge/enable", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["stage"], 1)
        # arm 未确认前，功能端点仍是 404
        status, _ = self.request("GET", "/api/android-bridge/ping")
        self.assertEqual(status, 404)

    def test_404_indistinguishable_from_unknown_path(self):
        status_bridge, _ = self.request("GET", "/api/android-bridge/ping")
        status_random, _ = self.request("GET", "/api/nonexistent-xyz")
        self.assertEqual(status_bridge, status_random)

    def test_server_binds_loopback_only(self):
        self.assertEqual(android_bridge.BIND_HOST, "127.0.0.1")
        host, _ = self.server.server_address[:2]
        self.assertEqual(host, "127.0.0.1")


class TestAuthAndDoubleConfirm(BridgeTestBase):
    def test_unauthorized_401_when_enabled(self):
        self.enable_bridge()
        status, _ = self.request("GET", "/api/android-bridge/ping", token=None)
        self.assertEqual(status, 401)

    def test_enable_requires_two_stages(self):
        status, body = self.request("POST", "/api/android-bridge/enable", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["stage"], 1)
        token = body["confirm_token"]

        status, _ = self.request("POST", "/api/android-bridge/enable", {})
        self.assertEqual(status, 403)  # 已 arm 后不带 token 再来只能 403

        status, _ = self.request("POST", "/api/android-bridge/enable", {"confirm_token": "bad"})
        self.assertEqual(status, 403)

        status, body = self.request("POST", "/api/android-bridge/enable", {"confirm_token": token})
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])

    def test_enable_token_single_use(self):
        _, body = self.request("POST", "/api/android-bridge/enable", {})
        token = body["confirm_token"]
        status, _ = self.request("POST", "/api/android-bridge/enable", {"confirm_token": token})
        self.assertEqual(status, 200)
        # 新一轮 arm 后，旧 token 不能再通过 stage 2
        _, body2 = self.request("POST", "/api/android-bridge/enable", {})
        self.assertEqual(body2["stage"], 1)
        status, _ = self.request("POST", "/api/android-bridge/enable", {"confirm_token": token})
        self.assertEqual(status, 403)

    def test_enable_token_expired(self):
        _, body = self.request("POST", "/api/android-bridge/enable", {})
        token = body["confirm_token"]
        with open(android_bridge.TOKEN_PATH, "r", encoding="utf-8") as f:
            tokens = json.load(f)
        tokens[token]["expires_at"] = time.time() - 1
        with open(android_bridge.TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(tokens, f)
        status, _ = self.request("POST", "/api/android-bridge/enable", {"confirm_token": token})
        self.assertEqual(status, 403)


class TestWhitelistAndParams(BridgeTestBase):
    def setUp(self):
        super().setUp()
        self.enable_bridge()

    def test_op_outside_whitelist_rejected(self):
        status, body = self.request("POST", "/api/android-bridge/exec",
                                    {"op": "shell", "params": {"cmd": "rm -rf /"}})
        self.assertEqual(status, 400)
        self.assertIn("whitelist", body["error"])

    def test_invalid_package_rejected(self):
        for bad in ["com.bad pkg", "pkg;ls", "../../etc", "", "a" * 200]:
            status, _ = self.request("POST", "/api/android-bridge/exec",
                                     {"op": "app.launch", "params": {"package": bad}})
            self.assertEqual(status, 500, "package %r should be rejected" % bad)

    def test_invalid_keycode_rejected(self):
        status, _ = self.request("POST", "/api/android-bridge/exec",
                                 {"op": "key.event", "params": {"keycode": "4; rm"}})
        self.assertEqual(status, 500)

    def test_invalid_text_rejected(self):
        status, _ = self.request("POST", "/api/android-bridge/exec",
                                 {"op": "text.input", "params": {"text": ""}})
        self.assertEqual(status, 500)
        status, _ = self.request("POST", "/api/android-bridge/exec",
                                 {"op": "text.input", "params": {"text": "a\nb"}})
        self.assertEqual(status, 500)
        status, _ = self.request("POST", "/api/android-bridge/exec",
                                 {"op": "text.input", "params": {"text": "x" * 501}})
        self.assertEqual(status, 500)

    def test_screenshot_blocked_without_confirm(self):
        status, _ = self.request("POST", "/api/android-bridge/exec",
                                 {"op": "screen.capture", "params": {}})
        self.assertEqual(status, 403)

    def test_screenshot_needs_fresh_token_each_time(self):
        status, body = self.request("POST", "/api/android-bridge/screenshot/confirm", {})
        self.assertEqual(status, 200)
        token = body["confirm_token"]
        status, _ = self.request("POST", "/api/android-bridge/exec",
                                 {"op": "screen.capture", "params": {}, "confirm_token": token})
        self.assertIn(status, (200, 500))  # 执行层可能因无 adb 失败，但确认层已放行
        status, _ = self.request("POST", "/api/android-bridge/exec",
                                 {"op": "screen.capture", "params": {}, "confirm_token": token})
        self.assertEqual(status, 403)  # 同一 token 一次性


class TestNoShellAndTimeout(BridgeTestBase):
    def setUp(self):
        super().setUp()
        self.enable_bridge()

    def test_argv_is_list_never_string(self):
        for op, params in [
            ("app.launch", {"package": "com.example.app"}),
            ("app.stop", {"package": "com.example.app"}),
            ("key.event", {"keycode": "4"}),
            ("text.input", {"text": "hello"}),
        ]:
            ok, cleaned = android_bridge.validate_op(op, params)
            self.assertTrue(ok)
            argv = android_bridge.build_argv(op, cleaned)
            self.assertIsInstance(argv, list)
            for part in argv:
                self.assertIsInstance(part, str)

    def test_subprocess_called_without_shell(self):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

            class P:
                returncode = 0
                stdout = b"ok"
                stderr = b""

            return P()

        original = android_bridge.subprocess.run
        android_bridge.subprocess.run = fake_run
        try:
            result = android_bridge.execute_op("key.event", {"keycode": "4"}, "tester")
        finally:
            android_bridge.subprocess.run = original
        self.assertTrue(result["ok"])
        self.assertFalse(captured["kwargs"].get("shell", False) is True)
        self.assertNotIsInstance(captured["argv"], str)

    def test_audit_is_minimal_and_contains_no_content(self):
        android_bridge.execute_op(
            "text.input", {"text": "private message"}, "privacy-tester"
        )
        entries = self.read_audit()
        self.assertTrue(entries)
        entry = entries[-1]
        self.assertEqual(set(entry), {"ts", "caller", "op", "params_present", "result"})
        self.assertEqual(set(entry["result"]), {"ok", "duration_ms"})
        serialized = json.dumps(entries, ensure_ascii=False)
        self.assertNotIn("private message", serialized)
        self.assertNotIn("stdout", serialized)
        self.assertNotIn("stderr", serialized)

    def test_config_cannot_enable_bridge(self):
        android_bridge.CLI_ARMED = False
        with open(android_bridge.CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump({"android_bridge": {"enabled": True}}, handle)
        self.assertFalse(android_bridge.bridge_enabled())


class TestCliOnlyEnable(unittest.TestCase):
    def test_serve_requires_cli_flag(self):
        import inspect
        signature = inspect.signature(android_bridge.serve)
        self.assertFalse(signature.parameters["enable_bridge"].default)

    def test_injection_attempt_stays_argv(self):
        argv = android_bridge.build_argv("text.input", {"text": "hello; rm -rf / && cat /etc/passwd"})
        ok, _ = android_bridge.validate_op("text.input", {"text": "hello; rm -rf / && cat /etc/passwd"})
        self.assertFalse(ok)  # 控制字符注入在参数层就被拒

    def test_timeout_recorded(self):
        def slow_run(argv, **kwargs):
            raise android_bridge.subprocess.TimeoutExpired(cmd=argv, timeout=10)

        original = android_bridge.subprocess.run
        android_bridge.subprocess.run = slow_run
        try:
            result = android_bridge.execute_op("device.list", {}, "tester")
        finally:
            android_bridge.subprocess.run = original
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "timeout")

    def test_serial_lock_prevents_concurrency(self):
        order = []

        def tracking_run(argv, **kwargs):
            order.append("start")
            time.sleep(0.05)
            order.append("end")

            class P:
                returncode = 0
                stdout = b""
                stderr = b""

            return P()

        original = android_bridge.subprocess.run
        android_bridge.subprocess.run = tracking_run
        try:
            threads = [
                threading.Thread(target=lambda: android_bridge.execute_op("device.list", {}, "t%d" % i))
                for i in range(3)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            android_bridge.subprocess.run = original
        # 串行锁下 start/end 必须严格交替，不出现连续两个 start
        for i in range(0, len(order) - 1, 2):
            self.assertEqual(order[i], "start")
            self.assertEqual(order[i + 1], "end")


class TestAudit(BridgeTestBase):
    def setUp(self):
        super().setUp()
        self.enable_bridge()

    def test_audit_five_fields(self):
        self.request("POST", "/api/android-bridge/exec", {"op": "device.list", "params": {}})
        entries = self.read_audit()
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(
                set(entry.keys()), {"ts", "caller", "op", "params_present", "result"}
            )
            self.assertEqual(set(entry["result"].keys()), {"ok", "duration_ms"})

    def test_rejected_op_audited(self):
        self.request("POST", "/api/android-bridge/exec", {"op": "shell", "params": {"cmd": "evil"}})
        entries = self.read_audit()
        self.assertTrue(any(e["op"] == "shell" and not e["result"]["ok"] for e in entries))


if __name__ == "__main__":
    unittest.main(verbosity=2)
