"""CLI de tempo: argumentos, preguntas interactivas y mensajes.

Es una capa delgada sobre `Tempo`: pregunta con `Terminal`, llama a la API
con decisiones explicitas y traduce cada `Err` a un mensaje en espanol.
`main` es el unico lugar que conoce las implementaciones reales.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import assert_never

import tempo as tempo_package
from tempo import durations
from tempo.clock import SystemClock
from tempo.config import Config
from tempo.core import ActiveStatus, Format, Tempo
from tempo.db import SqliteStore
from tempo.durations import format_duration, format_duration_up
from tempo.models import Client, Project, Session
from tempo.report import TerminalRenderer, parse_month, render_to_text, text_table
from tempo.result import (
    AlreadyPaused,
    ClientArchived,
    ClientHasActiveProjects,
    ClientNameTaken,
    ClientNotArchived,
    ClientNotFound,
    DomainError,
    EmptyDescription,
    EmptyMonth,
    Err,
    FileExists,
    InvalidDate,
    InvalidDuration,
    InvalidInterval,
    InvalidMonth,
    InvalidName,
    MonthLocked,
    MonthNotClosed,
    NoActiveSession,
    NotPaused,
    Ok,
    OpenSessionInMonth,
    ProjectArchived,
    ProjectNameTaken,
    ProjectNotArchived,
    ProjectNotFound,
    Result,
    SessionAlreadyActive,
    SessionExpired,
    SessionIsActive,
    SessionNotFound,
)
from tempo.store_files import DiskReportStore
from tempo.supervisor import ProcessSupervisor
from tempo.terminal import Picked, PosixTerminal, Terminal, Typed, Validator

DATE_TIME = "%Y-%m-%d %H:%M"
DATE = "%Y-%m-%d"


@dataclass(frozen=True, slots=True)
class Deps:
    terminal: Terminal
    tempo: Tempo
    list_days: int
    report_days: int


def notifier_supervisor(config: Config) -> ProcessSupervisor:
    """El proceso de aviso se lanza como `python -m tempo.notifier_main`.

    PYTHONPATH apunta a donde esta el paquete: la carpeta del proyecto o el
    ejecutable zipapp instalado, asi funciona en los dos casos.
    """
    package_root = Path(tempo_package.__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    return ProcessSupervisor(
        pid_path=config.pid_path,
        lock_path=config.lock_path,
        log_path=config.home / "notifier.log",
        command=[sys.executable, "-m", "tempo.notifier_main"],
        env=env,
    )


def build_deps(config: Config) -> Deps:
    """Arma las piezas reales. Es el unico lugar que las conoce."""
    tempo = Tempo(
        SqliteStore(config.db_path),
        SystemClock(),
        notifier_supervisor(config),
        saved_reports=DiskReportStore(config.reports_dir),
        loose_reports=DiskReportStore(Path.cwd()),
    )
    return Deps(PosixTerminal(), tempo, config.list_days, config.report_days)


# --- Mensajes ---------------------------------------------------------------


def message(error: DomainError) -> str:
    match error:
        case InvalidDuration(text):
            return f"Duracion invalida: {text!r}."
        case InvalidDate(text):
            return f"Fecha invalida: {text!r}. Usa AAAA-MM-DD."
        case InvalidMonth(text):
            return f"Mes invalido: {text!r}. Usa el numero de mes (10) o AAAA-MM."
        case InvalidName():
            return "El nombre no puede estar vacio."
        case SessionIsActive(session_id):
            return f"La sesion #{session_id} esta en curso y no se puede editar."
        case SessionAlreadyActive(session_id):
            return (f"Ya hay una sesion activa (#{session_id})."
                    " Termina la sesion activa con `tempo stop`.")
        case NoActiveSession():
            return "No hay ninguna sesion activa."
        case SessionNotFound(session_id):
            return f"No existe la sesion #{session_id}."
        case AlreadyPaused(since):
            return f"La sesion ya esta pausada desde las {since.astimezone():%H:%M}."
        case NotPaused():
            return "La sesion no esta pausada."
        case SessionExpired(expired_at):
            return (f"La sesion vencio a las {expired_at.astimezone():%H:%M}."
                    " Usa `tempo extend` o `tempo stop`.")
        case ProjectNotFound(name):
            return f"No existe el proyecto {name!r}."
        case ProjectArchived(name):
            return f"El proyecto {name!r} esta archivado."
        case ClientNotFound(name):
            return f"No existe el cliente {name!r}."
        case ProjectNameTaken(name):
            return f"Ya existe un proyecto llamado {name!r}."
        case ClientNameTaken(name):
            return f"Ya existe un cliente llamado {name!r}."
        case ProjectNotArchived(name):
            return f"El proyecto {name!r} no esta archivado."
        case ClientNotArchived(name):
            return f"El cliente {name!r} no esta archivado."
        case EmptyDescription():
            return "El trabajo realizado no puede estar vacio."
        case ClientHasActiveProjects(projects):
            return f"El cliente tiene proyectos activos: {', '.join(projects)}."
        case ClientArchived(name):
            return f"El cliente {name!r} esta archivado."
        case InvalidInterval():
            return "El fin tiene que ser posterior al inicio."
        case MonthLocked(month):
            return (f"El mes {month} ya se exporto y esta bloqueado:"
                    " sus sesiones no se pueden modificar.")
        case MonthNotClosed(month):
            return f"El mes {month} todavia no termino. Solo se exportan meses terminados."
        case OpenSessionInMonth(month, session_id):
            return (f"La sesion #{session_id} de {month} sigue abierta."
                    " Termina la sesion activa con `tempo stop`.")
        case EmptyMonth(month):
            return f"El mes {month} no tiene sesiones. No se creo ningun archivo."
        case FileExists(path):
            return f"El archivo {path} ya existe. Usa -o para elegir otro nombre."
        case _:
            assert_never(error)


def fail(term: Terminal, error: DomainError) -> int:
    term.error(message(error))
    return 1


# --- Utilidades -------------------------------------------------------------


def when(tempo: Tempo, moment: datetime) -> str:
    return tempo.local(moment).strftime(DATE_TIME)


def parse_local(tempo: Tempo, text: str, fmt: str) -> Result[datetime, InvalidDate]:
    """Lee una fecha tipeada por el usuario, en hora local."""
    try:
        naive = datetime.strptime(text.strip(), fmt)
    except ValueError:
        return Err(InvalidDate(text))
    return Ok(naive.replace(tzinfo=tempo.now().tzinfo))


def describe_status(tempo: Tempo, active: ActiveStatus) -> list[str]:
    session, state = active.session, active.state
    client = f" ({session.client_name})" if session.client_name else ""
    lines = [("Proyecto", f"{session.project_name}{client}")]
    match state.kind:
        case "running":
            assert state.expires_at is not None
            lines.append(("Estado", f"corriendo, vence a las {tempo.local(state.expires_at):%H:%M}"))
        case "paused":
            assert state.paused_since is not None
            elapsed = int((tempo.now() - state.paused_since).total_seconds())
            lines.append(("Estado", f"pausado desde las {tempo.local(state.paused_since):%H:%M}"
                                    f" (hace {format_duration(elapsed)})"))
        case "expired":
            assert state.expires_at is not None
            lines.append(("Estado", f"vencido a las {tempo.local(state.expires_at):%H:%M}"
                                    " (usa `tempo extend` o `tempo stop`)"))
    lines.append(("Restante", format_duration_up(state.remaining_s)))
    lines.append(("Trabajado", format_duration(state.worked_s)))
    lines.append(("Trabajo realizado", session.description))
    return align_fields(lines)


def align_fields(fields: Sequence[tuple[str, str]], indent: str = "") -> list[str]:
    """Una linea "Etiqueta: valor" por campo, con los valores alineados."""
    width = max(len(label) for label, _ in fields) + 2
    return [f"{indent}{label + ':':<{width}}{value}" for label, value in fields]


def write_block(term: Terminal, text: str) -> None:
    for line in text.rstrip("\n").split("\n"):
        term.write(line)


def find_projects(tempo: Tempo, names: Sequence[str] | None) -> Result[list[Project] | None,
                                                                         ProjectNotFound]:
    if not names:
        return Ok(None)
    return tempo.find_projects(names)


# --- Proyectos y clientes interactivos -----------------------------------------


@dataclass(frozen=True, slots=True)
class NewClient:
    """Cliente elegido con "Crear cliente": se crea junto con el proyecto."""

    name: str


def as_validator(check: Callable[[str], DomainError | None]) -> Validator:
    """Adapta una validacion que devuelve una falla del dominio a `Terminal.text`."""
    def validate(text: str) -> str | None:
        error = check(text)
        return None if error is None else message(error)
    return validate


def description_error(text: str) -> DomainError | None:
    return EmptyDescription() if not text.strip() else None


def project_name_error(app: Deps, text: str, current: Project | None = None) -> DomainError | None:
    """Nombre vacio o de otro proyecto (activo o archivado). `current` es el proyecto
    que se esta editando: conservar su propio nombre es valido."""
    name = text.strip()
    if not name:
        return InvalidName(text)
    match app.tempo.find_project(name):
        case Ok(existing) if current is None or existing.id != current.id:
            return ProjectNameTaken(name)
        case _:
            return None


def client_name_error(app: Deps, text: str, current: Client | None = None) -> DomainError | None:
    """Como `project_name_error`, para clientes."""
    name = text.strip()
    if not name:
        return InvalidName(text)
    match app.tempo.find_client(name):
        case Ok(existing) if current is None or existing.id != current.id:
            return ClientNameTaken(name)
        case _:
            return None


def pick_client(app: Deps) -> Result[Client | NewClient | None, DomainError] | None:
    """Elige el cliente de un proyecto nuevo. Ok(None) = sin cliente; None = el
    usuario cancelo."""
    clients = app.tempo.list_clients()
    every_name = [c.name for c in app.tempo.list_clients(include_archived=True)]
    options = ["Omitir", *(c.name for c in clients), "Crear cliente"]
    match app.terminal.choose("Numero de cliente o nombre", options, title="Elige un cliente",
                              names=every_name):
        case Picked(0):
            return Ok(None)
        case Picked(index) if index == len(options) - 1:
            name = app.terminal.text("Nombre del cliente",
                                     validate=as_validator(lambda t: client_name_error(app, t)))
            return Ok(NewClient(name))
        case Picked(index):
            return Ok(clients[index - 1])
        case Typed(name):
            # Los activos estan en la lista: un nombre escrito es de un archivado.
            match app.tempo.find_client(name):
                case Err() as missing:
                    return missing
                case Ok(archived):
                    pass
            if not app.terminal.confirm(f"El cliente {archived.name!r} esta archivado."
                                        " ¿Desarchivarlo?"):
                return None
            match app.tempo.unarchive_client(archived.id):
                case Err() as failed:
                    return failed
                case Ok():
                    return Ok(archived)


def create_project(app: Deps, name: str) -> Result[Project, DomainError] | None:
    """Pide cliente y descripcion y crea el proyecto. None = el usuario cancelo."""
    match pick_client(app):
        case None:
            return None
        case Err() as failed:
            return failed
        case Ok(picked):
            pass
    description = app.terminal.text("Descripcion del proyecto (opcional)")
    match picked:
        case NewClient(client_name):
            match app.tempo.add_client(client_name):
                case Err() as failed:
                    return failed
                case Ok(client):
                    client_id: int | None = client.id
        case Client():
            client_id = picked.id
        case None:
            client_id = None
    return app.tempo.add_project(name, client_id, description)


def new_project(app: Deps) -> Result[Project, DomainError] | None:
    name = app.terminal.text("Nombre del proyecto",
                             validate=as_validator(lambda t: project_name_error(app, t)))
    return create_project(app, name)


def unarchive_project(app: Deps, project: Project) -> Result[None, DomainError] | None:
    """Desarchiva el proyecto; si su cliente tambien esta archivado, pregunta antes
    de desarchivarlo. None = el usuario cancelo."""
    match app.tempo.unarchive_project(project.id, with_client=False):
        case Err(ClientArchived(client)):
            if not app.terminal.confirm(f"El proyecto {project.name!r} depende del cliente"
                                        f" {client!r} que tambien esta archivado."
                                        " ¿Desarchivarlo?"):
                return None
            return app.tempo.unarchive_project(project.id, with_client=True)
        case other:
            return other


def reuse_archived_project(app: Deps, name: str) -> Result[Project, DomainError] | None:
    """Ofrece desarchivar el proyecto escrito por nombre. None = el usuario cancelo."""
    match app.tempo.find_project(name):
        case Err() as missing:
            return missing
        case Ok(project):
            pass
    if not app.terminal.confirm(f"El proyecto {project.name!r} esta archivado."
                                " ¿Desarchivarlo?"):
        return None
    match unarchive_project(app, project):
        case None:
            return None
        case Err() as failed:
            return failed
        case Ok():
            return Ok(project)


def pick_project(app: Deps, keep: Project | None = None) -> Result[Project, DomainError] | None:
    """Elige el proyecto de una sesion: uno activo, uno archivado escrito por nombre
    (ofrece desarchivarlo) o uno nuevo. Con `keep`, la primera opcion lo conserva.
    None = el usuario cancelo."""
    projects = app.tempo.list_projects()
    every_name = [p.name for p in app.tempo.list_projects(include_archived=True)]
    if not every_name:
        app.terminal.write("Todavia no hay proyectos; crea el primero.")
        return new_project(app)
    head = [] if keep is None else [f"Mantener {keep.name}"]
    options = [*head, *(p.name for p in projects), "Crear proyecto"]
    match app.terminal.choose("Numero de proyecto o nombre", options, title="Elige un proyecto",
                              names=every_name):
        case Picked(index) if index == len(options) - 1:
            return new_project(app)
        case Picked(index) if index < len(head):
            assert keep is not None
            return Ok(keep)
        case Picked(index):
            return Ok(projects[index - len(head)])
        case Typed(name):
            # Los activos estan en la lista: un nombre escrito es de un archivado.
            return reuse_archived_project(app, name)


# --- Comandos de sesion -------------------------------------------------------


def cmd_start(app: Deps, args: argparse.Namespace) -> int:
    term, tempo = app.terminal, app.tempo
    match tempo.status():
        case Ok(active):
            return fail(term, SessionAlreadyActive(active.session.id))
        case Err():
            pass
    picked = pick_project(app)
    if picked is None:
        term.write("Cancelado.")
        return 1
    match picked:
        case Err(error):
            return fail(term, error)
        case Ok(project):
            pass
    seconds = term.duration("Duracion")
    description = term.text("Trabajo de esta sesion", validate=as_validator(description_error))
    match tempo.start(project.id, seconds, description):
        case Err(error):
            return fail(term, error)
        case Ok(active):
            assert active.state.expires_at is not None
            client = f" ({project.client_name})" if project.client_name else ""
            term.write("Sesion iniciada.")
            for line in align_fields([("Proyecto", f"{project.name}{client}"),
                                      ("Duracion", format_duration(seconds)),
                                      ("Vence", f"{tempo.local(active.state.expires_at):%H:%M}")],
                                     indent="  "):
                term.write(line)
            return 0


def cmd_status(app: Deps, args: argparse.Namespace) -> int:
    match app.tempo.status():
        case Err(error):
            return fail(app.terminal, error)
        case Ok(active):
            for line in describe_status(app.tempo, active):
                app.terminal.write(line)
            return 0


def cmd_pause(app: Deps, args: argparse.Namespace) -> int:
    match app.tempo.pause():
        case Err(error):
            return fail(app.terminal, error)
        case Ok(active):
            app.terminal.write("Sesion pausada."
                               f" Restante: {format_duration_up(active.state.remaining_s)}.")
            return 0


def cmd_resume(app: Deps, args: argparse.Namespace) -> int:
    match app.tempo.resume():
        case Err(error):
            return fail(app.terminal, error)
        case Ok(active):
            app.terminal.write("Sesion reanudada."
                               f" Restante: {format_duration_up(active.state.remaining_s)}.")
            return 0


def cmd_extend(app: Deps, args: argparse.Namespace) -> int:
    # mypy fija el tipo de `error` en el primer `case Err(error)` y rechazaria los
    # siguientes, que capturan otros tipos de falla: se declara con el tipo comun.
    error: DomainError
    match durations.parse(args.duration):
        case Err(error):
            return fail(app.terminal, error)
        case Ok(seconds):
            pass
    match app.tempo.extend(seconds):
        case Err(error):
            return fail(app.terminal, error)
        case Ok(active):
            app.terminal.write(f"Se agregaron {format_duration(seconds)}."
                               f" Restante: {format_duration_up(active.state.remaining_s)}.")
            return 0


def cmd_stop(app: Deps, args: argparse.Namespace) -> int:
    term, tempo = app.terminal, app.tempo
    match tempo.stop(None):
        case Err(error):
            return fail(term, error)
        case Ok(session):
            assert session.end_at is not None
            term.write(f"Sesion cerrada: {when(tempo, session.start_at)} ->"
                       f" {when(tempo, session.end_at)}, trabajado"
                       f" {format_duration(tempo.worked_s(session))}.")
            return 0


# --- Sesiones pasadas ---------------------------------------------------------


SESSION_HEADERS = ("Id", "Proyecto", "Inicio", "Fin", "Trabajado", "Trabajo realizado")


def session_table(tempo: Tempo, sessions: Sequence[Session], width: int) -> list[str]:
    def cells(session: Session) -> list[str]:
        end = "en curso" if session.end_at is None else when(tempo, session.end_at)
        return [str(session.id), session.project_name, when(tempo, session.start_at), end,
                format_duration(tempo.worked_s(session)), session.description]
    return text_table(SESSION_HEADERS, [cells(s) for s in sessions], {0, 4}, width)


def cmd_list(app: Deps, args: argparse.Namespace) -> int:
    term, tempo = app.terminal, app.tempo
    match find_projects(tempo, args.project):
        case Err(error):
            return fail(term, error)
        case Ok(projects):
            pass
    days = args.days if args.days is not None else app.list_days
    ids = None if projects is None else [p.id for p in projects]
    found = tempo.list_sessions(tempo.now() - timedelta(days=days), ids, args.limit)
    if not found:
        term.write(f"No hay sesiones en los ultimos {days} dias.")
        return 0
    for line in session_table(tempo, found, term.width()):
        term.write(line)
    return 0


def parse_validated(tempo: Tempo, text: str) -> datetime:
    """Fecha y hora de una respuesta que ya paso por la validacion de `Terminal.text`."""
    match parse_local(tempo, text, DATE_TIME):
        case Ok(moment):
            return moment
        case Err():
            raise AssertionError(f"respuesta sin validar: {text!r}")


def cmd_edit(app: Deps, args: argparse.Namespace) -> int:
    term, tempo = app.terminal, app.tempo
    # mypy fija el tipo de `error` en el primer `case Err(error)` y rechazaria los
    # siguientes, que capturan otros tipos de falla: se declara con el tipo comun.
    error: DomainError
    match tempo.get_session(args.id):
        case Err(error):
            return fail(term, error)
        case Ok(session):
            pass
    if session.end_at is None:
        return fail(term, SessionIsActive(session.id))
    month = tempo.month_of(session.start_at)
    if tempo.is_month_locked(month):
        return fail(term, MonthLocked(month))
    match tempo.find_project(session.project_name):
        case Err(error):
            return fail(term, error)
        case Ok(current):
            pass
    for line in session_table(tempo, [session], term.width()):
        term.write(line)
    term.write("Enter conserva el valor actual.")
    match pick_project(app, keep=current):
        case None:
            term.write("Cancelado.")
            return 1
        case Err(error):
            return fail(term, error)
        case Ok(project):
            pass
    description = term.text("Trabajo realizado", default=session.description,
                            validate=as_validator(description_error))

    def start_problem(text: str) -> str | None:
        match parse_local(tempo, text, DATE_TIME):
            case Err():
                return "Fecha invalida. Usa AAAA-MM-DD HH:MM."
            case Ok(moment):
                target = tempo.month_of(moment)
                return message(MonthLocked(target)) if tempo.is_month_locked(target) else None

    start = parse_validated(tempo, term.text("Inicio (AAAA-MM-DD HH:MM)",
                                             default=when(tempo, session.start_at),
                                             validate=start_problem))

    def end_problem(text: str) -> str | None:
        match parse_local(tempo, text, DATE_TIME):
            case Err():
                return "Fecha invalida. Usa AAAA-MM-DD HH:MM."
            case Ok(moment):
                return message(InvalidInterval(start, moment)) if moment <= start else None

    end = parse_validated(tempo, term.text("Fin (AAAA-MM-DD HH:MM)",
                                           default=when(tempo, session.end_at),
                                           validate=end_problem))
    match tempo.edit_session(session.id, project_id=project.id, description=description,
                             start_at=start, end_at=end):
        case Err(error):
            return fail(term, error)
        case Ok(edited):
            term.write(f"Sesion #{edited.id} actualizada.")
            return 0


def cmd_delete(app: Deps, args: argparse.Namespace) -> int:
    term, tempo = app.terminal, app.tempo
    # mypy fija el tipo de `error` en el primer `case Err(error)` y rechazaria los
    # siguientes, que capturan otros tipos de falla: se declara con el tipo comun.
    error: DomainError
    match tempo.get_session(args.id):
        case Err(error):
            return fail(term, error)
        case Ok(session):
            pass
    month = tempo.month_of(session.start_at)
    if tempo.is_month_locked(month):
        return fail(term, MonthLocked(month))
    for line in session_table(tempo, [session], term.width()):
        term.write(line)
    if not term.confirm(f"¿Borrar la sesion #{session.id}?"):
        term.write("Cancelado.")
        return 1
    match tempo.delete_session(session.id):
        case Err(error):
            return fail(term, error)
        case Ok():
            term.write(f"Sesion #{session.id} borrada.")
            return 0


# --- Proyectos ----------------------------------------------------------------


def select_project(app: Deps, name: str | None, *, archived: bool
                   ) -> Result[Project, DomainError] | None:
    """Proyecto sobre el que actua un comando: el del argumento o uno elegido de la
    lista de activos (archived=False) o de archivados (archived=True). En la lista
    tambien se acepta el nombre de cualquier proyecto. None = la lista esta vacia."""
    if name is not None:
        return app.tempo.find_project(name)
    every = app.tempo.list_projects(include_archived=True)
    candidates = [p for p in every if p.archived == archived]
    if not candidates:
        return None
    match app.terminal.choose("Numero de proyecto o nombre", [p.name for p in candidates],
                              title="Elige un proyecto", names=[p.name for p in every]):
        case Picked(index):
            return Ok(candidates[index])
        case Typed(typed):
            return app.tempo.find_project(typed)


def cmd_project_add(app: Deps, args: argparse.Namespace) -> int:
    invalid = project_name_error(app, args.name)
    if invalid is not None:
        return fail(app.terminal, invalid)
    match create_project(app, args.name):
        case None:
            app.terminal.write("Cancelado.")
            return 1
        case Err(error):
            return fail(app.terminal, error)
        case Ok(project):
            app.terminal.write(f"Proyecto {project.name!r} creado.")
            return 0


def cmd_project_list(app: Deps, args: argparse.Namespace) -> int:
    projects = app.tempo.list_projects(include_archived=args.archived)
    if not projects:
        app.terminal.write("No hay proyectos.")
        return 0
    rows = [[p.name, p.client_name or "", "archivado" if p.archived else "activo",
             p.description] for p in projects]
    for line in text_table(("Proyecto", "Cliente", "Estado", "Descripcion"), rows, set(),
                           app.terminal.width()):
        app.terminal.write(line)
    return 0


def cmd_project_edit(app: Deps, args: argparse.Namespace) -> int:
    term = app.terminal
    match select_project(app, args.name, archived=False):
        case None:
            term.error("No hay proyectos activos.")
            return 1
        case Err(error):
            return fail(term, error)
        case Ok(project):
            pass
    locked = app.tempo.project_locked_month(project.id)
    if locked is not None:
        return fail(term, MonthLocked(locked))
    term.write("Enter conserva el valor actual.")
    term.write(f"Cliente: {project.client_name or '(sin cliente)'}")
    name = term.text("Nombre", default=project.name,
                     validate=as_validator(lambda t: project_name_error(app, t, project)))
    description = term.text("Descripcion del proyecto", default=project.description)
    match app.tempo.edit_project(project.id, name=name, description=description):
        case Err(error):
            return fail(term, error)
        case Ok():
            term.write(f"Proyecto {name.strip()!r} actualizado.")
            return 0


def cmd_project_archive(app: Deps, args: argparse.Namespace) -> int:
    term = app.terminal
    match select_project(app, args.name, archived=False):
        case None:
            term.error("No hay proyectos activos.")
            return 1
        case Err(error):
            return fail(term, error)
        case Ok(project):
            pass
    match app.tempo.archive_project(project.id):
        case Err(error):
            return fail(term, error)
        case Ok():
            term.write(f"Proyecto {project.name!r} archivado.")
            return 0


def cmd_project_unarchive(app: Deps, args: argparse.Namespace) -> int:
    term = app.terminal
    match select_project(app, args.name, archived=True):
        case None:
            term.error("No hay proyectos archivados.")
            return 1
        case Err(error):
            return fail(term, error)
        case Ok(project):
            pass
    match unarchive_project(app, project):
        case None:
            term.write("Cancelado.")
            return 1
        case Err(error):
            return fail(term, error)
        case Ok():
            term.write(f"Proyecto {project.name!r} desarchivado.")
            return 0


# --- Clientes -----------------------------------------------------------------


def select_client(app: Deps, name: str | None, *, archived: bool
                  ) -> Result[Client, DomainError] | None:
    """Como `select_project`, para clientes."""
    if name is not None:
        return app.tempo.find_client(name)
    every = app.tempo.list_clients(include_archived=True)
    candidates = [c for c in every if c.archived == archived]
    if not candidates:
        return None
    match app.terminal.choose("Numero de cliente o nombre", [c.name for c in candidates],
                              title="Elige un cliente", names=[c.name for c in every]):
        case Picked(index):
            return Ok(candidates[index])
        case Typed(typed):
            return app.tempo.find_client(typed)


def cmd_client_add(app: Deps, args: argparse.Namespace) -> int:
    match app.tempo.add_client(args.name):
        case Err(error):
            return fail(app.terminal, error)
        case Ok(client):
            app.terminal.write(f"Cliente {client.name!r} creado.")
            return 0


def cmd_client_list(app: Deps, args: argparse.Namespace) -> int:
    clients = app.tempo.list_clients(include_archived=args.archived)
    if not clients:
        app.terminal.write("No hay clientes.")
        return 0
    rows = [[c.name, "archivado" if c.archived else "activo"] for c in clients]
    for line in text_table(("Cliente", "Estado"), rows, set(), app.terminal.width()):
        app.terminal.write(line)
    return 0


def cmd_client_edit(app: Deps, args: argparse.Namespace) -> int:
    term = app.terminal
    match select_client(app, args.name, archived=False):
        case None:
            term.error("No hay clientes activos.")
            return 1
        case Err(error):
            return fail(term, error)
        case Ok(client):
            pass
    locked = app.tempo.client_locked_month(client.id)
    if locked is not None:
        return fail(term, MonthLocked(locked))
    term.write("Enter conserva el valor actual.")
    name = term.text("Nombre", default=client.name,
                     validate=as_validator(lambda t: client_name_error(app, t, client)))
    match app.tempo.edit_client(client.id, name):
        case Err(error):
            return fail(term, error)
        case Ok():
            term.write(f"Cliente {name.strip()!r} actualizado.")
            return 0


def cmd_client_archive(app: Deps, args: argparse.Namespace) -> int:
    term = app.terminal
    match select_client(app, args.name, archived=False):
        case None:
            term.error("No hay clientes activos.")
            return 1
        case Err(error):
            return fail(term, error)
        case Ok(client):
            pass
    projects = app.tempo.client_projects(client.id)
    if projects:
        names = ", ".join(p.name for p in projects)
        if not term.confirm(f"Se van a archivar tambien sus proyectos: {names}. ¿Continuar?"):
            term.write("Cancelado.")
            return 1
    match app.tempo.archive_client(client.id, cascade=bool(projects)):
        case Err(error):
            return fail(term, error)
        case Ok():
            term.write(f"Cliente {client.name!r} archivado.")
            return 0


def cmd_client_unarchive(app: Deps, args: argparse.Namespace) -> int:
    term = app.terminal
    match select_client(app, args.name, archived=True):
        case None:
            term.error("No hay clientes archivados.")
            return 1
        case Err(error):
            return fail(term, error)
        case Ok(client):
            pass
    match app.tempo.unarchive_client(client.id):
        case Err(error):
            return fail(term, error)
        case Ok():
            term.write(f"Cliente {client.name!r} desarchivado. Sus proyectos siguen archivados:"
                       " desarchivalos con `tempo project unarchive`.")
            return 0


# --- Reportes -----------------------------------------------------------------


def cmd_report(app: Deps, args: argparse.Namespace) -> int:
    term, tempo = app.terminal, app.tempo
    # mypy fija el tipo de `error` en el primer `case Err(error)` y rechazaria los
    # siguientes, que capturan otros tipos de falla: se declara con el tipo comun.
    error: DomainError
    match find_projects(tempo, args.project):
        case Err(error):
            return fail(term, error)
        case Ok(projects):
            pass
    if args.month is not None:
        return export(app, args, projects)
    if args.from_date is not None:
        match parse_local(tempo, args.from_date, DATE):
            case Err(error):
                return fail(term, error)
            case Ok(start):
                pass
        end = tempo.now() + timedelta(days=1)
        if args.to_date is not None:
            match parse_local(tempo, args.to_date, DATE):
                case Err(error):
                    return fail(term, error)
                case Ok(last_day):
                    end = last_day + timedelta(days=1)  # --to incluye ese dia
    else:
        days = args.days if args.days is not None else app.report_days
        start, end = tempo.now() - timedelta(days=days), tempo.now() + timedelta(days=1)
    report = tempo.report(start, end, projects)
    write_block(term, render_to_text(TerminalRenderer(term.width()), report))
    return 0


def export(app: Deps, args: argparse.Namespace, projects: list[Project] | None) -> int:
    term, tempo = app.terminal, app.tempo
    # mypy fija el tipo de `error` en el primer `case Err(error)` y rechazaria los
    # siguientes, que capturan otros tipos de falla: se declara con el tipo comun.
    error: DomainError
    match parse_month(args.month, tempo.local(tempo.now())):
        case Err(error):
            return fail(term, error)
        case Ok(month):
            pass
    fmt: Format = "csv" if args.csv else "md"
    if projects is None:
        match tempo.export_month(month, fmt):
            case Err(error):
                return fail(term, error)
            case Ok(path):
                term.write(f"Reporte guardado en {path}. El mes {month} quedo bloqueado.")
                return 0
    match tempo.export_month_filtered(month, fmt, projects, args.output):
        case Err(error):
            return fail(term, error)
        case Ok(path):
            term.write(f"Reporte guardado en {path}.")
            return 0


# --- Argumentos ---------------------------------------------------------------


# argparse pasa sus propios textos por gettext. Esta tabla los traduce; los que
# no estan (errores de programacion al armar el parser) quedan en ingles.
ARGPARSE_TEXTS = {
    "usage: ": "uso: ",
    "positional arguments": "argumentos",
    "options": "opciones",
    "show this help message and exit": "mostrar esta ayuda y salir",
    "%(prog)s: error: %(message)s\n": "%(prog)s: error: %(message)s\n",
    "argument %(argument_name)s: %(message)s": "argumento %(argument_name)s: %(message)s",
    "the following arguments are required: %s": "faltan los argumentos: %s",
    "one of the arguments %s is required": "falta uno de estos argumentos: %s",
    "unrecognized arguments: %s": "argumentos desconocidos: %s",
    "invalid choice: %(value)r (choose from %(choices)s)":
        "opcion invalida: %(value)r (opciones: %(choices)s)",
    "invalid %(type)s value: %(value)r": "valor invalido: %(value)r",
    "expected one argument": "falta el valor",
    "expected at most one argument": "se espera a lo sumo un valor",
    "expected at least one argument": "se espera al menos un valor",
    "not allowed with argument %s": "no se combina con %s",
    "ambiguous option: %(option)s could match %(matches)s":
        "opcion ambigua: %(option)s puede ser %(matches)s",
}


def translate_argparse() -> None:
    """Reemplaza la funcion de traduccion de argparse (`argparse._`, el gettext
    que usa para sus textos) por la tabla `ARGPARSE_TEXTS`. Tiene que correr
    antes de crear los parsers: algunos textos se traducen al construirlos."""
    setattr(argparse, "_", lambda text: ARGPARSE_TEXTS.get(text, text))


def positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("tiene que ser mayor a cero")
    return value


def build_parser(list_days: int, report_days: int) -> argparse.ArgumentParser:
    """`list_days` y `report_days` son los valores por defecto de --days, que se
    muestran en la ayuda."""
    translate_argparse()
    parser = argparse.ArgumentParser(prog="tempo", description="Gestor de sesiones de trabajo.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMANDO")

    def add(name: str, handler: Callable[[Deps, argparse.Namespace], int], text: str,
            group: argparse._SubParsersAction[argparse.ArgumentParser] = commands,
            ) -> argparse.ArgumentParser:
        sub = group.add_parser(name, help=text, description=text)
        # command_parser: el parser mas especifico, para errores con su propio uso.
        sub.set_defaults(handler=handler, command_parser=sub)
        return sub

    session_id = "numero de la sesion (ver `tempo list`)"
    add("start", cmd_start, "iniciar una sesion")
    add("status", cmd_status, "ver el estado y el tiempo restante de la sesion activa")
    add("pause", cmd_pause, "pausar la sesion activa")
    add("resume", cmd_resume, "reanudar la sesion pausada")
    add("extend", cmd_extend, "agregar tiempo a la sesion activa").add_argument(
        "duration", metavar="DURACION", help="tiempo a agregar, p. ej. 30m o 1h")
    add("stop", cmd_stop, "terminar la sesion activa")

    listing = add("list", cmd_list, "listar sesiones")
    listing.add_argument("--project", action="append", metavar="NOMBRE",
                         help="filtrar por proyecto (repetible)")
    listing.add_argument("--days", type=positive_int, metavar="DIAS",
                         help=f"dias hacia atras (por defecto {list_days})")
    listing.add_argument("--limit", type=positive_int, metavar="CANTIDAD",
                         help="maximo de sesiones")

    add("edit", cmd_edit, "editar una sesion cerrada").add_argument(
        "id", type=int, metavar="ID", help=session_id)
    add("delete", cmd_delete, "borrar una sesion").add_argument(
        "id", type=int, metavar="ID", help=session_id)

    project = add("project", lambda app, args: 0, "gestionar proyectos")
    project_cmds = project.add_subparsers(dest="action", required=True, metavar="ACCION")
    project_name = "nombre del proyecto; sin nombre muestra la lista"
    add("add", cmd_project_add, "crear un proyecto", project_cmds).add_argument(
        "name", metavar="NOMBRE", help="nombre del proyecto")
    add("list", cmd_project_list, "listar proyectos", project_cmds).add_argument(
        "--archived", action="store_true", help="incluir archivados")
    add("edit", cmd_project_edit, "editar nombre y descripcion de un proyecto",
        project_cmds).add_argument("name", nargs="?", metavar="NOMBRE", help=project_name)
    add("archive", cmd_project_archive, "archivar un proyecto", project_cmds).add_argument(
        "name", nargs="?", metavar="NOMBRE", help=project_name)
    add("unarchive", cmd_project_unarchive, "desarchivar un proyecto",
        project_cmds).add_argument("name", nargs="?", metavar="NOMBRE", help=project_name)

    client = add("client", lambda app, args: 0, "gestionar clientes")
    client_cmds = client.add_subparsers(dest="action", required=True, metavar="ACCION")
    client_name = "nombre del cliente; sin nombre muestra la lista"
    add("add", cmd_client_add, "crear un cliente", client_cmds).add_argument(
        "name", metavar="NOMBRE", help="nombre del cliente")
    add("list", cmd_client_list, "listar clientes", client_cmds).add_argument(
        "--archived", action="store_true", help="incluir archivados")
    add("edit", cmd_client_edit, "editar el nombre de un cliente",
        client_cmds).add_argument("name", nargs="?", metavar="NOMBRE", help=client_name)
    add("archive", cmd_client_archive, "archivar un cliente y sus proyectos",
        client_cmds).add_argument("name", nargs="?", metavar="NOMBRE", help=client_name)
    add("unarchive", cmd_client_unarchive, "desarchivar un cliente",
        client_cmds).add_argument("name", nargs="?", metavar="NOMBRE", help=client_name)

    report = add("report", cmd_report, "reporte de horas")
    report.add_argument("--project", action="append", metavar="NOMBRE",
                        help="filtrar por proyecto (repetible)")
    report.add_argument("--days", type=positive_int, metavar="DIAS",
                        help=f"dias hacia atras (por defecto {report_days})")
    report.add_argument("--from", dest="from_date", metavar="AAAA-MM-DD",
                        help="desde ese dia, inclusive")
    report.add_argument("--to", dest="to_date", metavar="AAAA-MM-DD",
                        help="hasta ese dia, inclusive (requiere --from)")
    report.add_argument("--month", metavar="MES", help="exportar un mes: 10 o AAAA-MM")
    formats = report.add_mutually_exclusive_group()
    formats.add_argument("--csv", action="store_true", help="exportar en CSV")
    formats.add_argument("--md", action="store_true", help="exportar en Markdown")
    report.add_argument("-o", dest="output", metavar="SALIDA",
                        help="archivo de salida (solo con --project)")
    return parser


def validate_report_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Combinaciones de opciones de `report` que argparse no puede expresar solo."""
    exporting = args.month is not None
    if exporting and not (args.csv or args.md):
        parser.error("--month necesita --csv o --md")
    if (args.csv or args.md) and not exporting:
        parser.error("--csv y --md necesitan --month")
    if args.output is not None and not (exporting and args.project):
        parser.error("-o solo se usa al exportar con --project")
    if exporting and (args.days or args.from_date or args.to_date):
        parser.error("--month no se combina con --days, --from ni --to")
    if args.days is not None and (args.from_date or args.to_date):
        parser.error("--days no se combina con --from ni --to")
    if args.to_date is not None and args.from_date is None:
        parser.error("--to necesita --from")


def main(argv: Sequence[str] | None = None, deps: Deps | None = None) -> int:
    config: Config | None = None
    if deps is None:
        config = Config.from_env(os.environ)
        parser = build_parser(config.list_days, config.report_days)
    else:
        parser = build_parser(deps.list_days, deps.report_days)
    args = parser.parse_args(argv)
    if args.command == "report":
        validate_report_args(args.command_parser, args)
    if config is not None:
        config.home.mkdir(parents=True, exist_ok=True)
        deps = build_deps(config)
    assert deps is not None
    deps.tempo.ensure_notifier()
    handler: Callable[[Deps, argparse.Namespace], int] = args.handler
    return handler(deps, args)


def entry() -> None:
    """Punto de entrada del ejecutable (`python -m tempo` o el zipapp instalado)."""
    try:
        code = main()
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C o Ctrl+D en una pregunta: no es un error del programa.
        print("\nCancelado.", file=sys.stderr)
        code = 130
    sys.exit(code)
