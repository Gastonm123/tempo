"""Tempo: la API estable con la logica de negocio.

No pregunta ni imprime nada. Las decisiones llegan como parametros
obligatorios y las fallas esperables se devuelven como `Err`. Despues de cada
escritura avisa al supervisor para que el proceso de aviso relea la base.

Regla interna: cada operacion valida todo antes de escribir, asi nunca queda
una escritura a medias que haya que deshacer.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Concatenate, Literal

from tempo import sessions as calc
from tempo.clock import Clock
from tempo.db import Store
from tempo.models import Client, Project, Session
from tempo.report import (
    CsvRenderer,
    MarkdownRenderer,
    Renderer,
    Report,
    build_report,
    month_bounds,
    render_to_text,
)
from tempo.result import (
    AlreadyPaused,
    ClientArchived,
    ClientHasActiveProjects,
    ClientNotFound,
    EmptyMonth,
    Err,
    FileExists,
    InvalidInterval,
    InvalidName,
    MonthLocked,
    MonthNotClosed,
    NameTaken,
    NoActiveSession,
    NotPaused,
    Ok,
    OpenSessionInMonth,
    ProjectArchived,
    ProjectNotFound,
    Result,
    SessionAlreadyActive,
    SessionExpired,
    SessionIsActive,
    SessionNotFound,
)
from tempo.sessions import Interval, SessionState
from tempo.store_files import ReportStore
from tempo.supervisor import Supervisor

type StartError = SessionAlreadyActive | ProjectNotFound | ProjectArchived
type PauseError = NoActiveSession | AlreadyPaused | SessionExpired
type EditError = SessionNotFound | SessionIsActive | MonthLocked | InvalidInterval | ProjectNotFound
type DeleteError = SessionNotFound | MonthLocked
type ExportError = MonthNotClosed | OpenSessionInMonth | EmptyMonth
type Format = Literal["csv", "md"]

RENDERERS: dict[Format, Renderer] = {"csv": CsvRenderer(), "md": MarkdownRenderer()}


@dataclass(frozen=True, slots=True)
class ActiveStatus:
    session: Session
    state: SessionState


def clean_name(text: str) -> Result[str, InvalidName]:
    name = text.strip()
    return Ok(name) if name else Err(InvalidName(text))


def atomic_write[**P, T, E](
    method: Callable[Concatenate[Tempo, P], Result[T, E]],
) -> Callable[Concatenate[Tempo, P], Result[T, E]]:
    """Escritura atomica de Tempo: transaccion y aviso al supervisor, en ese orden.

    Corre el metodo completo (lectura, validacion y escritura) en una sola transaccion.

    BEGIN IMMEDIATE toma el lock de escritura antes de leer: lo validado no puede
    cambiar hasta el COMMIT, porque SQLite admite un solo escritor. Las fallas se
    devuelven antes de escribir, asi que un Err hace COMMIT de una transaccion vacia.
    El supervisor se avisa despues del COMMIT y solo si hubo Ok: con WAL, el proceso
    de aviso solo ve lo confirmado.

    Las dos responsabilidades van juntas a proposito, para forzar ese orden. Si
    estuvieran en decoradores separados, el orden dependeria de como se apilan en
    cada metodo, y apilarlos al reves (SIGHUP antes del COMMIT) haria que el
    proceso de aviso relea el estado viejo sin que nada lo detecte.
    """

    def wrapper(self: Tempo, /, *args: P.args, **kwargs: P.kwargs) -> Result[T, E]:
        with self._store.transaction():
            result = method(self, *args, **kwargs)
        match result:
            case Ok():
                self._supervisor.sync()
            case Err():
                pass
        return result

    functools.update_wrapper(wrapper, method)  # nombre y docstring; el tipo no cambia
    return wrapper


class Tempo:
    def __init__(
        self,
        store: Store,
        clock: Clock,
        supervisor: Supervisor,
        *,
        saved_reports: ReportStore,
        loose_reports: ReportStore,
    ) -> None:
        """`saved_reports`: reportes guardados (reports/). `loose_reports`: exports
        con --project, que no se guardan ni bloquean el mes (directorio actual)."""
        self._store = store
        self._clock = clock
        self._supervisor = supervisor
        self._saved_reports = saved_reports
        self._loose_reports = loose_reports

    # --- Utilidades de tiempo ---------------------------------------------

    def now(self) -> datetime:
        return self._clock.now()

    @property
    def _tz(self) -> tzinfo | None:
        return self._clock.now().tzinfo

    def local(self, moment: datetime) -> datetime:
        """Convierte un momento a la zona horaria local del reloj."""
        return moment.astimezone(self._tz)

    def month_of(self, moment: datetime) -> str:
        """Mes 'AAAA-MM' (hora local) al que pertenece un momento."""
        return self.local(moment).strftime("%Y-%m")

    def worked_s(self, session: Session) -> int:
        """Segundos trabajados: hasta el fin, o hasta ahora si sigue abierta."""
        if session.end_at is None:
            now = self._clock.now()
            return calc.session_state(
                session.start_at, session.duration_s, session.pauses, now
            ).worked_s
        return calc.worked_seconds(session.start_at, session.pauses, session.end_at)

    def paused_s(self, session: Session) -> int:
        until = session.end_at if session.end_at is not None else self._clock.now()
        return calc.paused_seconds(session.pauses, until)

    # --- Proceso de aviso ---------------------------------------------------

    def ensure_notifier(self) -> None:
        """Asegura que el proceso de aviso este vivo, aunque no haya escrituras.

        La CLI lo llama al inicio de cada comando: asi un proceso que murio (por
        ejemplo al reiniciar WSL) se relanza tambien con `status`, `list` o `report`.
        """
        self._supervisor.sync()

    # --- Sesion activa ------------------------------------------------------

    def _active(self) -> Result[ActiveStatus, NoActiveSession]:
        session = self._store.active_session()
        if session is None:
            return Err(NoActiveSession())
        state = calc.session_state(
            session.start_at, session.duration_s, session.pauses, self._clock.now()
        )
        return Ok(ActiveStatus(session, state))

    def _status_of(self, session_id: int) -> ActiveStatus:
        session = self._store.get_session(session_id)
        assert session is not None
        state = calc.session_state(
            session.start_at, session.duration_s, session.pauses, self._clock.now()
        )
        return ActiveStatus(session, state)

    def status(self) -> Result[ActiveStatus, NoActiveSession]:
        return self._active()

    @atomic_write
    def start(
        self, project_id: int, duration_s: int, description: str
    ) -> Result[ActiveStatus, StartError]:
        project = self._store.get_project(project_id)
        if project is None:
            return Err(ProjectNotFound(str(project_id)))
        if project.archived:
            return Err(ProjectArchived(project.name))
        added = self._store.add_session(
            project_id, description.strip(), self._clock.now(), duration_s
        )
        match added:
            case Err():
                return added
            case Ok(session):
                return Ok(self._status_of(session.id))

    @atomic_write
    def pause(self) -> Result[ActiveStatus, PauseError]:
        match self._active():
            case Err() as missing:
                return missing
            case Ok(active):
                pass
        if active.state.kind == "expired":
            assert active.state.expires_at is not None
            return Err(SessionExpired(active.state.expires_at))
        if active.state.kind == "paused":
            assert active.state.paused_since is not None
            return Err(AlreadyPaused(active.state.paused_since))
        self._store.open_pause(active.session.id, self._clock.now())
        return Ok(self._status_of(active.session.id))

    @atomic_write
    def resume(self) -> Result[ActiveStatus, NoActiveSession | NotPaused]:
        match self._active():
            case Err() as missing:
                return missing
            case Ok(active):
                pass
        if active.state.kind != "paused":
            return Err(NotPaused())
        self._store.close_pause(active.session.id, self._clock.now())
        return Ok(self._status_of(active.session.id))

    @atomic_write
    def extend(self, seconds: int) -> Result[ActiveStatus, NoActiveSession]:
        """Suma tiempo en cualquier estado. Si ya vencio, el hueco no cuenta."""
        match self._active():
            case Err() as missing:
                return missing
            case Ok(active):
                pass
        session = active.session
        now = self._clock.now()
        if active.state.kind == "expired":
            assert active.state.expires_at is not None
            self._store.add_closed_pause(session.id, active.state.expires_at, now)
        self._store.update_session(
            session.id,
            project_id=session.project_id,
            description=session.description,
            start_at=session.start_at,
            duration_s=session.duration_s + seconds,
            end_at=None,
        )
        return Ok(self._status_of(session.id))

    @atomic_write
    def stop(self, description: str | None) -> Result[Session, NoActiveSession]:
        """Cierra la sesion. Fin = ahora, o el vencimiento si ya vencio.

        `description` None conserva la descripcion cargada al iniciar.
        """
        match self._active():
            case Err() as missing:
                return missing
            case Ok(active):
                pass
        session = active.session
        end = calc.effective_end(
            session.start_at, session.duration_s, session.pauses, self._clock.now()
        )
        if active.state.kind == "paused":
            self._store.close_pause(session.id, end)
        self._store.update_session(
            session.id,
            project_id=session.project_id,
            description=session.description if description is None else description.strip(),
            start_at=session.start_at,
            duration_s=session.duration_s,
            end_at=end,
        )
        stopped = self._store.get_session(session.id)
        assert stopped is not None
        return Ok(stopped)

    # --- Sesiones pasadas ---------------------------------------------------

    def list_sessions(
        self, since: datetime, project_ids: Sequence[int] | None = None, limit: int | None = None
    ) -> list[Session]:
        until = self._clock.now() + timedelta(days=1)
        return self._store.sessions_between(since, until, project_ids, limit)

    def sessions_between(
        self, start: datetime, end: datetime, project_ids: Sequence[int] | None = None
    ) -> list[Session]:
        return self._store.sessions_between(start, end, project_ids)

    def get_session(self, session_id: int) -> Result[Session, SessionNotFound]:
        session = self._store.get_session(session_id)
        return Err(SessionNotFound(session_id)) if session is None else Ok(session)

    def is_month_locked(self, month: str) -> bool:
        return self._store.is_month_locked(month)

    @atomic_write
    def edit_session(
        self,
        session_id: int,
        *,
        project_id: int,
        description: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Result[Session, EditError]:
        """Edita una sesion cerrada. El mes de origen y el de destino deben estar abiertos."""
        session = self._store.get_session(session_id)
        if session is None:
            return Err(SessionNotFound(session_id))
        if session.is_open:
            return Err(SessionIsActive(session_id))
        for month in (self.month_of(session.start_at), self.month_of(start_at)):
            if self._store.is_month_locked(month):
                return Err(MonthLocked(month))
        if end_at <= start_at:
            return Err(InvalidInterval(start_at, end_at))
        if self._store.get_project(project_id) is None:
            return Err(ProjectNotFound(str(project_id)))
        # Las pausas se recortan al nuevo intervalo; las que quedan afuera se descartan.
        pauses = [
            Interval(max(p.start, start_at), min(p.end, end_at))
            for p in session.pauses
            if p.end is not None and p.end > start_at and p.start < end_at
        ]
        self._store.update_session(
            session_id,
            project_id=project_id,
            description=description.strip(),
            start_at=start_at,
            duration_s=session.duration_s,
            end_at=end_at,
        )
        self._store.replace_pauses(session_id, pauses)
        edited = self._store.get_session(session_id)
        assert edited is not None
        return Ok(edited)

    @atomic_write
    def delete_session(self, session_id: int) -> Result[None, DeleteError]:
        session = self._store.get_session(session_id)
        if session is None:
            return Err(SessionNotFound(session_id))
        month = self.month_of(session.start_at)
        if self._store.is_month_locked(month):
            return Err(MonthLocked(month))
        self._store.delete_session(session_id)
        return Ok(None)

    # --- Clientes -----------------------------------------------------------

    @atomic_write
    def add_client(self, name: str) -> Result[Client, InvalidName | NameTaken]:
        match clean_name(name):
            case Err() as invalid:
                return invalid
            case Ok(clean):
                pass
        written = self._store.add_client(clean)
        return written

    def find_client(self, name: str) -> Result[Client, ClientNotFound]:
        client = self._store.find_client(name.strip())
        return Err(ClientNotFound(name)) if client is None else Ok(client)

    def list_clients(self, *, include_archived: bool = False) -> list[Client]:
        return self._store.list_clients(include_archived=include_archived)

    def client_locked_month(self, client_id: int) -> str | None:
        """Primer mes exportado con sesiones de algun proyecto del cliente, incluidos
        los archivados. None = el cliente se puede editar."""
        projects = self._store.list_projects(include_archived=True, client_id=client_id)
        if not projects:
            return None
        return self._locked_month([p.id for p in projects])

    @atomic_write
    def edit_client(
        self, client_id: int, name: str
    ) -> Result[None, ClientNotFound | MonthLocked | InvalidName | NameTaken]:
        """Cambia el nombre. No procede si algun proyecto del cliente tiene sesiones en
        un mes exportado: el reporte guardado dejaria de coincidir con la base."""
        if self._store.get_client(client_id) is None:
            return Err(ClientNotFound(str(client_id)))
        locked = self.client_locked_month(client_id)
        if locked is not None:
            return Err(MonthLocked(locked))
        match clean_name(name):
            case Err() as invalid:
                return invalid
            case Ok(clean):
                pass
        written = self._store.rename_client(client_id, clean)
        return written

    def client_projects(self, client_id: int) -> list[Project]:
        """Proyectos activos de un cliente."""
        return self._store.list_projects(include_archived=False, client_id=client_id)

    @atomic_write
    def archive_client(
        self, client_id: int, *, cascade: bool
    ) -> Result[tuple[Project, ...], ClientNotFound | ClientHasActiveProjects]:
        """Archiva un cliente. Con cascade=True archiva tambien sus proyectos activos
        y los devuelve; con cascade=False solo procede si no tiene proyectos activos."""
        if self._store.get_client(client_id) is None:
            return Err(ClientNotFound(str(client_id)))
        projects = tuple(self.client_projects(client_id))
        if projects and not cascade:
            return Err(ClientHasActiveProjects(tuple(p.name for p in projects)))
        for project in projects:
            self._store.set_project_archived(project.id, True)
        self._store.set_client_archived(client_id, True)
        return Ok(projects)

    @atomic_write
    def unarchive_client(self, client_id: int) -> Result[None, ClientNotFound]:
        """Reactiva solo el cliente; sus proyectos se reactivan uno por uno."""
        if self._store.get_client(client_id) is None:
            return Err(ClientNotFound(str(client_id)))
        self._store.set_client_archived(client_id, False)
        return Ok(None)

    # --- Proyectos ----------------------------------------------------------

    @atomic_write
    def add_project(
        self, name: str, client_id: int | None, description: str
    ) -> Result[Project, InvalidName | NameTaken | ClientNotFound | ClientArchived]:
        match clean_name(name):
            case Err() as invalid:
                return invalid
            case Ok(clean):
                pass
        if client_id is not None:
            client = self._store.get_client(client_id)
            if client is None:
                return Err(ClientNotFound(str(client_id)))
            if client.archived:
                return Err(ClientArchived(client.name))
        written = self._store.add_project(clean, client_id, description.strip())
        return written

    def find_project(self, name: str) -> Result[Project, ProjectNotFound]:
        project = self._store.find_project(name.strip())
        return Err(ProjectNotFound(name)) if project is None else Ok(project)

    def find_projects(self, names: Sequence[str]) -> Result[list[Project], ProjectNotFound]:
        found: list[Project] = []
        for name in names:
            match self.find_project(name):
                case Err() as missing:
                    return missing
                case Ok(project):
                    found.append(project)
        return Ok(found)

    def list_projects(self, *, include_archived: bool = False) -> list[Project]:
        return self._store.list_projects(include_archived=include_archived)

    def _locked_month(self, project_ids: Sequence[int]) -> str | None:
        """Primer mes exportado con sesiones de esos proyectos. Los meses son en hora
        local, por eso se recorren los limites de cada uno en vez de filtrar en SQL."""
        for month in self._store.exported_months():
            start, end = month_bounds(month, self._tz)
            if self._store.sessions_between(start, end, project_ids, limit=1):
                return month
        return None

    def project_locked_month(self, project_id: int) -> str | None:
        """Primer mes exportado con sesiones del proyecto. None = se puede editar."""
        return self._locked_month([project_id])

    @atomic_write
    def edit_project(
        self, project_id: int, *, name: str, description: str
    ) -> Result[None, ProjectNotFound | MonthLocked | InvalidName | NameTaken]:
        """Cambia nombre y descripcion; el cliente no cambia nunca. No procede si el
        proyecto tiene sesiones en un mes exportado."""
        if self._store.get_project(project_id) is None:
            return Err(ProjectNotFound(str(project_id)))
        locked = self.project_locked_month(project_id)
        if locked is not None:
            return Err(MonthLocked(locked))
        match clean_name(name):
            case Err() as invalid:
                return invalid
            case Ok(clean):
                pass
        updated = self._store.update_project(project_id, name=clean,
                                             description=description.strip())
        return updated

    @atomic_write
    def archive_project(self, project_id: int) -> Result[None, ProjectNotFound]:
        if self._store.get_project(project_id) is None:
            return Err(ProjectNotFound(str(project_id)))
        self._store.set_project_archived(project_id, True)
        return Ok(None)

    @atomic_write
    def unarchive_project(
        self, project_id: int, *, with_client: bool
    ) -> Result[None, ProjectNotFound | ClientArchived]:
        """Reactiva un proyecto. Si su cliente esta archivado, solo procede con
        with_client=True, que reactiva tambien al cliente."""
        project = self._store.get_project(project_id)
        if project is None:
            return Err(ProjectNotFound(str(project_id)))
        client = None if project.client_id is None else self._store.get_client(project.client_id)
        if client is not None and client.archived and not with_client:
            return Err(ClientArchived(client.name))
        if client is not None and client.archived:
            self._store.set_client_archived(client.id, False)
        self._store.set_project_archived(project_id, False)
        return Ok(None)

    # --- Reportes -----------------------------------------------------------

    def report(
        self, start: datetime, end: datetime, projects: Sequence[Project] | None = None
    ) -> Report:
        """Reporte de las sesiones cerradas que empezaron en [start, end)."""
        ids = None if projects is None else [p.id for p in projects]
        return build_report(
            self._store.sessions_between(start, end, ids),
            tz=self._tz,
            generated_at=self._clock.now(),
            projects=None if projects is None else [p.name for p in projects],
        )

    def month_report(
        self, month: str, projects: Sequence[Project] | None
    ) -> Result[Report, ExportError]:
        """Reporte de un mes exportable: cerrado, sin sesiones abiertas y no vacio."""
        if month >= self.month_of(self._clock.now()):
            return Err(MonthNotClosed(month))
        start, end = month_bounds(month, self._tz)
        ids = None if projects is None else [p.id for p in projects]
        found = self._store.sessions_between(start, end, ids)
        open_session = next((s for s in found if s.is_open), None)
        if open_session is not None:
            return Err(OpenSessionInMonth(month, open_session.id))
        if not found:
            return Err(EmptyMonth(month))
        return Ok(self.report(start, end, projects))

    @atomic_write
    def export_month(self, month: str, fmt: Format) -> Result[Path, ExportError]:
        """Reporte guardado de todos los proyectos. Sobrescribe si ya existe y
        bloquea el mes para ediciones."""
        match self.month_report(month, None):
            case Err() as failed:
                return failed
            case Ok(report):
                pass
        content = render_to_text(RENDERERS[fmt], report)
        # El registro y el archivo van en la misma transaccion (@atomic_write): si escribir
        # el archivo explota, el ROLLBACK descarta la fila y el mes no queda bloqueado.
        self._store.add_export(month, self._clock.now())
        saved = self._saved_reports.save(f"tempo-{month}.{fmt}", content, overwrite=True)
        match saved:
            case Ok(path):
                return Ok(path)
            case Err():
                raise AssertionError("con overwrite=True no puede existir el archivo")

    def export_month_filtered(
        self, month: str, fmt: Format, projects: Sequence[Project], output: str | None
    ) -> Result[Path, ExportError | FileExists]:
        """Export suelto de algunos proyectos: no se guarda ni bloquea el mes, y
        nunca pisa un archivo existente."""
        match self.month_report(month, projects):
            case Err() as failed:
                return failed
            case Ok(report):
                pass
        name = output if output is not None else f"tempo-{month}.{fmt}"
        return self._loose_reports.save(name, render_to_text(RENDERERS[fmt], report),
                                        overwrite=False)
