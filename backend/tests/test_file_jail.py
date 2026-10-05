"""路径监狱必须挡住穿越、系统目录和压缩包里的越界条目。"""

import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.files.security import PathJailError, clear_alerts, locate, recent_alerts
from app.files.service import (
    FileOpError,
    change_mode,
    compress_paths,
    extract_archive,
    list_dir,
    make_file,
    remove_paths,
)


class JailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "wwwroot"
        self.root.mkdir()
        (self.root / "index.html").write_text("ok", encoding="utf-8")
        clear_alerts()

    def tearDown(self):
        self.tmp.cleanup()

    def test_relative_file_stays_inside(self):
        found = locate("index.html", root=self.root)
        self.assertEqual(found.resolve(), (self.root / "index.html").resolve())

    def test_reject_parent_traversal(self):
        with self.assertRaises(PathJailError) as caught:
            locate("../secret", root=self.root)
        self.assertEqual(caught.exception.reason, "traversal")
        self.assertTrue(recent_alerts())

    def test_reject_nested_traversal(self):
        with self.assertRaises(PathJailError):
            locate("docs/../../etc/passwd", root=self.root)

    def test_reject_etc_and_root(self):
        for raw in ("/etc/passwd", "/etc", "/root/.ssh/id_rsa", "/private/etc/passwd"):
            with self.assertRaises(PathJailError) as caught:
                locate(raw, root=self.root)
            self.assertEqual(caught.exception.reason, "sensitive_path", raw)

    def test_reject_sibling_outside_jail(self):
        outside = Path(self.tmp.name) / "secret.txt"
        outside.write_text("no", encoding="utf-8")
        with self.assertRaises(PathJailError) as caught:
            locate(outside.as_posix(), root=self.root)
        self.assertEqual(caught.exception.reason, "outside_jail")

    def test_reject_symlink_escape(self):
        outside = Path(self.tmp.name) / "secret.txt"
        outside.write_text("no", encoding="utf-8")
        link = self.root / "escape"
        link.symlink_to(outside)
        with self.assertRaises(PathJailError):
            locate("escape", root=self.root, follow_final=True)
        # 删除时不跟随链接，只去掉监狱里的这个链接本身。
        remove_paths(["escape"], root=self.root)
        self.assertFalse(link.exists())
        self.assertTrue(outside.exists())

    def test_list_reports_metadata(self):
        (self.root / "docs").mkdir()
        payload = list_dir("", root=self.root)
        names = [item["name"] for item in payload["entries"]]
        self.assertEqual(names[0], "docs")
        html = next(item for item in payload["entries"] if item["name"] == "index.html")
        self.assertFalse(html["is_dir"])
        self.assertGreater(html["size"], 0)
        self.assertIn("mtime", html)
        self.assertEqual(len(html["mode"]), 3)
        self.assertTrue(html["mode_text"].startswith("-"))

    def test_chmod_rejects_setuid(self):
        with self.assertRaises(FileOpError):
            change_mode(["index.html"], "4755", root=self.root)
        change_mode(["index.html"], "755", root=self.root)
        mode = stat.S_IMODE((self.root / "index.html").stat().st_mode)
        self.assertEqual(mode, 0o755)

    def test_zip_slip_is_rejected(self):
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("../evil.txt", "pwned")
        with self.assertRaises(PathJailError):
            extract_archive("bad.zip", "", root=self.root)
        self.assertFalse((Path(self.tmp.name) / "evil.txt").exists())

    def test_tar_symlink_is_rejected(self):
        archive = self.root / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            info = tarfile.TarInfo(name="link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            package.addfile(info)
        with self.assertRaises(PathJailError):
            extract_archive("bad.tar.gz", "", root=self.root)

    def test_round_trip_archive(self):
        make_file("", "note.txt", root=self.root)
        (self.root / "note.txt").write_text("hello", encoding="utf-8")
        compress_paths(["note.txt"], "note.zip", root=self.root)
        (self.root / "note.txt").unlink()
        extract_archive("note.zip", "", root=self.root)
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "hello")


if __name__ == "__main__":
    unittest.main()
