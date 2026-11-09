"""Proceso de aviso: avisa cuando vence la sesion y cuando una pausa es larga.

`Notifier.tick()` hace un paso: manda los avisos que correspondan y dice
cuanto esperar hasta el proximo evento. `run()` repite ese paso esperando con
el reloj; el evento `wake` (lo activa SIGHUP) interrumpe la espera para
releer la base enseguida.

Reglas:
- El vencimiento se avisa solo si este proceso vio la sesion corriendo antes
  de vencer. Si arranca con la sesion ya vencida, no avisa algo viejo.
- Una pausa que llega a `pause_reminder` se avisa una sola vez (queda marcada
  en la base).
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta

from tempo import sessions as calc
from tempo.alerts import Alerter
from tempo.clock import Clock
from tempo.db import Store
from tempo.durations import format_hm
from tempo.result import AlertFailed, Err, Ok


class Notifier:
    def __init__(
        self, store: Store, clock: Clock, alerter: Alerter, pause_reminder: timedelta
    ) -> None:
        self._store = store
        self._clock = clock
        self._alerter = alerter
        self._pause_reminder = pause_reminder
        # Vencimiento que vimos venir: (sesion, momento). Solo ese se avisa.
        self._armed: tuple[int, datetime] | None = None

    def _seconds_until(self, moment: datetime) -> float:
        return max((moment - self._clock.now()).total_seconds(), 0.0)

    def _alert(self, title: str, body: str) -> None:
        match self._alerter.send(title, body):
            case Ok():
                pass
            case Err(AlertFailed(reason)):
                # El proceso no tiene a quien mostrarle el error: queda en su log.
                print(f"tempo notifier: no se pudo avisar: {reason}", file=sys.stderr)

    def tick(self) -> float | None:
        """Manda los avisos pendientes. Devuelve los segundos a esperar (None = sin limite)."""
        session = self._store.active_session()
        if session is None:
            self._armed = None
            return None
        now = self._clock.now()
        state = calc.session_state(session.start_at, session.duration_s, session.pauses, now)
        match state.kind:
            case "running":
                assert state.expires_at is not None
                self._armed = (session.id, state.expires_at)
                return self._seconds_until(state.expires_at)
            case "expired":
                if self._armed == (session.id, state.expires_at):
                    self._armed = None
                    self._alert("Sesión terminada",
                                f"Se cumplió el tiempo de {session.project_name}"
                                f" ({format_hm(session.duration_s)}). Usá tempo extend para"
                                " seguir o tempo stop para cerrarla.")
                return None
            case "paused":
                self._armed = None
                pause = next(p for p in session.pauses if p.end is None)
                if pause.reminded:
                    return None
                due = pause.start + self._pause_reminder
                if now < due:
                    return self._seconds_until(due)
                self._store.mark_pause_reminded(pause.id)
                self._alert("Pausa larga",
                            f"La sesión de {session.project_name} lleva"
                            f" {format_hm(int((now - pause.start).total_seconds()))} pausada.")
                return None

    def run(self, wake: threading.Event) -> None:
        while True:
            timeout = self.tick()
            self._clock.wait(wake, timeout)
            wake.clear()
