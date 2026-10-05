"""授权 IP：空名单不限制，非法地址拒绝，不能把当前地址排除在外。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.nginx.security import SiteError
from app.panel import settings


class AllowIpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        self.original = settings._path
        settings._path = lambda: self.path

    def tearDown(self):
        settings._path = self.original
        self.tmp.cleanup()

    def test_parse_commas_and_reject_bad_values(self):
        self.assertEqual(settings.parse_allow_ips(""), [])
        self.assertEqual(
            settings.parse_allow_ips("116.50.137.202, 116.50.137.232，116.50.137.202"),
            ["116.50.137.202", "116.50.137.232"],
        )
        self.assertEqual(settings.normalize_ip("::ffff:10.1.2.3"), "10.1.2.3")
        for raw in ("999.1.1.1", "0.0.0.0", "10.0.0.0/8", "not-an-ip"):
            with self.assertRaises(SiteError):
                settings.parse_allow_ips(raw)

    def test_empty_list_allows_any_client(self):
        self.assertTrue(settings.ip_permitted("203.0.113.8", []))
        self.assertFalse(settings.ip_permitted("203.0.113.8", ["203.0.113.9"]))
        self.assertTrue(settings.ip_permitted("::ffff:203.0.113.9", ["203.0.113.9"]))

    def test_save_refuses_to_lock_out_current_ip(self):
        with self.assertRaises(SiteError) as caught:
            settings.save_allow_ips("203.0.113.9", "203.0.113.8")
        self.assertIn("不能保存", str(caught.exception))
        self.assertFalse(self.path.exists())

        saved = settings.save_allow_ips("203.0.113.8, 203.0.113.9", "203.0.113.8")
        self.assertEqual(saved["allow_ips"], "203.0.113.8,203.0.113.9")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["allow_ips"], ["203.0.113.8", "203.0.113.9"])
        self.assertTrue(settings.ip_permitted("203.0.113.8"))
        self.assertFalse(settings.ip_permitted("198.51.100.4"))

        cleared = settings.save_allow_ips("", "198.51.100.4")
        self.assertEqual(cleared["allow_ips"], "")
        self.assertTrue(settings.ip_permitted("198.51.100.4"))
