"""Datos del dominio que devuelven el store y Tempo (inmutables)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tempo.sessions import Span


@dataclass(frozen=True, slots=True)
class Client:
    id: int
    name: str
    archived: bool


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    name: str
    client_id: int | None
    client_name: str | None
    description: str
    archived: bool


@dataclass(frozen=True, slots=True)
class Pause(Span):
    id: int
    start: datetime
    end: datetime | None  # None = pausa abierta
    reminded: bool  # ya se aviso que lleva 1 h pausada


@dataclass(frozen=True, slots=True)
class Session:
    id: int
    project_id: int
    project_name: str
    client_name: str | None
    description: str
    start_at: datetime
    duration_s: int
    end_at: datetime | None  # None = sesion abierta
    pauses: tuple[Pause, ...]

    @property
    def is_open(self) -> bool:
        return self.end_at is None
