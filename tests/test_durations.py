import unittest

from tempo import durations
from tempo.result import Err, InvalidDuration, Ok


class ParseTest(unittest.TestCase):
    def test_valid(self) -> None:
        cases = {
            "1h": 3600,
            "90m": 5400,
            "1h30m": 5400,
            "1h 30m": 5400,
            " 2H ": 7200,
            "45M": 2700,
            "0h15m": 900,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(durations.parse(text), Ok(expected))

    def test_invalid(self) -> None:
        for text in ["", "abc", "90", "1.5h", "-1h", "0m", "0h0m", "30m1h", "h", "1 hora"]:
            with self.subTest(text=text):
                self.assertEqual(durations.parse(text), Err(InvalidDuration(text)))


class FormatTest(unittest.TestCase):
    def test_format_hm(self) -> None:
        self.assertEqual(durations.format_hm(0), "0:00")
        self.assertEqual(durations.format_hm(5400), "1:30")
        self.assertEqual(durations.format_hm(59), "0:00")
        self.assertEqual(durations.format_hm(36000 + 300), "10:05")

    def test_format_hm_up(self) -> None:
        self.assertEqual(durations.format_hm_up(59), "0:01")
        self.assertEqual(durations.format_hm_up(60), "0:01")
        self.assertEqual(durations.format_hm_up(0), "0:00")

    def test_format_hours(self) -> None:
        self.assertEqual(durations.format_hours(5400), "1.50")
        self.assertEqual(durations.format_hours(900), "0.25")
        self.assertEqual(durations.format_hours(0), "0.00")


if __name__ == "__main__":
    unittest.main()
