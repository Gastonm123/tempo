import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tempo.clock import FakeClock, SystemClock
from tempo.config import Config

T0 = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)


class ClockTest(unittest.TestCase):
    def test_clocks(self) -> None:
        self.assertIsNotNone(SystemClock().now().tzinfo)
        clock = FakeClock(T0)
        self.assertFalse(clock.wait(threading.Event(), 3600))  # avanza en vez de dormir
        self.assertEqual(clock.now(), T0 + timedelta(hours=1))
        event = threading.Event()
        event.set()
        self.assertTrue(clock.wait(event, 3600))  # evento activo: vuelve sin avanzar
        self.assertEqual(clock.now(), T0 + timedelta(hours=1))


class ConfigTest(unittest.TestCase):
    def test_default_home(self) -> None:
        config = Config.from_env({})
        self.assertEqual(config.home, Path("~/.local/tempo").expanduser().resolve())
        self.assertTrue(config.notify_enabled)

    def test_env_overrides(self) -> None:
        config = Config.from_env({"TEMPO_HOME": "/tmp/x", "TEMPO_NO_NOTIFY": "1"})
        self.assertEqual(config.home, Path("/tmp/x"))
        self.assertFalse(config.notify_enabled)
        self.assertEqual(config.db_path, Path("/tmp/x/tempo.db"))
        self.assertEqual(config.reports_dir, Path("/tmp/x/reports"))


if __name__ == "__main__":
    unittest.main()
