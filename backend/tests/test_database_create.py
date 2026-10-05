import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pymysql.err import MySQLError

from app.databases.security import DatabaseError
from app.databases.service import create_database, public_password
from app.vault import PREFIX, decrypt_text


class FakeCursor:
    def __init__(self, fail_on=None):
        self.statements = []
        self.fail_on = fail_on

    def execute(self, query, args=None):
        if args is not None:
            raise AssertionError("execute must not take an args tuple")
        self.statements.append(query)
        if self.fail_on and query.startswith(self.fail_on):
            raise MySQLError("statement failed")

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


class CreateDatabaseUpsertTest(unittest.TestCase):
    def test_second_create_for_same_user_keeps_one_binding(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("app.databases.service._connect", return_value=connection), patch(
                "app.databases.service.DATA_DIR", data_dir
            ):
                create_database("binddb", "binduser", "Bindpass_1", "all")
                create_database("binddb", "binduser", "Bindpass_2", "all")
            stored = json.loads((data_dir / "mysql" / "state.json").read_text(encoding="utf-8"))
            sealed = stored["records"]["binddb"]["password"]
            self.assertEqual(decrypt_text(sealed), "Bindpass_2")

        self.assertTrue(connection.committed)
        self.assertEqual(len(cursor.statements), 12)
        second = cursor.statements[6:]
        kinds = []
        for statement in second:
            if statement == "FLUSH PRIVILEGES":
                kinds.append("flush")
            elif statement.startswith("CREATE USER IF NOT EXISTS "):
                kinds.append("create_user")
            elif statement.startswith("ALTER USER "):
                kinds.append("alter")
            elif statement.startswith("CREATE DATABASE IF NOT EXISTS "):
                kinds.append("create_db")
            elif statement.startswith("GRANT ALL PRIVILEGES ON "):
                kinds.append("grant")
        self.assertEqual(kinds, ["flush", "create_user", "alter", "create_db", "grant", "flush"])
        create_user = second[1]
        self.assertIn("`binduser`@'%'", create_user)
        self.assertNotIn("%%", create_user)
        self.assertIn("IDENTIFIED BY 'Bindpass_2'", create_user)
        self.assertIn("utf8mb4", second[3])
        self.assertIn("utf8mb4_general_ci", second[3])
        self.assertNotIn("DROP ", "\n".join(cursor.statements))
        self.assertEqual(list(stored["records"]), ["binddb"])
        record = stored["records"]["binddb"]
        self.assertEqual(record["user"], "binduser")
        self.assertEqual(record["host"], "%")
        self.assertEqual(record["privileges"], "ALL")
        self.assertTrue(record["password"].startswith(PREFIX))
        self.assertNotIn("Bindpass_1", json.dumps(stored))
        self.assertNotIn("Bindpass_2", json.dumps(stored))
        shown = public_password(record["password"])
        self.assertEqual(shown["password"], "")
        self.assertTrue(shown["has_password"])

    def test_later_statement_failure_does_not_save_or_drop(self):
        cursor = FakeCursor(fail_on="GRANT ")
        connection = FakeConnection(cursor)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("app.databases.service._connect", return_value=connection), patch(
                "app.databases.service.DATA_DIR", data_dir
            ):
                with self.assertRaises(DatabaseError):
                    create_database("binddb", "binduser", "Bindpass_1", "local")
            self.assertFalse((data_dir / "mysql" / "state.json").exists())
        self.assertFalse(connection.committed)
        self.assertTrue(any(item.startswith("CREATE USER IF NOT EXISTS ") for item in cursor.statements))
        self.assertNotIn("DROP ", "\n".join(cursor.statements))


if __name__ == "__main__":
    unittest.main()
