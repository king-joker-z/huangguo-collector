import unittest
from datetime import datetime

from app.scheduler import next_run_at, parse_hhmm


class SchedulerTests(unittest.TestCase):
    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("03:30"), (3, 30))
        self.assertEqual(parse_hhmm("3:5"), (3, 5))
        self.assertIsNone(parse_hhmm("24:00"))
        self.assertIsNone(parse_hhmm("bad"))

    def test_next_run_same_week(self):
        after = datetime(2026, 11, 24, 21, 0, 0)  # Tuesday
        result = next_run_at("03:30", [0, 1, 2, 3, 4, 5, 6], after)
        self.assertEqual(result, datetime(2026, 11, 25, 3, 30, 0))

    def test_next_run_selected_weekday(self):
        after = datetime(2026, 11, 24, 21, 0, 0)  # Tuesday
        result = next_run_at("03:30", [0], after)  # next Monday
        self.assertEqual(result, datetime(2026, 11, 30, 3, 30, 0))

    def test_empty_weekdays_means_every_day(self):
        after = datetime(2026, 11, 24, 21, 0, 0)
        result = next_run_at("03:30", [], after)
        self.assertEqual(result, datetime(2026, 11, 25, 3, 30, 0))

    def test_invalid_time(self):
        self.assertIsNone(next_run_at("99:99", [0]))


if __name__ == "__main__":
    unittest.main()

