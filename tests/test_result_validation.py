from __future__ import annotations

import unittest

from src.pipeline import ResultValidator


class TestResultValidator(unittest.TestCase):

    def test_empty_rows_no_warnings(self):
        self.assertEqual(ResultValidator.validate([], "SELECT 1"), [])

    def test_none_sql_no_warnings(self):
        self.assertEqual(ResultValidator.validate([{"a": 1}], None), [])

    def test_consistent_rows_no_warnings(self):
        rows = [{"age": 20, "name": "A"}, {"age": 25, "name": "B"}]
        self.assertEqual(ResultValidator.validate(rows, "SELECT age, name FROM t"), [])

    def test_inconsistent_columns_warns(self):
        rows = [{"age": 20, "name": "A"}, {"age": 25, "extra": "X"}]
        warnings = ResultValidator.validate(rows, "SELECT age, name FROM t")
        self.assertTrue(any("inconsistent" in w.lower() for w in warnings))

    def test_negative_count_warns(self):
        rows = [{"count": -5}]
        warnings = ResultValidator.validate(rows, "SELECT COUNT(*) as count FROM t")
        self.assertTrue(any("negative" in w.lower() for w in warnings))

    def test_valid_count_no_warning(self):
        rows = [{"count": 42}]
        warnings = ResultValidator.validate(rows, "SELECT COUNT(*) as count FROM t")
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
