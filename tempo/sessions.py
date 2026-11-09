"""Calculos puros sobre una sesion: tiempo trabajado, pausas y vencimiento.

Una sesion tiene un inicio, una duracion planificada y una lista de pausas.
El tiempo pausado no cuenta. La sesion vence cuando el tiempo trabajado
alcanza la duracion; desde ese momento el contador queda detenido.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol


class Span(Protocol):
    """Lo unico que los calculos necesitan de una pausa: inicio y fin.

    Se declaran como atributos y no como @property: una @property en la
    interfaz la heredan las dataclasses que la implementan y la toman como
    valor por defecto del campo.
    """

    start: datetime
    end: datetime | None  # None = sigue abierto


@dataclass(frozen=True, slots=True)
class Interval(Span):
    """Un intervalo suelto, para calculos y para escribir pausas nuevas."""

    start: datetime
    end: datetime | None


def _seconds(delta: timedelta) -> int:
    return int(delta.total_seconds())


def paused_seconds(pauses: Sequence[Span], until: datetime) -> int:
    """Segundos pausados desde el comienzo hasta `until`."""
    total = 0
    for pause in pauses:
        end = until if pause.end is None else min(pause.end, until)
        if end > pause.start:
            total += _seconds(end - pause.start)
    return total


def worked_seconds(start: datetime, pauses: Sequence[Span], until: datetime) -> int:
    """Segundos trabajados entre `start` y `until`, descontando las pausas."""
    if until <= start:
        return 0
    return _seconds(until - start) - paused_seconds(pauses, until)


def expiry_time(
    start: datetime, duration_s: int, pauses: Sequence[Span]
) -> datetime | None:
    """Momento en que el tiempo trabajado alcanza la duracion.

    Devuelve None si la sesion esta pausada (pausa abierta) antes de vencer:
    mientras dure la pausa el vencimiento no se puede saber.
    """
    moment = start
    remaining = timedelta(seconds=duration_s)
    for pause in sorted(pauses, key=lambda p: p.start):
        if pause.start < moment:
            # Pausa superpuesta con un tramo ya descontado: solo cuenta lo que sobresale.
            if pause.end is None:
                return None
            moment = max(moment, pause.end)
            continue
        if moment + remaining <= pause.start:
            return moment + remaining
        remaining -= pause.start - moment
        if pause.end is None:
            return None
        moment = pause.end
    return moment + remaining


@dataclass(frozen=True, slots=True)
class SessionState:
    kind: Literal["running", "paused", "expired"]
    worked_s: int
    remaining_s: int
    expires_at: datetime | None  # None mientras esta pausada
    paused_since: datetime | None


def session_state(
    start: datetime, duration_s: int, pauses: Sequence[Span], now: datetime
) -> SessionState:
    """Estado de una sesion abierta en el momento `now`."""
    expires_at = expiry_time(start, duration_s, pauses)
    if expires_at is not None and expires_at <= now:
        return SessionState("expired", duration_s, 0, expires_at, None)
    worked = worked_seconds(start, pauses, now)
    open_pause = next((p for p in pauses if p.end is None), None)
    if open_pause is not None:
        return SessionState("paused", worked, duration_s - worked, None, open_pause.start)
    return SessionState("running", worked, duration_s - worked, expires_at, None)


def effective_end(
    start: datetime, duration_s: int, pauses: Sequence[Span], now: datetime
) -> datetime:
    """Fin a registrar al detener la sesion en `now`: el vencimiento si ya paso."""
    expires_at = expiry_time(start, duration_s, pauses)
    if expires_at is not None and expires_at <= now:
        return expires_at
    return now
