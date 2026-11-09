import unittest
from datetime import datetime, timedelta, timezone

from tempo.sessions import (
    Interval,
    expiry_time,
    worked_seconds,
)

TZ = timezone(timedelta(hours=-3))


def at(hour: int, minute: int = 0, day: int = 9) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


HOUR = 3600


class WorkedTest(unittest.TestCase):
    def test_closed_and_open_pauses(self) -> None:
        pauses = [Interval(at(10, 15), at(10, 30)), Interval(at(11), None)]
        # 10:00-11:30 = 90 min, menos 15 de la primera y 30 de la abierta.
        self.assertEqual(worked_seconds(at(10), pauses, at(11, 30)), 45 * 60)

    def test_until_before_start(self) -> None:
        self.assertEqual(worked_seconds(at(10), [], at(9)), 0)


class ExpiryTest(unittest.TestCase):
    def test_pause_shifts_expiry(self) -> None:
        pauses = [Interval(at(10, 30), at(10, 45))]
        self.assertEqual(expiry_time(at(10), HOUR, pauses), at(11, 15))

    def test_pause_after_expiry_is_ignored(self) -> None:
        pauses = [Interval(at(12), at(12, 30))]
        self.assertEqual(expiry_time(at(10), HOUR, pauses), at(11))

    def test_open_pause_means_unknown(self) -> None:
        self.assertIsNone(expiry_time(at(10), HOUR, [Interval(at(10, 30), None)]))

    def test_crosses_midnight(self) -> None:
        self.assertEqual(expiry_time(at(23, 30), HOUR, []), at(0, 30, day=10))


if __name__ == "__main__":
    unittest.main()
