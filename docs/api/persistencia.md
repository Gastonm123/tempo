# Persistencia

`db.py` define la interfaz `Store` y su única implementación, `SqliteStore`. Es el único módulo que escribe SQL.

## Esquema

```mermaid
erDiagram
    clients ||--o{ projects : "client_id (opcional)"
    projects ||--o{ sessions : "project_id"
    sessions ||--o{ pauses : "session_id, cascade"
    clients {
        INTEGER id PK
        TEXT name UK
        INTEGER archived
    }
    projects {
        INTEGER id PK
        TEXT name UK
        INTEGER client_id FK
        TEXT description
        INTEGER archived
    }
    sessions {
        INTEGER id PK
        INTEGER project_id FK
        TEXT description
        TEXT start_at
        INTEGER duration_s
        TEXT end_at
    }
    pauses {
        INTEGER id PK
        INTEGER session_id FK
        TEXT start_at
        TEXT end_at
        INTEGER reminded
    }
    exports {
        INTEGER id PK
        TEXT month
        TEXT exported_at
    }
```

```sql
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
CREATE UNIQUE INDEX one_active ON sessions((1)) WHERE end_at IS NULL;
CREATE INDEX sessions_start ON sessions(start_at);

CREATE TABLE pauses (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    start_at   TEXT    NOT NULL,
    end_at     TEXT,
    reminded   INTEGER NOT NULL DEFAULT 0 CHECK (reminded IN (0, 1)),
    CHECK (end_at IS NULL OR end_at >= start_at)
);
CREATE UNIQUE INDEX one_open_pause ON pauses(session_id) WHERE end_at IS NULL;

CREATE TABLE exports (
    id          INTEGER PRIMARY KEY,
    month       TEXT NOT NULL,
    exported_at TEXT NOT NULL
);
CREATE INDEX exports_month ON exports(month);
```

### Detalles

**`one_active`.** Un índice único sobre la expresión constante `(1)`, parcial a `end_at IS NULL`: todas las sesiones abiertas tendrían la misma clave, así que puede existir una sola. Las cerradas no entran en el índice. `one_open_pause` aplica la misma idea por sesión.

**Fechas.** `start_at`, `end_at` y `exported_at` son texto ISO-8601 en UTC con resolución de segundos, por ejemplo `2026-11-09T13:00:00+00:00` (`to_db`/`from_db`). Al tener todas el mismo formato y offset, el orden lexicográfico coincide con el cronológico: los `CHECK (end_at >= start_at)`, el índice `sessions_start` y los rangos de `sessions_between` comparan texto. `from_db` devuelve `datetime` con zona UTC; la conversión a hora local es responsabilidad de quien muestra.

**`exports.month`.** `AAAA-MM` en hora local, calculado por `Tempo.month_of`. Es la clave de bloqueo; `exported_at` es informativo.

**Borrados.** Solo se borran sesiones (con sus pausas, por la cascada). Clientes y proyectos se archivan, nunca se borran, por eso sus claves foráneas no declaran acción.

**`reminded`.** Lo marca el proceso de aviso al mandar el aviso de pausa larga. `replace_pauses` lo vuelve a 0, lo que solo ocurre al editar sesiones cerradas.

### Conexión y migraciones

| Ajuste | Valor | Motivo |
|---|---|---|
| `isolation_level` | `None` | Autocommit fuera de `transaction()`; las transacciones se abren explícitamente |
| `foreign_keys` | `ON` | SQLite las ignora por defecto |
| `busy_timeout` | 5000 ms | Un segundo proceso espera el lock en lugar de fallar con `database is locked` |
| `journal_mode` | `WAL` (solo en archivo) | Lectores y un escritor concurrentes; el proceso de aviso lee mientras la CLI escribe |
| `row_factory` | `sqlite3.Row` | Acceso por nombre de columna |

La versión del esquema vive en `PRAGMA user_version`. Si es 0, `_migrate` crea el esquema completo y la pone en 1. El script corre con `executescript`, que hace `COMMIT` de cualquier transacción pendiente antes de ejecutar, así que el `BEGIN IMMEDIATE`/`COMMIT` va dentro del propio script. Una versión futura del esquema se agrega como otra rama de `_migrate`.

### Transacciones

`transaction()` devuelve un context manager:

- Abre `BEGIN IMMEDIATE`, que toma el lock de escritura al empezar. Si otro proceso lo tiene, espera hasta `busy_timeout`. Así la operación nunca falla a mitad de camino por lock.
- Si ya hay una transacción abierta en la conexión, se une a ella sin abrir otra.
- Ante cualquier excepción hace `ROLLBACK` y la vuelve a lanzar.
- Al salir normalmente hace `COMMIT`.

Si el proceso muere con una transacción abierta, SQLite la descarta al reabrir la base.

## `Store`

Los métodos de escritura no abren transacciones propias: las agrupa quien llama (`Tempo`) con `transaction()`. Los que pueden violar una restricción esperable devuelven `Result`; cualquier otra `IntegrityError` se vuelve a lanzar.

### Clientes

| Método | Notas |
|---|---|
| `add_client(name) -> Result[Client, NameTaken]` | `IntegrityError` sobre `clients.name` → `NameTaken` |
| `get_client(id) -> Client \| None` | |
| `find_client(name) -> Client \| None` | Búsqueda exacta, incluye archivados |
| `list_clients(*, include_archived) -> list[Client]` | Ordenados por nombre |
| `rename_client(id, name) -> Result[None, NameTaken]` | |
| `set_client_archived(id, archived)` | |

### Proyectos

| Método | Notas |
|---|---|
| `add_project(name, client_id, description) -> Result[Project, NameTaken]` | |
| `get_project(id)`, `find_project(name) -> Project \| None` | `client_name` viene de un `LEFT JOIN` |
| `list_projects(*, include_archived, client_id=None) -> list[Project]` | Filtro opcional por cliente; ordenados por nombre |
| `rename_project(id, name) -> Result[None, NameTaken]` | |
| `set_project_archived(id, archived)` | |

### Sesiones

| Método | Notas |
|---|---|
| `add_session(project_id, description, start_at, duration_s) -> Result[Session, SessionAlreadyActive]` | `IntegrityError` de `one_active` → `SessionAlreadyActive(id de la activa)` |
| `get_session(id)`, `active_session() -> Session \| None` | Con proyecto, cliente y pausas |
| `sessions_between(start, end, project_ids=None, limit=None) -> list[Session]` | `start_at` en `[start, end)`, orden cronológico. Con `limit`, las N más recientes (subconsulta `DESC LIMIT`), devueltas en orden cronológico |
| `update_session(id, *, project_id, description, start_at, duration_s, end_at)` | Reemplaza todos los campos; `end_at=None` reabre |
| `delete_session(id)` | Las pausas caen por cascada |

Las pausas de un conjunto de sesiones se cargan con una sola consulta `WHERE session_id IN (...)`, ordenadas por inicio.

### Pausas y exports

| Método | Notas |
|---|---|
| `open_pause(session_id, start_at)` | `end_at = NULL` |
| `close_pause(session_id, end_at)` | Cierra la pausa abierta, si existe |
| `add_closed_pause(session_id, start_at, end_at)` | Usado por `extend` para el hueco posterior al vencimiento |
| `replace_pauses(session_id, pauses: Sequence[Span])` | Borra y reinserta; usado por `edit` |
| `mark_pause_reminded(pause_id)` | |
| `add_export(month, exported_at)` | |
| `is_month_locked(month) -> bool` | Existe alguna fila en `exports` para ese mes |

## Modelos

Dataclasses inmutables (`frozen=True, slots=True`) en `models.py`:

```python
Client(id, name, archived)
Project(id, name, client_id, client_name, description, archived)
Pause(id, start, end, reminded)                      # implementa Span
Session(id, project_id, project_name, client_name, description,
        start_at, duration_s, end_at, pauses: tuple[Pause, ...])   # .is_open
```

`Session` trae desnormalizados los nombres de proyecto y cliente, que es lo que necesitan todas las vistas.
