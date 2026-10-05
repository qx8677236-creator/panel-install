import unittest

from app.databases.security import (
    DatabaseError,
    identifier,
    password,
    pymysql_sql,
    quote_host,
    quote_ident,
    root_password,
)


class DatabaseSecurityTest(unittest.TestCase):
    def test_rejects_sql_and_shell_characters(self):
        for value in ["a;drop", "a'b", "a b", "../etc", "mysql", "root", "a`b"]:
            with self.assertRaises(DatabaseError):
                identifier(value)

    def test_quote_is_only_applied_to_safe_names(self):
        self.assertEqual(quote_ident("app_db", "数据库名"), "`app_db`")
        self.assertEqual(quote_host("local"), "'localhost'")
        self.assertEqual(quote_host("all"), "'%'")
        self.assertEqual(quote_host("ip", "203.0.113.10"), "'203.0.113.10'")
        with self.assertRaises(DatabaseError):
            quote_host("ip", "1.2.3.4;drop")
        with self.assertRaises(DatabaseError):
            password("short")
        with self.assertRaises(DatabaseError):
            password("has space")

    def test_root_password_requires_mixed_characters(self):
        self.assertEqual(root_password("Aa1!aaaa"), "Aa1!aaaa")
        self.assertEqual(root_password("Aa1_aaaa"), "Aa1_aaaa")
        rejected = [
            "",
            "abc",
            "Aa1!aaa",
            "abcdefgh",
            "ABCDEFGH",
            "Abcdefgh",
            "Abcdefg1",
            "Aa1aaaaa",
            "Aa1!aaa'",
            'Aa1!aaa"',
            "Aa1!aaa;",
            "Aa1! aaa",
            "Aa1!\taaaa",
            "Aa1!aaaa\x00",
            "Admin123!@#",
            "A" * 65,
            None,
            12,
        ]
        for value in rejected:
            with self.assertRaises(DatabaseError):
                root_password(value)

    def test_percent_host_is_escaped_for_pymysql(self):
        alter = pymysql_sql("ALTER USER 'root'@'%' IDENTIFIED BY %s")
        self.assertEqual(alter % ("'Aa1!aaaa'",), "ALTER USER 'root'@'%' IDENTIFIED BY 'Aa1!aaaa'")
        create = pymysql_sql("CREATE USER `app`@'%' IDENTIFIED BY %s")
        self.assertEqual(create % ("'x'",), "CREATE USER `app`@'%' IDENTIFIED BY 'x'")
        placeholders = pymysql_sql("CREATE USER %s@%s IDENTIFIED BY %s")
        self.assertEqual(placeholders, "CREATE USER %s@%s IDENTIFIED BY %s")
        self.assertEqual(placeholders % ("'app'", "'%'", "'x'"), "CREATE USER 'app'@'%' IDENTIFIED BY 'x'")


if __name__ == "__main__":
    unittest.main()
