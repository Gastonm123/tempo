# Dominio: `Tempo`

`core.Tempo` es la API estable de la aplicación. La CLI es una capa delgada encima y los tests la usan directamente.

```python
Tempo(
    store: Store,
    clock: Clock,
    supervisor: Supervisor,
    *,
    saved_reports: ReportStore,   # reportes guardados: bloquean el mes
    loose_reports: ReportStore,   # exports con --project
)
```

Contrato:

- No pregunta ni imprime. Las decisiones del usuario son parámetros obligatorios de solo palabra clave (`cascade`, `with_client`).
- Las fallas esperables se devuelven como `Err` con un tipo concreto. Ver la tabla al final.
- Cada operación que escribe corre completa, validación incluida, en una transacción `BEGIN IMMEDIATE` (`@atomic_write`) y valida antes de la primera escritura. Ver [concurrencia](persistencia.md#concurrencia).
- Después de cada operación que escribe llama a `supervisor.sync()`, para que el proceso de aviso relea la base. Las lecturas no lo llaman; para eso está `ensure_notifier()`.
- La hora local es la zona del reloj inyectado (`clock.now().tzinfo`), no la del sistema. En los tests eso hace los resultados independientes de la máquina.

Los detalles de comportamiento de cada operación están en [sesiones.md](../sesiones.md) y [reportes.md](../reportes.md); acá van las firmas y lo que devuelven.

## Proceso de aviso

| Método | |
|---|---|
| `ensure_notifier() -> None` | `supervisor.sync()` sin escribir nada. La CLI lo llama al inicio de cada comando, así un proceso de aviso caído se relanza también con una lectura |

## Tiempo

| Método | |
|---|---|
| `now() -> datetime` | `clock.now()` |
| `local(moment) -> datetime` | Convierte a la zona del reloj |
| `month_of(moment) -> str` | `AAAA-MM` del momento en hora local |
| `worked_s(session) -> int` | Trabajado hasta el fin, o hasta ahora si sigue abierta |
| `paused_s(session) -> int` | Pausado hasta el fin, o hasta ahora |

## Sesión activa

```python
@dataclass(frozen=True)
class ActiveStatus:
    session: Session
    state: SessionState   # kind, worked_s, remaining_s, expires_at, paused_since
```

| Método | Ok | Err |
|---|---|---|
| `status()` | `ActiveStatus` | `NoActiveSession` |
| `start(project_id, duration_s, description)` | `ActiveStatus` | `ProjectNotFound`, `ProjectArchived`, `SessionAlreadyActive` |
| `pause()` | `ActiveStatus` | `NoActiveSession`, `SessionExpired`, `AlreadyPaused` |
| `resume()` | `ActiveStatus` | `NoActiveSession`, `NotPaused` |
| `extend(seconds)` | `ActiveStatus` | `NoActiveSession` |
| `stop(description \| None)` | `Session` cerrada | `NoActiveSession` |

## Sesiones

| Método | Ok | Err |
|---|---|---|
| `list_sessions(since, project_ids=None, limit=None)` | `list[Session]` con inicio en `[since, ahora + 1 d)` | — |
| `sessions_between(start, end, project_ids=None)` | `list[Session]` | — |
| `get_session(id)` | `Session` | `SessionNotFound` |
| `is_month_locked(month)` | `bool` | — |
| `edit_session(id, *, project_id, description, start_at, end_at)` | `Session` | `SessionNotFound`, `SessionIsActive`, `MonthLocked`, `InvalidInterval`, `ProjectNotFound` |
| `delete_session(id)` | `None` | `SessionNotFound`, `MonthLocked` |

## Clientes

| Método | Ok | Err |
|---|---|---|
| `add_client(name)` | `Client` | `InvalidName`, `NameTaken` |
| `find_client(name)` | `Client` (incluye archivados) | `ClientNotFound` |
| `list_clients(*, include_archived=False)` | `list[Client]` | — |
| `rename_client(id, name)` | `None` | `ClientNotFound`, `InvalidName`, `NameTaken` |
| `client_projects(id)` | `list[Project]` activos | — |
| `archive_client(id, *, cascade)` | `tuple[Project, ...]` archivados en cascada | `ClientNotFound`, `ClientHasActiveProjects` |
| `unarchive_client(id)` | `None` | `ClientNotFound` |

## Proyectos

| Método | Ok | Err |
|---|---|---|
| `add_project(name, client_id \| None, description)` | `Project` | `InvalidName`, `ClientNotFound`, `ClientArchived`, `NameTaken` |
| `find_project(name)` | `Project` (incluye archivados) | `ProjectNotFound` |
| `find_projects(names)` | `list[Project]` en el orden pedido | `ProjectNotFound` del primero que falte |
| `list_projects(*, include_archived=False)` | `list[Project]` | — |
| `rename_project(id, name)` | `None` | `ProjectNotFound`, `InvalidName`, `NameTaken` |
| `archive_project(id)` | `None` | `ProjectNotFound` |
| `unarchive_project(id, *, with_client)` | `None` | `ProjectNotFound`, `ClientArchived` |

## Reportes

| Método | Ok | Err |
|---|---|---|
| `report(start, end, projects=None)` | `Report` de las sesiones cerradas con inicio en `[start, end)` | — |
| `month_report(month, projects)` | `Report` del mes | `MonthNotClosed`, `OpenSessionInMonth`, `EmptyMonth` |
| `export_month(month, fmt)` | `Path` del reporte guardado | las de `month_report` |
| `export_month_filtered(month, fmt, projects, output)` | `Path` del archivo creado | las de `month_report`, `FileExists` |

`fmt` es `Literal["csv", "md"]`. `output=None` usa `tempo-AAAA-MM.<fmt>`.

## Fallas

Todas son dataclasses `frozen` en `result.py`; `DomainError` es su unión.

| Tipo | Campos | Origen |
|---|---|---|
| `InvalidDuration` | `text` | `durations.parse` |
| `InvalidDate` | `text` | parseo de fechas en la CLI |
| `InvalidMonth` | `text` | `report.parse_month` |
| `InvalidName` | `text` | nombre vacío |
| `NameTaken` | `name` | `UNIQUE` de clientes o proyectos |
| `SessionAlreadyActive` | `session_id` | `one_active` |
| `NoActiveSession` | — | |
| `SessionNotFound` | `session_id` | |
| `SessionIsActive` | `session_id` | edición de una sesión abierta |
| `AlreadyPaused` | `since` | |
| `NotPaused` | — | |
| `SessionExpired` | `expired_at` | pausa sobre una sesión vencida |
| `ProjectNotFound` | `name` (o el id como texto) | |
| `ProjectArchived` | `name` | |
| `ClientNotFound` | `name` (o el id como texto) | |
| `ClientArchived` | `name` | |
| `ClientHasActiveProjects` | `projects: tuple[str, ...]` | archivar sin `cascade` |
| `InvalidInterval` | `start`, `end` | `end <= start` al editar |
| `MonthLocked` | `month` | edición o borrado en un mes exportado |
| `MonthNotClosed` | `month` | export del mes actual o futuro |
| `OpenSessionInMonth` | `month`, `session_id` | |
| `EmptyMonth` | `month` | |
| `FileExists` | `path` | export suelto sobre un archivo existente |

`AlertFailed(reason)` también está en `result.py` pero no es parte de `DomainError`: la produce el `Alerter` y solo se registra en el log del proceso de aviso.
