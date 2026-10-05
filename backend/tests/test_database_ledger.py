import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.databases.security import DatabaseError
from app.databases.service import (
    change_password,
    delete_database,
    ledger_upsert,
    list_databases,
    resolve_backup_file,
)
from app.vault import PREFIX, decrypt_text, encrypt_text


class FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, query, args=None):
        if args is not None:
            raise AssertionError("execute must not take an args tuple")
        self.statements.append(query)

    def fetchall(self):
        return [("mysql",), ("information_schema",), ("performance_schema",), ("sys",), ("appdb",)]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def escape(self, obj, mapping=None):
        return "'" + str(obj).replace("'", "''") + "'"

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DatabaseLedgerTest(unittest.TestCase):
    def test_upsert_and_list_hide_secret(self):
        secret = "LedgerPass_1"
        updated = "LedgerPass_2"
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("app.databases.service.DATA_DIR", data_dir), patch(
                "app.databases.service._import_live_databases"
            ), patch("app.databases.service.detect", return_value={"installed": False}):
                ledger_upsert("shopdb", "shopuser", secret, "localhost")
                ledger_upsert("shopdb", "shopuser", updated, "localhost")
                listed = list_databases(1, 50)
            path = data_dir / "mysql" / "panel_databases.sqlite"
            blob = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(secret.encode(), blob)
            self.assertNotIn(updated.encode(), blob)
            text = json.dumps(listed, ensure_ascii=False)
            self.assertNotIn(secret, text)
            self.assertNotIn(updated, text)
            self.assertNotIn("password_enc", text)
            item = listed["items"][0]
            self.assertEqual(item["db_name"], "shopdb")
            self.assertEqual(item["username"], "shopuser")
            self.assertEqual(item["password"], "")
            self.assertTrue(item["has_password"])
            self.assertEqual(item["privileges"], "ALL")
            self.assertEqual(item["host"], "localhost")
            self.assertEqual(item["auth_type"], "本地")
            stored = sqlite3.connect(path).execute(
                "SELECT password_enc FROM databases WHERE db_name = ?",
                ("shopdb",),
            ).fetchone()[0]
            self.assertTrue(stored.startswith(PREFIX))
            self.assertEqual(decrypt_text(stored), updated)
            self.assertEqual(len(listed["items"]), 1)

    def test_migrate_keeps_ciphertext_and_list_password_empty(self):
        sealed = encrypt_text("KeepPass_1")
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            folder = data_dir / "mysql"
            folder.mkdir()
            (folder / "state.json").write_text(
                json.dumps(
                    {
                        "remote": None,
                        "records": {
                            "olddb": {
                                "user": "olduser",
                                "host": "localhost",
                                "password": sealed,
                                "privileges": "ALL",
                                "created_at": "2020-01-01 00:00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("app.databases.service.DATA_DIR", data_dir), patch(
                "app.databases.service._import_live_databases"
            ), patch("app.databases.service.detect", return_value={"installed": False}):
                listed = list_databases(1, 50)
            blob = (folder / "panel_databases.sqlite").read_bytes()
            self.assertNotIn(b"KeepPass_1", blob)
            self.assertIn(sealed.encode(), blob)
            item = listed["items"][0]
            self.assertEqual(item["password"], "")
            self.assertTrue(item["has_password"])
            self.assertEqual(item["username"], "olduser")
            self.assertEqual(item["auth_type"], "本地")

    def test_import_skips_system_schemas_and_keeps_empty_user(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("app.databases.service.DATA_DIR", data_dir), patch(
                "app.databases.service._connect", return_value=connection
            ), patch("app.databases.service.detect", return_value={"installed": True}):
                listed = list_databases(1, 50)
        names = [item["db_name"] for item in listed["items"]]
        self.assertEqual(names, ["appdb"])
        item = listed["items"][0]
        self.assertEqual(item["username"], "")
        self.assertEqual(item["password"], "")
        self.assertFalse(item["has_password"])
        self.assertEqual(item["auth_type"], "本地")
        for system in ("mysql", "information_schema", "performance_schema", "sys"):
            self.assertNotIn(system, names)

    def test_delete_refuses_mismatched_confirm_name(self):
        with patch("app.databases.service._connect", side_effect=AssertionError("should not connect")):
            with self.assertRaises(DatabaseError) as caught:
                delete_database("appdb", "other")
        self.assertIn("数据库名", caught.exception.message)

    def test_backup_path_rejects_name_outside_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "backups"
            folder.mkdir()
            outside = root / "appdb-20200101010101.sql.gz"
            outside.write_text("nope", encoding="utf-8")
            link = folder / "appdb-20200101010101.sql.gz"
            link.symlink_to(outside)
            with self.assertRaises(DatabaseError):
                resolve_backup_file("appdb", "../appdb-20200101010101.sql.gz", folder=folder)
            with self.assertRaises(DatabaseError):
                resolve_backup_file("appdb", str(outside), folder=folder)
            with self.assertRaises(DatabaseError):
                resolve_backup_file("appdb", link.name, folder=folder)
            with self.assertRaises(DatabaseError):
                resolve_backup_file("appdb", "other-20200101010101.sql.gz", folder=folder)
            good = folder / "appdb-20200101010102.sql.gz"
            good.write_text("ok", encoding="utf-8")
            resolved = resolve_backup_file("appdb", good.name, folder=folder)
            self.assertEqual(resolved, good.resolve())
            self.assertEqual(resolved.parent, folder.resolve())

    def test_change_password_escapes_percent_host_without_args(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("app.databases.service.DATA_DIR", data_dir), patch(
                "app.databases.service._connect", return_value=connection
            ):
                ledger_upsert("shopdb", "shopuser", "OldPass_1a", "%")
                change_password("shopdb", "NewPass_1a!")
            blob = (data_dir / "mysql" / "panel_databases.sqlite").read_bytes()
        self.assertNotIn(b"NewPass_1a!", blob)
        self.assertNotIn(b"OldPass_1a", blob)
        alter = next(item for item in cursor.statements if item.startswith("ALTER USER "))
        self.assertIn("`shopuser`@'%'", alter)
        self.assertNotIn("%%", alter)
        self.assertNotIn("%s", alter)
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()
