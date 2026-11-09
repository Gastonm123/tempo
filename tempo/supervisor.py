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

    - El archivo guarda "PID inicio": el PID y el instante de creacion del
      proceso (campo 22 de /proc/<pid>/stat, en ticks desde el arranque). El
      proceso es el nuestro si ese PID existe, no es zombie y tiene el mismo
      inicio: un PID reciclado por otro programa tiene otro inicio. El inicio
      existe desde la creacion y no cambia con el exec, a diferencia de
      /proc/<pid>/cmdline, que queda vacio un instante mientras el hijo ejecuta
      el interprete.
    - Si esta vivo, le manda SIGHUP para que relea la base.
    - Si no, lo lanza. El chequeo y el lanzamiento van bajo un flock, asi dos
      comandos simultaneos no lanzan dos procesos. El archivo de PID lo escribe
      el supervisor bajo el mismo lock, antes de soltarlo.
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

    @staticmethod
    def start_time(pid: int) -> int | None:
        """Inicio del proceso en ticks desde el arranque; None si no existe o es zombie."""
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        except (FileNotFoundError, ProcessLookupError):
            return None
        # El campo 2 (nombre) va entre parentesis y puede contener espacios o
        # parentesis: los campos siguientes empiezan despues del ultimo ")".
        fields = stat[stat.rindex(")") + 2 :].split()
        state, start = fields[0], fields[19]  # campos 3 y 22
        return None if state in ("Z", "X") else int(start)

    def _read_identity(self) -> tuple[int, int] | None:
        try:
            parts = self._pid_path.read_text(encoding="ascii").split()
        except FileNotFoundError:
            return None
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None
        return int(parts[0]), int(parts[1])

    def running_pid(self) -> int | None:
        identity = self._read_identity()
        if identity is None:
            return None
        pid, start = identity
        return pid if self.start_time(pid) == start else None

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
            start = self.start_time(pid)
            if start is not None:  # None: ya murio al arrancar; el proximo comando lo relanza
                self._pid_path.write_text(f"{pid} {start}\n", encoding="ascii")


class FakeSupervisor(Supervisor):
    """Para tests: solo cuenta cuantas veces se pidio sincronizar."""

    def __init__(self) -> None:
        self.calls = 0

    def sync(self) -> None:
        self.calls += 1
