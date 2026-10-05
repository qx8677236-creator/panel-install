"""密钥文件和接口回包里的密码字段。"""

import json
import stat
import tempfile
import unittest
from pathlib import Path

from app.databases.service import public_password
from app.vault import PREFIX, decrypt_text, encrypt_text, read_secret_file, write_secret_file


class VaultTests(unittest.TestCase):
    def test_roundtrip_does_not_store_plaintext(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            plain = "Abcdef12_"
            stored = encrypt_text(plain, directory=base)
            self.assertTrue(stored.startswith(PREFIX))
            self.assertNotIn(plain, stored)
            self.assertEqual(decrypt_text(stored, directory=base), plain)
            self.assertEqual(decrypt_text(plain, directory=base), plain)
            key = base / "secret.key"
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            material = key.read_text(encoding="utf-8").strip()
            self.assertEqual(len(material), 64)
            bytes.fromhex(material)

    def test_legacy_secret_file_is_rewritten(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            path = base / "root.secret"
            path.write_text("Abcdef12_\n", encoding="utf-8")
            self.assertEqual(read_secret_file(path, directory=base), "Abcdef12_")
            saved = path.read_text(encoding="utf-8")
            self.assertTrue(saved.startswith(PREFIX))
            self.assertNotIn("Abcdef12_", saved)
            self.assertEqual(read_secret_file(path, directory=base), "Abcdef12_")

    def test_write_secret_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            path = base / "redis.secret"
            write_secret_file(path, "Abcdef12_", directory=base)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(read_secret_file(path, directory=base), "Abcdef12_")


class PasswordMaskTests(unittest.TestCase):
    def test_api_password_is_empty(self):
        masked = public_password("Abcdef12_")
        self.assertEqual(masked, {"password": "", "has_password": True})
        self.assertNotIn("Abcdef12_", json.dumps(masked))
        self.assertEqual(public_password(""), {"password": "", "has_password": False})


if __name__ == "__main__":
    unittest.main()
