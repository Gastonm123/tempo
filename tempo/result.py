"""Errores como valores: Ok / Err y las fallas esperables del dominio.

Las fallas del dominio son datos (dataclasses inmutables), no excepciones.
Cada operacion declara en su tipo de retorno que fallas puede devolver, y
quien la llama las resuelve con `match`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generic, TypeVar

T = TypeVar("T", covariant=True)
E = TypeVar("E", covariant=True)


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    error: E


type Result[V, F] = Ok[V] | Err[F]


# --- Fallas del dominio -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class InvalidDuration:
    text: str


@dataclass(frozen=True, slots=True)
class InvalidDate:
    text: str


@dataclass(frozen=True, slots=True)
class InvalidMonth:
    text: str


@dataclass(frozen=True, slots=True)
class InvalidName:
    text: str


@dataclass(frozen=True, slots=True)
class SessionIsActive:
    session_id: int


@dataclass(frozen=True, slots=True)
class SessionAlreadyActive:
    session_id: int


@dataclass(frozen=True, slots=True)
class NoActiveSession:
    pass


@dataclass(frozen=True, slots=True)
class SessionNotFound:
    session_id: int


@dataclass(frozen=True, slots=True)
class AlreadyPaused:
    since: datetime


@dataclass(frozen=True, slots=True)
class NotPaused:
    pass


@dataclass(frozen=True, slots=True)
class SessionExpired:
    expired_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectNotFound:
    name: str


@dataclass(frozen=True, slots=True)
class ProjectArchived:
    name: str


@dataclass(frozen=True, slots=True)
class ClientNotFound:
    name: str


@dataclass(frozen=True, slots=True)
class NameTaken:
    name: str


@dataclass(frozen=True, slots=True)
class ClientHasActiveProjects:
    projects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClientArchived:
    name: str


@dataclass(frozen=True, slots=True)
class InvalidInterval:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class MonthLocked:
    month: str


@dataclass(frozen=True, slots=True)
class MonthNotClosed:
    month: str


@dataclass(frozen=True, slots=True)
class OpenSessionInMonth:
    month: str
    session_id: int


@dataclass(frozen=True, slots=True)
class EmptyMonth:
    month: str


@dataclass(frozen=True, slots=True)
class FileExists:
    path: Path


@dataclass(frozen=True, slots=True)
class AlertFailed:
    """No se pudo mostrar una notificacion (no es una falla del dominio)."""

    reason: str


type DomainError = (
    InvalidDuration
    | InvalidDate
    | InvalidMonth
    | InvalidName
    | SessionIsActive
    | SessionAlreadyActive
    | NoActiveSession
    | SessionNotFound
    | AlreadyPaused
    | NotPaused
    | SessionExpired
    | ProjectNotFound
    | ProjectArchived
    | ClientNotFound
    | NameTaken
    | ClientHasActiveProjects
    | ClientArchived
    | InvalidInterval
    | MonthLocked
    | MonthNotClosed
    | OpenSessionInMonth
    | EmptyMonth
    | FileExists
)
"""Todas las fallas esperables. La CLI las traduce a mensajes con un match exhaustivo."""
