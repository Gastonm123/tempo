"""Control del proceso de aviso: interfaz `Supervisor` e implementaciones."""

from __future__ import annotations

import fcntl
import os
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol


class Supervisor(Protocol):
    def sync(self) -> None:
        """Asegura que el proceso de aviso este vivo y le avisa que relea la base."""
        ...


class ProcessSupervisor(Supervisor):
    """Mantiene un unico proceso de aviso, identificado por un archivo de PID.

    - Vivo = existe el PID y /proc/<pid>/cmdline contiene "tempo" y "notifier"
      (asi un PID reciclado por otro programa no cuenta).
    - Si esta vivo, le manda SIGHUP para que relea la base.
    - Si no, lo lanza. El chequeo y el lanzamiento van bajo un flock, asi dos
      comandos simultaneos no lanzan dos procesos. El PID lo escribe el
      supervisor bajo el mismo lock, antes de soltarlo.
    """

    def __init__(
        self,
        pid_path: Path,
        lock_path: Path,
        log_path: Path,
        command: Sequence[str],
        env: Mapping[str, str],
    ) -> None:
        self._pid_path = pid_path
        self._lock_path = lock_path
        self._log_path = log_path
        self._command = list(command)
        self._env = dict(env)

    def _read_pid(self) -> int | None:
        try:
            text = self._pid_path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        return int(text) if text.isdigit() else None

    @staticmethod
    def is_notifier(pid: int) -> bool:
        try:
            os.kill(pid, 0)  # senal 0: solo pregunta si el proceso existe
        except (ProcessLookupError, PermissionError):
            return False
        try:
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except FileNotFoundError:  # termino entre las dos consultas
            return False
        return any(b"tempo" in a for a in args) and b"notifier" in args

    def running_pid(self) -> int | None:
        pid = self._read_pid()
        return pid if pid is not None and self.is_notifier(pid) else None

    def sync(self) -> None:
        pid = self.running_pid()
        if pid is not None:
            os.kill(pid, signal.SIGHUP)
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # se suelta al cerrar el archivo
            if self.running_pid() is not None:
                return  # otro comando lo lanzo mientras esperabamos el lock
            pid = os.posix_spawn(
                self._command[0],
                self._command,
                self._env,
                file_actions=[
                    (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                    (os.POSIX_SPAWN_OPEN, 1, str(self._log_path),
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644),
                    (os.POSIX_SPAWN_DUP2, 1, 2),
                ],
                setsid=True,  # sesion propia: sobrevive al comando que lo lanzo
                # Nace con SIGHUP bloqueada: si llega antes de que instale su
                # handler queda pendiente en vez de terminarlo (accion por defecto).
                setsigmask={signal.SIGHUP},
            )
            self._pid_path.write_text(f"{pid}\n", encoding="ascii")


class FakeSupervisor(Supervisor):
    """Para tests: solo cuenta cuantas veces se pidio sincronizar."""

    def __init__(self) -> None:
        self.calls = 0

    def sync(self) -> None:
        self.calls += 1
