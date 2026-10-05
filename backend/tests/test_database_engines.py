import unittest
from pathlib import Path

from app.databases.security import (
    DatabaseError,
    engine_name,
    generated_password,
    resolve_sqlite_file,
    strong_password,
)


class EngineSecurityTest(unittest.TestCase):
    def test_engine_names_are_fixed(self):
        self.assertEqual(engine_name("redis"), "redis")
        for value in ["mysql;rm", "Redis", "../", ""]:
            with self.assertRaises(DatabaseError):
                engine_name(value)

    def test_generated_password_is_16_and_mixed(self):
        raw = generated_password()
        self.assertEqual(len(raw), 16)
        strong_password(raw)

    def test_sqlite_path_stays_inside_allowed_roots(self):
        root = Path("/tmp/panel-sqlite-root")
        root.mkdir(exist_ok=True)
        allowed = root / "app.db"
        allowed.write_text("", encoding="utf-8")
        self.assertEqual(resolve_sqlite_file(str(allowed), [root]), allowed.resolve())
        with self.assertRaises(DatabaseError):
            resolve_sqlite_file("/etc/passwd.db", [root])
        with self.assertRaises(DatabaseError):
            resolve_sqlite_file(str(root / ".." / "app.db"), [root])

    def test_admin_script_has_no_shell_and_binds_localhost(self):
        script = Path("/usr/local/sbin/panel-db-admin").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", script)
        self.assertIn("127.0.0.1:", script)
        self.assertNotIn("0.0.0.0:", script.split("bind 0.0.0.0", 1)[0])


if __name__ == "__main__":
    unittest.main()
