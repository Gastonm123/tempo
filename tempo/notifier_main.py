"""Punto de entrada del proceso de aviso: `python -m tempo.notifier_main`.

Lo lanza el supervisor (ver `cli.notifier_supervisor`); no es un comando de la
CLI ni esta pensado para ejecutarse a mano.
"""

from __future__ import annotations

import os
import signal
import sys
import threading

from tempo.alerts import (
    Alerter,
    FirstWorkingAlerter,
    NotifySendAlerter,
    NullAlerter,
    WindowsToastAlerter,
)
from tempo.clock import SystemClock
from tempo.config import Config
from tempo.db import SqliteStore
from tempo.notifier import Notifier


def build_alerter(config: Config) -> Alerter:
    if not config.notify_enabled:
        return NullAlerter()
    return FirstWorkingAlerter([WindowsToastAlerter(), NotifySendAlerter()])


def serve_notifier(config: Config) -> int:
    """Cuerpo del proceso de aviso. SIGHUP lo despierta para releer la base."""
    wake = threading.Event()
    # Sin este handler, SIGHUP terminaria el proceso (es su accion por defecto).
    signal.signal(signal.SIGHUP, lambda signum, frame: wake.set())
    # El supervisor lo lanza con SIGHUP bloqueada; al desbloquearla, una senal
    # que haya llegado durante el arranque se entrega ahora al handler.
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGHUP})
    notifier = Notifier(SqliteStore(config.db_path), SystemClock(), build_alerter(config),
                        config.pause_reminders, config.alarm_duration)
    notifier.run(wake)
    return 0


def main() -> int:
    config = Config.from_env(os.environ)
    config.home.mkdir(parents=True, exist_ok=True)
    return serve_notifier(config)


if __name__ == "__main__":
    sys.exit(main())
