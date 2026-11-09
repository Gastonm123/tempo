"""Interfaz de reloj y sus implementaciones."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Fecha y hora actual, con zona horaria local."""
        ...

    def wait(self, event: threading.Event, seconds: float | None) -> bool:
        """Espera hasta que pasen `seconds` (None = sin limite) o se active `event`.

        Devuelve True si la espera termino porque se activo el evento.
        """
        ...


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now().astimezone()

    def wait(self, event: threading.Event, seconds: float | None) -> bool:
        return event.wait(seconds)


class FakeClock(Clock):
    """Reloj manual para tests: el tiempo solo avanza cuando se lo pide."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock necesita una fecha con zona horaria")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, moment: datetime) -> None:
        self._now = moment

    def wait(self, event: threading.Event, seconds: float | None) -> bool:
        # No duerme: si el evento ya esta activo vuelve enseguida; si no,
        # avanza el tiempo lo que se habria esperado.
        if event.is_set():
            return True
        if seconds is not None:
            self._now += timedelta(seconds=seconds)
        return event.is_set()
