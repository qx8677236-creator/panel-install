"""备份打包、保留份数和 Cron 解析。不连真实数据库，也不写面板的任务清单。"""

import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from app.backup.runner import pack_directory, prune
from app.backup.schedule import describe, validate_cron
from app.backup.store import BackupError, check_payload


class CronTests(unittest.TestCase):
    def test_daily_description(self):
        self.assertEqual(validate_cron("30 3 * * *"), "30 3 * * *")
        self.assertEqual(describe("30 3 * * *"), "每天 03:30")
        self.assertEqual(describe("*/15 * * * *"), "每 15 分钟")
        self.assertEqual(describe("30 3 * * 0"), "每周日 03:30")

    def test_rejects_bad_cron(self):
        for expr in ("* * * *", "99 3 * * *", "30 3 * * * *", "rm -rf /"):
            with self.assertRaises(BackupError):
                validate_cron(expr)


class PayloadTests(unittest.TestCase):
    def test_site_must_be_known(self):
        sites = [{"domain": "demo.test", "root": "demo"}]
        task = check_payload(
            {"name": "日报", "kind": "site", "target": "demo.test", "cron": "30 3 * * *", "keep": 3},
            sites,
            [],
        )
        self.assertEqual(task["target_label"], "网站 / demo.test")
        self.assertEqual(task["keep"], 3)
        with self.assertRaises(BackupError):
            check_payload(
                {"name": "日报", "kind": "site", "target": "../etc", "cron": "30 3 * * *", "keep": 3},
                sites,
                [],
            )

    def test_database_must_be_in_the_list(self):
        databases = [{"engine": "mysql", "name": "shop", "label": "MySQL / shop"}]
        task = check_payload(
            {
                "name": "库备份",
                "kind": "database",
                "engine": "mysql",
                "target": "shop",
                "cron": "0 4 * * *",
                "keep": 2,
            },
            [],
            databases,
        )
        self.assertEqual(task["engine"], "mysql")
        with self.assertRaises(BackupError):
            check_payload(
                {
                    "name": "库备份",
                    "kind": "database",
                    "engine": "mysql",
                    "target": "other",
                    "cron": "0 4 * * *",
                    "keep": 2,
                },
                [],
                databases,
            )


class ArchiveTests(unittest.TestCase):
    def test_pack_keeps_symlink_as_link_and_prune_keeps_newest(self):
        if not os.path.isfile("/usr/bin/tar"):
            self.skipTest("没有 tar")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text("hello", encoding="utf-8")
            outside = root / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            (site / "link").symlink_to(outside)
            dest = root / "out"
            archive = pack_directory(site, dest, 7)
            self.assertTrue(archive.name.startswith("task7-"))
            with tarfile.open(archive, "r:gz") as packed:
                names = packed.getnames()
                self.assertTrue(any(name.endswith("index.html") for name in names))
                link = next(member for member in packed.getmembers() if member.name.endswith("link"))
                self.assertTrue(link.issym())
                self.assertEqual(link.linkname, str(outside))
            folder = root / "kept"
            folder.mkdir()
            for index in range(4):
                path = folder / f"task3-2026010101010{index}.tar.gz"
                path.write_bytes(b"x")
                os.utime(path, (index, index))
            (folder / "notes.txt").write_text("keep", encoding="utf-8")
            (folder / "task9-20260101010101.tar.gz").write_bytes(b"y")
            safe = folder / "safe.txt"
            safe.write_text("safe", encoding="utf-8")
            (folder / "task3-20260101010109.tar.gz").symlink_to(safe)
            removed = prune(folder, 3, 2)
            self.assertEqual(removed, 2)
            left = {path.name for path in folder.iterdir()}
            self.assertIn("notes.txt", left)
            self.assertIn("task9-20260101010101.tar.gz", left)
            self.assertIn("safe.txt", left)
            self.assertEqual(sum(1 for name in left if name.startswith("task3-") and not name.endswith("09.tar.gz")), 2)


if __name__ == "__main__":
    unittest.main()
