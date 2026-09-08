#!/usr/bin/env python3
"""Tests for Qoder asynchronous task recovery."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chatroom"))

import qoder_watch  # noqa: E402


class QoderRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "main.sqlite"
        self.pending_path = Path(self.tmp.name) / "pending.json"
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.execute(
            "CREATE TABLE chat_session_messages ("
            "session_id TEXT, turn_id TEXT, sequence INTEGER, "
            "status TEXT, source TEXT, payload_json TEXT)"
        )
        qoder_watch.QODER_APP_DB = self.db_path
        qoder_watch.PENDING_TASKS_FILE = self.pending_path

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def insert_message(self, session_id, turn_id, sequence, status, role, text):
        payload = {"role": role, "text": text}
        self.connection.execute(
            "INSERT INTO chat_session_messages VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, turn_id, sequence, status, "sdk", json.dumps(payload)),
        )
        self.connection.commit()

    def write_tasks(self, *tasks):
        self.pending_path.write_text(json.dumps(list(tasks)), encoding="utf-8")

    def test_completed_task_is_recovered_without_reminder(self):
        self.insert_message("sid", "done", 0, "completed", "user", "work")
        self.insert_message("sid", "done", 1, "completed", "assistant", "DONE_OK")
        self.write_tasks({"project": "mycodexapp", "message_id": 7, "turn_id": "done"})
        with mock.patch.object(qoder_watch, "send") as send, mock.patch.object(qoder_watch, "log"):
            qoder_watch.recover_pending_tasks("sid")
        send.assert_called_once_with("mycodexapp", "DONE_OK")
        self.assertEqual(qoder_watch.load_pending_tasks(), [])

    def test_interrupted_task_reports_once(self):
        self.insert_message("sid", "stopped", 0, "interrupted", "assistant", "partial")
        self.write_tasks({"project": "mycodexapp", "message_id": 8, "turn_id": "stopped"})
        with mock.patch.object(qoder_watch, "send") as send, mock.patch.object(qoder_watch, "log"):
            qoder_watch.recover_pending_tasks("sid")
        self.assertEqual(send.call_count, 1)
        self.assertIn("中断", send.call_args.args[1])
        self.assertEqual(qoder_watch.load_pending_tasks(), [])

    def test_running_task_stays_pending(self):
        self.write_tasks({"project": "mycodexapp", "message_id": 9, "turn_id": "running"})
        with mock.patch.object(qoder_watch, "send") as send, mock.patch.object(qoder_watch, "log"):
            qoder_watch.recover_pending_tasks("sid")
        send.assert_not_called()
        self.assertEqual(len(qoder_watch.load_pending_tasks()), 1)

    def test_expired_running_task_times_out(self):
        self.write_tasks({
            "project": "mycodexapp", "message_id": 10, "turn_id": "expired",
            "started_at": 0, "max_wait_seconds": 1,
        })
        with mock.patch.object(qoder_watch, "send") as send, mock.patch.object(qoder_watch, "log"):
            qoder_watch.recover_pending_tasks("sid")
        send.assert_called_once()
        self.assertIn("超时", send.call_args.args[1])
        self.assertEqual(qoder_watch.load_pending_tasks(), [])


if __name__ == "__main__":
    unittest.main()
