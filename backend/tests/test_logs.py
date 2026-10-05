import tempfile
import unittest
from pathlib import Path

from app.logs.files import _nginx_file, _ssh, tail_lines
from app.logs.geo import place
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
        self.assertIn("203.0.113.10", item["details"])
        self.assertIsNone(_ssh("2026-10-02T06:26:38+00:00 host sshd[1]: Failed password for root from 203.0.113.10 port 22 ssh2"))

    def test_tail_reads_only_the_end(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.log"
            path.write_text("\n".join(f"line-{index}" for index in range(1000)), encoding="utf-8")
            lines = tail_lines(path, 5, max_bytes=200)
            self.assertLessEqual(len(lines), 5)
            self.assertTrue(lines[-1].endswith("999"))


if __name__ == "__main__":
    unittest.main()
