"""Persistencia: interfaz `Store` y su implementacion SQLite.

Es el unico modulo que escribe SQL. Las fechas se guardan como texto ISO-8601
en UTC (con fecha, hora y offset): asi se comparan correctamente como texto y
no dependen de cambios de horario. Al leerlas se devuelven con zona horaria;
la conversion a hora local la hace quien las muestra.

Las excepciones de sqlite3 que corresponden a fallas esperables (por ejemplo
la regla de una sola sesion activa) se convierten en `Err` aca. Cualquier otra
excepcion es inesperada y no se atrapa.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from tempo.models import Client, Pause, Project, Session
from tempo.result import Err, NameTaken, Ok, Result, SessionAlreadyActive
from tempo.sessions import Span

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE clients (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL UNIQUE,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);

CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    client_id   INTEGER REFERENCES clients(id),
    description TEXT    NOT NULL DEFAULT '',
    archived    INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);

-- El cliente de un proyecto se fija al crearlo y no se puede cambiar.
CREATE TRIGGER project_client_immutable
BEFORE UPDATE OF client_id ON projects
WHEN OLD.client_id IS NOT NEW.client_id
BEGIN
    SELECT RAISE(ABORT, 'project client is immutable');
END;

CREATE TABLE sessions (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    description TEXT    NOT NULL DEFAULT '',
    start_at    TEXT    NOT NULL,
    duration_s  INTEGER NOT NULL CHECK (duration_s > 0),
    end_at      TEXT,
    CHECK (end_at IS NULL OR end_at >= start_at)
);

-- Como maximo una sesion abierta.
CREATE UNIQUE INDEX one_active ON sessions((1)) WHERE end_at IS NULL;
CREATE INDEX sessions_start ON sessions(start_at);

CREATE TABLE pauses (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    start_at   TEXT    NOT NULL,
    end_at     TEXT,
    CHECK (end_at IS NULL OR end_at >= start_at)
);

-- Como maximo una pausa abierta por sesion.
CREATE UNIQUE INDEX one_open_pause ON pauses(session_id) WHERE end_at IS NULL;

-- Una fila por reporte guardado; un mes con al menos una fila queda bloqueado.
CREATE TABLE exports (
    id          INTEGER PRIMARY KEY,
    month       TEXT NOT NULL,
    exported_at TEXT NOT NULL
);
CREATE INDEX exports_month ON exports(month);
"""


def to_db(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def from_db(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Store(Protocol):
    def transaction(self) -> AbstractContextManager[None]:
        """Agrupa varias operaciones de forma atomica."""
        ...

    # Clientes
    def add_client(self, name: str) -> Result[Client, NameTaken]: ...
    def get_client(self, client_id: int) -> Client | None: ...
    def find_client(self, name: str) -> Client | None: ...
    def list_clients(self, *, include_archived: bool) -> list[Client]: ...
    def rename_client(self, client_id: int, name: str) -> Result[None, NameTaken]: ...
    def set_client_archived(self, client_id: int, archived: bool) -> None: ...

    # Proyectos
    def add_project(
        self, name: str, client_id: int | None, description: str
    ) -> Result[Project, NameTaken]: ...
    def get_project(self, project_id: int) -> Project | None: ...
    def find_project(self, name: str) -> Project | None: ...
    def list_projects(
        self, *, include_archived: bool, client_id: int | None = None
    ) -> list[Project]: ...
    def update_project(
        self, project_id: int, *, name: str, description: str
    ) -> Result[None, NameTaken]: ...
    def set_project_archived(self, project_id: int, archived: bool) -> None: ...

    # Sesiones
    def add_session(
        self, project_id: int, description: str, start_at: datetime, duration_s: int
    ) -> Result[Session, SessionAlreadyActive]: ...
    def get_session(self, session_id: int) -> Session | None: ...
    def active_session(self) -> Session | None: ...
    def sessions_between(
        self,
        start: datetime,
        end: datetime,
        project_ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> list[Session]:
        """Sesiones con inicio en [start, end), ordenadas por inicio."""
        ...
    def update_session(
        self,
        session_id: int,
        *,
        project_id: int,
        description: str,
        start_at: datetime,
        duration_s: int,
        end_at: datetime | None,
    ) -> None: ...
    def delete_session(self, session_id: int) -> None: ...

    # Pausas
    def open_pause(self, session_id: int, start_at: datetime) -> None: ...
    def close_pause(self, session_id: int, end_at: datetime) -> None: ...
    def add_closed_pause(self, session_id: int, start_at: datetime, end_at: datetime) -> None: ...
    def replace_pauses(self, session_id: int, pauses: Sequence[Span]) -> None: ...

    # Reportes guardados
    def add_export(self, month: str, exported_at: datetime) -> None: ...
    def is_month_locked(self, month: str) -> bool: ...
    def exported_months(self) -> list[str]:
        """Meses con al menos un reporte guardado, ordenados."""
        ...


class SqliteStore(Store):
    def __init__(self, path: Path | str) -> None:
        # isolation_level=None: autocommit fuera de transaction(); las
        # transacciones se abren explicitamente con BEGIN IMMEDIATE.
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if str(path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            # executescript hace COMMIT de lo pendiente antes de correr, asi que
            # la transaccion va dentro del propio script.
            self._conn.executescript(
                f"BEGIN IMMEDIATE;\n{_SCHEMA}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._conn.in_transaction:
            yield  # ya hay una transaccion abierta: se une a ella
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def transaction(self) -> AbstractContextManager[None]:
        return self._transaction()

    # --- Clientes -----------------------------------------------------------

    @staticmethod
    def _client(row: sqlite3.Row) -> Client:
        return Client(id=row["id"], name=row["name"], archived=bool(row["archived"]))

    def add_client(self, name: str) -> Result[Client, NameTaken]:
        try:
            cursor = self._conn.execute("INSERT INTO clients(name) VALUES (?)", (name,))
        except sqlite3.IntegrityError as error:
            if "clients.name" in str(error):
                return Err(NameTaken(name))
            raise
        assert cursor.lastrowid is not None
        return Ok(Client(id=cursor.lastrowid, name=name, archived=False))

    def get_client(self, client_id: int) -> Client | None:
        row = self._conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        return None if row is None else self._client(row)

    def find_client(self, name: str) -> Client | None:
        row = self._conn.execute("SELECT * FROM clients WHERE name = ?", (name,)).fetchone()
        return None if row is None else self._client(row)

    def list_clients(self, *, include_archived: bool) -> list[Client]:
        sql = "SELECT * FROM clients"
        if not include_archived:
            sql += " WHERE archived = 0"
        return [self._client(row) for row in self._conn.execute(sql + " ORDER BY name")]

    def rename_client(self, client_id: int, name: str) -> Result[None, NameTaken]:
        try:
            self._conn.execute("UPDATE clients SET name = ? WHERE id = ?", (name, client_id))
        except sqlite3.IntegrityError as error:
            if "clients.name" in str(error):
                return Err(NameTaken(name))
            raise
        return Ok(None)

    def set_client_archived(self, client_id: int, archived: bool) -> None:
        self._conn.execute(
            "UPDATE clients SET archived = ? WHERE id = ?", (int(archived), client_id)
        )

    # --- Proyectos ----------------------------------------------------------

    _PROJECT_SELECT = """
        SELECT p.*, c.name AS client_name
        FROM projects p LEFT JOIN clients c ON c.id = p.client_id
    """

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            client_id=row["client_id"],
            client_name=row["client_name"],
            description=row["description"],
            archived=bool(row["archived"]),
        )

    def add_project(
        self, name: str, client_id: int | None, description: str
    ) -> Result[Project, NameTaken]:
        try:
            cursor = self._conn.execute(
                "INSERT INTO projects(name, client_id, description) VALUES (?, ?, ?)",
                (name, client_id, description),
            )
        except sqlite3.IntegrityError as error:
            if "projects.name" in str(error):
                return Err(NameTaken(name))
            raise
        assert cursor.lastrowid is not None
        project = self.get_project(cursor.lastrowid)
        assert project is not None
        return Ok(project)

    def get_project(self, project_id: int) -> Project | None:
        row = self._conn.execute(
            self._PROJECT_SELECT + " WHERE p.id = ?", (project_id,)
        ).fetchone()
        return None if row is None else self._project(row)

    def find_project(self, name: str) -> Project | None:
        row = self._conn.execute(self._PROJECT_SELECT + " WHERE p.name = ?", (name,)).fetchone()
        return None if row is None else self._project(row)

    def list_projects(
        self, *, include_archived: bool, client_id: int | None = None
    ) -> list[Project]:
        conditions: list[str] = []
        params: list[object] = []
        if not include_archived:
            conditions.append("p.archived = 0")
        if client_id is not None:
            conditions.append("p.client_id = ?")
            params.append(client_id)
        sql = self._PROJECT_SELECT
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        return [self._project(row) for row in self._conn.execute(sql + " ORDER BY p.name", params)]

    def update_project(
        self, project_id: int, *, name: str, description: str
    ) -> Result[None, NameTaken]:
        try:
            self._conn.execute(
                "UPDATE projects SET name = ?, description = ? WHERE id = ?",
                (name, description, project_id),
            )
        except sqlite3.IntegrityError as error:
            if "projects.name" in str(error):
                return Err(NameTaken(name))
            raise
        return Ok(None)

    def set_project_archived(self, project_id: int, archived: bool) -> None:
        self._conn.execute(
            "UPDATE projects SET archived = ? WHERE id = ?", (int(archived), project_id)
        )

    # --- Sesiones -----------------------------------------------------------

    _SESSION_SELECT = """
        SELECT s.*, p.name AS project_name, c.name AS client_name
        FROM sessions s
        JOIN projects p ON p.id = s.project_id
        LEFT JOIN clients c ON c.id = p.client_id
    """

    def _sessions(self, rows: list[sqlite3.Row]) -> list[Session]:
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        marks = ",".join("?" * len(ids))
        pauses: dict[int, list[Pause]] = {}
        for pause in self._conn.execute(
            f"SELECT * FROM pauses WHERE session_id IN ({marks}) ORDER BY start_at", ids
        ):
            pauses.setdefault(pause["session_id"], []).append(
                Pause(
                    id=pause["id"],
                    start=from_db(pause["start_at"]),
                    end=None if pause["end_at"] is None else from_db(pause["end_at"]),
                )
            )
        return [
            Session(
                id=row["id"],
                project_id=row["project_id"],
                project_name=row["project_name"],
                client_name=row["client_name"],
                description=row["description"],
                start_at=from_db(row["start_at"]),
                duration_s=row["duration_s"],
                end_at=None if row["end_at"] is None else from_db(row["end_at"]),
                pauses=tuple(pauses.get(row["id"], [])),
            )
            for row in rows
        ]

    def add_session(
        self, project_id: int, description: str, start_at: datetime, duration_s: int
    ) -> Result[Session, SessionAlreadyActive]:
        try:
            cursor = self._conn.execute(
                "INSERT INTO sessions(project_id, description, start_at, duration_s)"
                " VALUES (?, ?, ?, ?)",
                (project_id, description, to_db(start_at), duration_s),
            )
        except sqlite3.IntegrityError as error:
            if "one_active" in str(error):
                active = self.active_session()
                assert active is not None
                return Err(SessionAlreadyActive(active.id))
            raise
        assert cursor.lastrowid is not None
        session = self.get_session(cursor.lastrowid)
        assert session is not None
        return Ok(session)

    def get_session(self, session_id: int) -> Session | None:
        rows = self._conn.execute(
            self._SESSION_SELECT + " WHERE s.id = ?", (session_id,)
        ).fetchall()
        sessions = self._sessions(rows)
        return sessions[0] if sessions else None

    def active_session(self) -> Session | None:
        rows = self._conn.execute(self._SESSION_SELECT + " WHERE s.end_at IS NULL").fetchall()
        sessions = self._sessions(rows)
        return sessions[0] if sessions else None

    def sessions_between(
        self,
        start: datetime,
        end: datetime,
        project_ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> list[Session]:
        sql = self._SESSION_SELECT + " WHERE s.start_at >= ? AND s.start_at < ?"
        params: list[object] = [to_db(start), to_db(end)]
        if project_ids is not None:
            sql += f" AND s.project_id IN ({','.join('?' * len(project_ids))})"
            params.extend(project_ids)
        if limit is not None:
            # Las mas recientes, devueltas en orden cronologico.
            sql = f"SELECT * FROM ({sql} ORDER BY s.start_at DESC LIMIT ?) ORDER BY start_at"
            params.append(limit)
        else:
            sql += " ORDER BY s.start_at"
        return self._sessions(self._conn.execute(sql, params).fetchall())

    def update_session(
        self,
        session_id: int,
        *,
        project_id: int,
        description: str,
        start_at: datetime,
        duration_s: int,
        end_at: datetime | None,
    ) -> None:
        self._conn.execute(
            "UPDATE sessions SET project_id = ?, description = ?, start_at = ?,"
            " duration_s = ?, end_at = ? WHERE id = ?",
            (
                project_id,
                description,
                to_db(start_at),
                duration_s,
                None if end_at is None else to_db(end_at),
                session_id,
            ),
        )

    def delete_session(self, session_id: int) -> None:
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # --- Pausas -------------------------------------------------------------

    def open_pause(self, session_id: int, start_at: datetime) -> None:
        self._conn.execute(
            "INSERT INTO pauses(session_id, start_at) VALUES (?, ?)",
            (session_id, to_db(start_at)),
        )

    def close_pause(self, session_id: int, end_at: datetime) -> None:
        self._conn.execute(
            "UPDATE pauses SET end_at = ? WHERE session_id = ? AND end_at IS NULL",
            (to_db(end_at), session_id),
        )

    def add_closed_pause(self, session_id: int, start_at: datetime, end_at: datetime) -> None:
        self._conn.execute(
            "INSERT INTO pauses(session_id, start_at, end_at) VALUES (?, ?, ?)",
            (session_id, to_db(start_at), to_db(end_at)),
        )

    def replace_pauses(self, session_id: int, pauses: Sequence[Span]) -> None:
        self._conn.execute("DELETE FROM pauses WHERE session_id = ?", (session_id,))
        for pause in pauses:
            self._conn.execute(
                "INSERT INTO pauses(session_id, start_at, end_at) VALUES (?, ?, ?)",
                (
                    session_id,
                    to_db(pause.start),
                    None if pause.end is None else to_db(pause.end),
                ),
            )

    # --- Reportes guardados -------------------------------------------------

    def add_export(self, month: str, exported_at: datetime) -> None:
        self._conn.execute(
            "INSERT INTO exports(month, exported_at) VALUES (?, ?)", (month, to_db(exported_at))
        )

    def is_month_locked(self, month: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM exports WHERE month = ? LIMIT 1", (month,))
        return row.fetchone() is not None

    def exported_months(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT month FROM exports ORDER BY month")
        return [row["month"] for row in rows]
