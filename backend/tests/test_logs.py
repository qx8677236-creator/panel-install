import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.clock import utc_wall_to_beijing
from app.logs.files import _nginx_file, _ssh, tail_lines
from app.logs.geo import place
from app.logs import store
from app.logs.store import LogError, _page


class LogSafetyTest(unittest.TestCase):
    def test_page_size_is_capped(self):
        self.assertEqual(_page(1, 10), (1, 10))
        with self.assertRaises(LogError):
            _page(1, 1000)

    def test_private_ip_stays_local(self):
        self.assertEqual(place("127.0.0.1"), "内网")
        self.assertEqual(place("10.1.1.1"), "内网")
        self.assertEqual(place("not-an-ip"), "")

    def test_nginx_path_cannot_escape(self):
        with self.assertRaises(LogError):
            _nginx_file("../auth.log")
        with self.assertRaises(LogError):
            _nginx_file("access.log.gz")

    def test_ssh_parser_reads_structured_success_only(self):
        line = "2026-10-02T06:26:38.076195+00:00 host sshd[1]: Accepted password for admin from 203.0.113.10 port 22 ssh2"
        item = _ssh(line)
        self.assertIsNotNone(item)
        self.assertEqual(item["operator"], "admin")
        self.assertEqual(item["created_at"], "2026-10-02 14:26:38")
        self.assertIn("203.0.113.10", item["details"])
        self.assertIsNone(_ssh("2026-10-02T06:26:38+00:00 host sshd[1]: Failed password for root from 203.0.113.10 port 22 ssh2"))

    def test_tail_reads_only_the_end(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.log"
            path.write_text("\n".join(f"line-{index}" for index in range(1000)), encoding="utf-8")
            lines = tail_lines(path, 5, max_bytes=200)
            self.assertLessEqual(len(lines), 5)
            self.assertTrue(lines[-1].endswith("999"))

    def test_utc_wall_clock_becomes_beijing(self):
        self.assertEqual(utc_wall_to_beijing("2026-10-05 09:49:32"), "2026-10-05 17:49:32")

    def test_existing_utc_rows_shift_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "panel.sqlite"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE panel_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operator TEXT NOT NULL,
                    type TEXT NOT NULL,
                    details TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO panel_logs(operator, type, details, ip, created_at) VALUES (?, ?, ?, ?, ?)",
                ("admin", "终端", "修改终端设置", "45.207.168.196", "2026-10-05 09:49:32"),
            )
            connection.commit()
            connection.close()
            original = store._path
            store._path = lambda: path
            try:
                with patch("app.logs.store.server_clock_is_utc", return_value=True):
                    first = store.query_logs("operation", 1, 10)
                    second = store.query_logs("operation", 1, 10)
            finally:
                store._path = original
        self.assertEqual(first["items"][0]["created_at"], "2026-10-05 17:49:32")
        self.assertEqual(second["items"][0]["created_at"], "2026-10-05 17:49:32")


if __name__ == "__main__":
    unittest.main()
