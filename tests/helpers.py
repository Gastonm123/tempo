"""Armado de un Tempo de prueba con base en memoria y fakes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from tempo.clock import FakeClock
from tempo.core import Tempo
from tempo.db import SqliteStore
from tempo.result import Err, Ok, Result
from tempo.store_files import MemoryReportStore
from tempo.supervisor import FakeSupervisor

TZ = timezone(timedelta(hours=-3))
HOUR = 3600

T = TypeVar("T")


def at(month: int, day: int, hour: int, minute: int = 0, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


@dataclass
class Rig:
    tempo: Tempo
    store: SqliteStore
    clock: FakeClock
    supervisor: FakeSupervisor
    saved: MemoryReportStore
    loose: MemoryReportStore


def make_rig(start: datetime | None = None) -> Rig:
    clock = FakeClock(start or at(11, 9, 10))
    store = SqliteStore(":memory:")
    supervisor = FakeSupervisor()
    saved = MemoryReportStore(Path("/reports"))
    loose = MemoryReportStore(Path("/cwd"))
    tempo = Tempo(store, clock, supervisor, saved_reports=saved, loose_reports=loose)
    return Rig(tempo, store, clock, supervisor, saved, loose)


def ok(result: Result[T, object]) -> T:
    """Extrae el valor de un Ok; si es Err, falla el test mostrando el error."""
    match result:
        case Ok(value):
            return value
        case Err(error):
            raise AssertionError(f"se esperaba Ok y llegó Err({error!r})")
