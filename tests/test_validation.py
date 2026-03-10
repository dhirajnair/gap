from __future__ import annotations

import unittest

from src.pipeline import SQLValidator


class TestSQLValidator(unittest.TestCase):

    def test_none_sql_is_invalid(self):
        result = SQLValidator.validate(None)
        self.assertFalse(result.is_valid)
        self.assertIn("No SQL", result.error)

    def test_valid_select(self):
        result = SQLValidator.validate("SELECT * FROM gaming_mental_health")
        self.assertTrue(result.is_valid)
        self.assertIsNotNone(result.validated_sql)

    def test_select_with_where(self):
        result = SQLValidator.validate("SELECT age, gender FROM gaming_mental_health WHERE age > 20")
        self.assertTrue(result.is_valid)

    def test_reject_delete(self):
        result = SQLValidator.validate("DELETE FROM gaming_mental_health")
        self.assertFalse(result.is_valid)
        self.assertIn("SELECT", result.error)

    def test_reject_drop(self):
        result = SQLValidator.validate("DROP TABLE gaming_mental_health")
        self.assertFalse(result.is_valid)

    def test_reject_insert(self):
        result = SQLValidator.validate("INSERT INTO gaming_mental_health VALUES (1,2,3)")
        self.assertFalse(result.is_valid)

    def test_reject_update(self):
        result = SQLValidator.validate("UPDATE gaming_mental_health SET age=1")
        self.assertFalse(result.is_valid)

    def test_reject_pragma(self):
        result = SQLValidator.validate("SELECT * FROM gaming_mental_health; PRAGMA table_info(gaming_mental_health)")
        self.assertFalse(result.is_valid)

    def test_reject_sqlite_master(self):
        result = SQLValidator.validate("SELECT * FROM sqlite_master")
        self.assertFalse(result.is_valid)

    def test_reject_multi_statement(self):
        result = SQLValidator.validate("SELECT 1; SELECT 2")
        self.assertFalse(result.is_valid)
        self.assertIn("Multiple", result.error)

    def test_reject_comment_injection(self):
        result = SQLValidator.validate("SELECT * FROM gaming_mental_health -- drop table")
        self.assertFalse(result.is_valid)

    def test_trailing_semicolon_stripped(self):
        result = SQLValidator.validate("SELECT * FROM gaming_mental_health;")
        self.assertTrue(result.is_valid)
        self.assertFalse(result.validated_sql.endswith(";"))

    def test_reject_create_table(self):
        result = SQLValidator.validate("CREATE TABLE evil (id INTEGER)")
        self.assertFalse(result.is_valid)

    def test_reject_attach(self):
        result = SQLValidator.validate("ATTACH DATABASE ':memory:' AS evil")
        self.assertFalse(result.is_valid)


if __name__ == "__main__":
    unittest.main()
