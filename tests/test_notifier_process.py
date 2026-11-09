"""Tests con un proceso de aviso real (PID, /proc, flock, SIGHUP). Tardan unos segundos."""

import os
import signal
import tempfile
import time
import unittest
from pathlib import Path

from tempo.cli import notifier_supervisor
from tempo.config import Config
from tempo.db import SqliteStore
from tempo.supervisor import ProcessSupervisor


def wait_until(condition: "object", timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(condition) and condition():
            return True
        time.sleep(0.05)
    return False


class NotifierProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        home = Path(self._tmp.name)
        os.environ["TEMPO_NO_NOTIFY"] = "1"
        self.config = Config(home=home, notify_enabled=False)
        SqliteStore(self.config.db_path).close()  # crea el esquema
        self.supervisor: ProcessSupervisor = notifier_supervisor(self.config)
        self.started: list[int] = []

    def tearDown(self) -> None:
        for pid in self.started:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        del os.environ["TEMPO_NO_NOTIFY"]
        self._tmp.cleanup()

    def launch(self) -> int:
        self.supervisor.sync()
        pid = self.supervisor.running_pid()
        assert pid is not None, (self.config.home / "notifier.log").read_text()
        self.started.append(pid)
        return pid

    def test_pid_file_identifies_the_process(self) -> None:
        pid = self.launch()
        self.assertEqual(self.config.pid_path.read_text().split(),
                         [str(pid), str(ProcessSupervisor.start_time(pid))])
        # Mismo PID con otro inicio (un PID reciclado) no es el proceso de aviso.
        self.config.pid_path.write_text(f"{pid} 1\n")
        self.assertIsNone(self.supervisor.running_pid())

    def test_sighup_reloads_without_terminating(self) -> None:
        # Sin esperar: la senal llega mientras el proceso todavia esta arrancando
        # (es lo que pasa en `tempo start`, que escribe varias veces seguidas).
        pid = self.launch()
        self.supervisor.sync()
        self.supervisor.sync()
        time.sleep(1.0)
        self.assertEqual(self.supervisor.running_pid(), pid)

    def test_relaunch_after_kill(self) -> None:
        pid = self.launch()
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        self.assertIsNone(self.supervisor.running_pid())
        new_pid = self.launch()
        self.assertNotEqual(new_pid, pid)
        self.assertTrue(wait_until(lambda: ProcessSupervisor.start_time(new_pid) is not None))


if __name__ == "__main__":
    unittest.main()
