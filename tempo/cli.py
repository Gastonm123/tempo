"""CLI de tempo: argumentos, preguntas interactivas y mensajes.

Es una capa delgada sobre `Tempo`: pregunta con `Terminal`, llama a la API
con decisiones explicitas y traduce cada `Err` a un mensaje en espanol.
`main` es el unico lugar que conoce las implementaciones reales.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import assert_never

import tempo as tempo_package
from tempo import durations
from tempo.alerts import (
    Alerter,
    FirstWorkingAlerter,
    NotifySendAlerter,
    NullAlerter,
    WindowsToastAlerter,
)
from tempo.clock import SystemClock
from tempo.config import Config
from tempo.core import ActiveStatus, Format, Tempo
from tempo.db import SqliteStore
from tempo.durations import format_hm, format_hm_up
from tempo.models import Client, Project, Session
from tempo.notifier import Notifier
from tempo.report import TerminalRenderer, parse_month, render_to_text, text_table
from tempo.result import (
    AlreadyPaused,
    ClientArchived,
    ClientHasActiveProjects,
    ClientNotFound,
    DomainError,
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
from tempo.store_files import DiskReportStore
from tempo.supervisor import ProcessSupervisor
from tempo.terminal import Picked, PosixTerminal, Terminal, Typed

DATE_TIME = "%Y-%m-%d %H:%M"
DATE = "%Y-%m-%d"


@dataclass(frozen=True, slots=True)
class Deps:
    terminal: Terminal
    tempo: Tempo
    list_days: int
    report_days: int


def notifier_supervisor(config: Config) -> ProcessSupervisor:
    """El proceso de aviso se lanza como `python -m tempo notifier`.

    PYTHONPATH apunta a donde esta el paquete: la carpeta del proyecto o el
    ejecutable zipapp instalado, asi funciona en los dos casos.
    """
    package_root = Path(tempo_package.__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    return ProcessSupervisor(
        pid_path=config.pid_path,
        lock_path=config.lock_path,
        log_path=config.home / "notifier.log",
        command=[sys.executable, "-m", "tempo", "notifier"],
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


def build_alerter(config: Config) -> Alerter:
    if not config.notify_enabled:
        return NullAlerter()
    return FirstWorkingAlerter([WindowsToastAlerter(), NotifySendAlerter()])


def serve_notifier(config: Config) -> int:
    """Cuerpo del proceso de aviso. SIGHUP lo despierta para releer la base."""
    wake = threading.Event()
    # Sin este handler, SIGHUP terminaria el proceso (es su accion por defecto).
    signal.signal(signal.SIGHUP, lambda signum, frame: wake.set())
    # El supervisor lo lanza con SIGHUP bloqueada; al desbloquearla, una senal
    # que haya llegado durante el arranque se entrega ahora al handler.
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGHUP})
    notifier = Notifier(SqliteStore(config.db_path), SystemClock(), build_alerter(config),
                        config.pause_reminder)
    notifier.run(wake)
    return 0


# --- Mensajes ---------------------------------------------------------------


def message(error: DomainError) -> str:
    match error:
        case InvalidDuration(text):
            return f"Duración inválida: {text!r}. Usá por ejemplo 1h, 90m o 1h30m."
        case InvalidDate(text):
            return f"Fecha inválida: {text!r}. Usá AAAA-MM-DD o AAAA-MM-DD HH:MM."
        case InvalidMonth(text):
            return f"Mes inválido: {text!r}. Usá el número (10) o AAAA-MM (2025-10)."
        case InvalidName():
            return "El nombre no puede estar vacío."
        case SessionIsActive(session_id):
            return f"La sesión #{session_id} está en curso; usá pause, extend o stop."
        case SessionAlreadyActive(session_id):
            return f"Ya hay una sesión activa (#{session_id}). Detenela con `tempo stop`."
        case NoActiveSession():
            return "No hay ninguna sesión activa."
        case SessionNotFound(session_id):
            return f"No existe la sesión #{session_id}."
        case AlreadyPaused(since):
            return f"La sesión ya está pausada desde las {since.astimezone():%H:%M}."
        case NotPaused():
            return "La sesión no está pausada."
        case SessionExpired(expired_at):
            return (f"La sesión venció a las {expired_at.astimezone():%H:%M}."
                    " Usá `tempo extend` o `tempo stop`.")
        case ProjectNotFound(name):
            return f"No existe el proyecto {name!r}."
        case ProjectArchived(name):
            return f"El proyecto {name!r} está archivado."
        case ClientNotFound(name):
            return f"No existe el cliente {name!r}."
        case NameTaken(name):
            return f"Ya existe uno con el nombre {name!r}."
        case ClientHasActiveProjects(projects):
            return f"El cliente tiene proyectos activos: {', '.join(projects)}."
        case ClientArchived(name):
            return f"El cliente {name!r} está archivado."
        case InvalidInterval():
            return "El fin tiene que ser posterior al inicio."
        case MonthLocked(month):
            return f"El mes {month} tiene un reporte guardado y no se puede modificar."
        case MonthNotClosed(month):
            return f"El mes {month} todavía no terminó; solo se exportan meses cerrados."
        case OpenSessionInMonth(month, session_id):
            return f"La sesión #{session_id} de {month} sigue abierta; detenela antes de exportar."
        case EmptyMonth(month):
            return f"El mes {month} no tiene registros; no se creó ningún archivo."
        case FileExists(path):
            return f"El archivo {path} ya existe; elegí otro nombre con -o."
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
    lines = [f"Proyecto:   {session.project_name}{client}"]
    match state.kind:
        case "running":
            assert state.expires_at is not None
            lines.append(f"Estado:     corriendo, vence a las {tempo.local(state.expires_at):%H:%M}")
        case "paused":
            assert state.paused_since is not None
            elapsed = int((tempo.now() - state.paused_since).total_seconds())
            lines.append(f"Estado:     pausada desde las {tempo.local(state.paused_since):%H:%M}"
                         f" (hace {format_hm(elapsed)})")
        case "expired":
            assert state.expires_at is not None
            lines.append(f"Estado:     vencida a las {tempo.local(state.expires_at):%H:%M}"
                         " (usá `tempo extend` o `tempo stop`)")
    lines.append(f"Restante:   {format_hm_up(state.remaining_s)}")
    lines.append(f"Trabajado:  {format_hm(state.worked_s)}")
    if session.description:
        lines.append(f"Trabajo:    {session.description}")
    return lines


def write_block(term: Terminal, text: str) -> None:
    for line in text.rstrip("\n").split("\n"):
        term.write(line)


def find_projects(tempo: Tempo, names: Sequence[str] | None) -> Result[list[Project] | None,
                                                                         ProjectNotFound]:
    if not names:
        return Ok(None)
    return tempo.find_projects(names)


# --- Proyectos y clientes interactivos -----------------------------------------


def pick_client(app: Deps) -> Result[Client | None, DomainError]:
    """Elige el cliente de un proyecto nuevo: uno existente, uno nuevo o ninguno."""
    clients = app.tempo.list_clients()
    options = ["(sin cliente)", *(c.name for c in clients)]
    match app.terminal.choose("Cliente", options, allow_typed=True):
        case Picked(0):
            return Ok(None)
        case Picked(index):
            return Ok(clients[index - 1])
        case Typed(name):
            match app.tempo.find_client(name):
                case Ok(existing):
                    # No esta en la lista de activos: esta archivado.
                    return Err(ClientArchived(existing.name))
                case Err():
                    return app.tempo.add_client(name)


def create_project(app: Deps, name: str) -> Result[Project, DomainError]:
    match pick_client(app):
        case Err() as failed:
            return failed
        case Ok(client):
            pass
    description = app.terminal.text("Descripción del proyecto (opcional)")
    return app.tempo.add_project(name, None if client is None else client.id, description)


def unarchive_project(app: Deps, project: Project) -> Result[None, DomainError]:
    match app.tempo.unarchive_project(project.id, with_client=False):
        case Err(ClientArchived(client)):
            if not app.terminal.confirm(f"Su cliente {client!r} también está archivado."
                                        " ¿Reactivarlo?"):
                return Err(ClientArchived(client))
            return app.tempo.unarchive_project(project.id, with_client=True)
        case other:
            return other


def pick_project(app: Deps) -> Result[Project, DomainError] | None:
    """Elige el proyecto de una sesion nueva. None = el usuario cancelo."""
    projects = app.tempo.list_projects()
    if projects:
        choice = app.terminal.choose("Proyecto", [p.name for p in projects], allow_typed=True)
    else:
        app.terminal.write("Todavía no hay proyectos; creá el primero.")
        choice = Typed(app.terminal.text("Nombre del proyecto"))
    match choice:
        case Picked(index):
            return Ok(projects[index])
        case Typed(name):
            match app.tempo.find_project(name):
                case Ok(archived):
                    if not app.terminal.confirm(f"El proyecto {archived.name!r} está archivado."
                                                " ¿Desarchivarlo?"):
                        return None
                    match unarchive_project(app, archived):
                        case Err() as failed:
                            return failed
                        case Ok():
                            return Ok(archived)
                case Err():
                    app.terminal.write(f"Proyecto nuevo: {name}")
                    return create_project(app, name)


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
    seconds = term.duration("Duración")
    description = term.text("Trabajo a realizar")
    match tempo.start(project.id, seconds, description):
        case Err(error):
            return fail(term, error)
        case Ok(active):
            assert active.state.expires_at is not None
            term.write(f"Sesión iniciada en {project.name}: {format_hm(seconds)},"
                       f" vence a las {tempo.local(active.state.expires_at):%H:%M}.")
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
            app.terminal.write(f"Sesión pausada. Restante: {format_hm_up(active.state.remaining_s)}.")
            return 0


def cmd_resume(app: Deps, args: argparse.Namespace) -> int:
    match app.tempo.resume():
        case Err(error):
            return fail(app.terminal, error)
        case Ok(active):
            assert active.state.expires_at is not None
            app.terminal.write("Sesión reanudada, vence a las"
                               f" {app.tempo.local(active.state.expires_at):%H:%M}.")
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
            app.terminal.write(f"Se agregaron {format_hm(seconds)}."
                               f" Restante: {format_hm_up(active.state.remaining_s)}.")
            return 0


def cmd_stop(app: Deps, args: argparse.Namespace) -> int:
    term, tempo = app.terminal, app.tempo
    match tempo.status():
        case Err(error):
            return fail(term, error)
        case Ok(active):
            pass
    description = term.text("Trabajo realizado", default=active.session.description)
    match tempo.stop(description):
        case Err(error):
            return fail(term, error)
        case Ok(session):
            assert session.end_at is not None
            term.write(f"Sesión cerrada: {when(tempo, session.start_at)} →"
                       f" {when(tempo, session.end_at)}, trabajado"
                       f" {format_hm(tempo.worked_s(session))}.")
            return 0


# --- Sesiones pasadas ---------------------------------------------------------


def session_cells(tempo: Tempo, session: Session) -> list[str]:
    end = "en curso" if session.end_at is None else when(tempo, session.end_at)
    return [str(session.id), session.project_name, when(tempo, session.start_at), end,
            format_hm(tempo.worked_s(session)), session.description]


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
        term.write(f"No hay sesiones en los últimos {days} días.")
        return 0
    headers = ("id", "Proyecto", "Inicio", "Fin", "Trabajado", "Descripción")
    for line in text_table(headers, [session_cells(tempo, s) for s in found], {0, 4},
                           term.width()):
        term.write(line)
    return 0


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
    term.write("Enter conserva el valor actual.")
    project_name = term.text("Proyecto", default=session.project_name)
    description = term.text("Trabajo realizado", default=session.description)
    start_text = term.text("Inicio (AAAA-MM-DD HH:MM)", default=when(tempo, session.start_at))
    end_text = term.text("Fin (AAAA-MM-DD HH:MM)", default=when(tempo, session.end_at))
    match tempo.find_project(project_name):
        case Err(error):
            return fail(term, error)
        case Ok(project):
            pass
    match parse_local(tempo, start_text, DATE_TIME):
        case Err(error):
            return fail(term, error)
        case Ok(start):
            pass
    match parse_local(tempo, end_text, DATE_TIME):
        case Err(error):
            return fail(term, error)
        case Ok(end):
            pass
    match tempo.edit_session(session.id, project_id=project.id, description=description,
                             start_at=start, end_at=end):
        case Err(error):
            return fail(term, error)
        case Ok(edited):
            term.write(f"Sesión #{edited.id} actualizada.")
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
    term.write("  ".join(session_cells(tempo, session)))
    if not term.confirm(f"¿Borrar la sesión #{session.id}?"):
        term.write("Cancelado.")
        return 1
    match tempo.delete_session(session.id):
        case Err(error):
            return fail(term, error)
        case Ok():
            term.write(f"Sesión #{session.id} borrada.")
            return 0


# --- Proyectos ----------------------------------------------------------------


def with_project(app: Deps, name: str, action: Callable[[Project], int]) -> int:
    match app.tempo.find_project(name):
        case Err(error):
            return fail(app.terminal, error)
        case Ok(project):
            return action(project)


def cmd_project_add(app: Deps, args: argparse.Namespace) -> int:
    match create_project(app, args.name):
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
    rows = [[p.name, p.client_name or "—", "archivado" if p.archived else "activo",
             p.description] for p in projects]
    for line in text_table(("Proyecto", "Cliente", "Estado", "Descripción"), rows, set(),
                           app.terminal.width()):
        app.terminal.write(line)
    return 0


def cmd_project_rename(app: Deps, args: argparse.Namespace) -> int:
    def action(project: Project) -> int:
        match app.tempo.rename_project(project.id, args.new_name):
            case Err(error):
                return fail(app.terminal, error)
            case Ok():
                app.terminal.write(f"Proyecto renombrado a {args.new_name.strip()!r}.")
                return 0
    return with_project(app, args.name, action)


def cmd_project_archive(app: Deps, args: argparse.Namespace) -> int:
    def action(project: Project) -> int:
        match app.tempo.archive_project(project.id):
            case Err(error):
                return fail(app.terminal, error)
            case Ok():
                app.terminal.write(f"Proyecto {project.name!r} archivado.")
                return 0
    return with_project(app, args.name, action)


def cmd_project_unarchive(app: Deps, args: argparse.Namespace) -> int:
    def action(project: Project) -> int:
        match unarchive_project(app, project):
            case Err(error):
                return fail(app.terminal, error)
            case Ok():
                app.terminal.write(f"Proyecto {project.name!r} reactivado.")
                return 0
    return with_project(app, args.name, action)


# --- Clientes -----------------------------------------------------------------


def with_client(app: Deps, name: str, action: Callable[[Client], int]) -> int:
    match app.tempo.find_client(name):
        case Err(error):
            return fail(app.terminal, error)
        case Ok(client):
            return action(client)


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


def cmd_client_rename(app: Deps, args: argparse.Namespace) -> int:
    def action(client: Client) -> int:
        match app.tempo.rename_client(client.id, args.new_name):
            case Err(error):
                return fail(app.terminal, error)
            case Ok():
                app.terminal.write(f"Cliente renombrado a {args.new_name.strip()!r}.")
                return 0
    return with_client(app, args.name, action)


def cmd_client_archive(app: Deps, args: argparse.Namespace) -> int:
    def action(client: Client) -> int:
        projects = app.tempo.client_projects(client.id)
        if projects:
            names = ", ".join(p.name for p in projects)
            if not app.terminal.confirm(f"Se van a archivar también sus proyectos: {names}."
                                        " ¿Continuar?"):
                app.terminal.write("Cancelado.")
                return 1
        match app.tempo.archive_client(client.id, cascade=bool(projects)):
            case Err(error):
                return fail(app.terminal, error)
            case Ok():
                app.terminal.write(f"Cliente {client.name!r} archivado.")
                return 0
    return with_client(app, args.name, action)


def cmd_client_unarchive(app: Deps, args: argparse.Namespace) -> int:
    def action(client: Client) -> int:
        match app.tempo.unarchive_client(client.id):
            case Err(error):
                return fail(app.terminal, error)
            case Ok():
                app.terminal.write(f"Cliente {client.name!r} reactivado. Sus proyectos siguen"
                                   " archivados: reactivalos con `tempo project unarchive`.")
                return 0
    return with_client(app, args.name, action)


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
                term.write(f"Reporte guardado: {path}")
                return 0
    match tempo.export_month_filtered(month, fmt, projects, args.output):
        case Err(error):
            return fail(term, error)
        case Ok(path):
            term.write(f"Archivo creado: {path}")
            return 0


# --- Argumentos ---------------------------------------------------------------


def positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("tiene que ser mayor a cero")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tempo", description="Gestor de sesiones de trabajo.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="comando")

    def add(name: str, handler: Callable[[Deps, argparse.Namespace], int], text: str,
            group: argparse._SubParsersAction[argparse.ArgumentParser] = commands,
            ) -> argparse.ArgumentParser:
        sub = group.add_parser(name, help=text, description=text)
        sub.set_defaults(handler=handler)
        return sub

    add("start", cmd_start, "iniciar una sesión (pregunta proyecto, duración y trabajo)")
    add("status", cmd_status, "ver el estado y el tiempo restante de la sesión activa")
    add("pause", cmd_pause, "pausar la sesión activa")
    add("resume", cmd_resume, "reanudar la sesión pausada")
    add("extend", cmd_extend, "agregar tiempo a la sesión activa").add_argument(
        "duration", help="tiempo a agregar, p. ej. 30m o 1h")
    add("stop", cmd_stop, "terminar la sesión activa")

    listing = add("list", cmd_list, "listar sesiones previas")
    listing.add_argument("--project", action="append", metavar="NOMBRE",
                         help="filtrar por proyecto (repetible)")
    listing.add_argument("--days", type=positive_int, help="días hacia atrás (por defecto 7)")
    listing.add_argument("--limit", type=positive_int, help="máximo de sesiones")

    add("edit", cmd_edit, "editar una sesión cerrada").add_argument("id", type=int)
    add("delete", cmd_delete, "borrar una sesión").add_argument("id", type=int)

    project = add("project", lambda app, args: 0, "gestionar proyectos")
    project_cmds = project.add_subparsers(dest="action", required=True, metavar="acción")
    add("add", cmd_project_add, "crear un proyecto", project_cmds).add_argument("name")
    add("list", cmd_project_list, "listar proyectos", project_cmds).add_argument(
        "--archived", action="store_true", help="incluir archivados")
    rename = add("rename", cmd_project_rename, "renombrar un proyecto", project_cmds)
    rename.add_argument("name")
    rename.add_argument("new_name")
    add("archive", cmd_project_archive, "archivar un proyecto", project_cmds).add_argument("name")
    add("unarchive", cmd_project_unarchive, "reactivar un proyecto",
        project_cmds).add_argument("name")

    client = add("client", lambda app, args: 0, "gestionar clientes")
    client_cmds = client.add_subparsers(dest="action", required=True, metavar="acción")
    add("add", cmd_client_add, "crear un cliente", client_cmds).add_argument("name")
    add("list", cmd_client_list, "listar clientes", client_cmds).add_argument(
        "--archived", action="store_true", help="incluir archivados")
    rename = add("rename", cmd_client_rename, "renombrar un cliente", client_cmds)
    rename.add_argument("name")
    rename.add_argument("new_name")
    add("archive", cmd_client_archive, "archivar un cliente y sus proyectos",
        client_cmds).add_argument("name")
    add("unarchive", cmd_client_unarchive, "reactivar un cliente", client_cmds).add_argument("name")

    report = add("report", cmd_report, "reporte de horas por terminal o exportación mensual")
    report.add_argument("--project", action="append", metavar="NOMBRE",
                        help="filtrar por proyecto (repetible)")
    report.add_argument("--days", type=positive_int, help="días hacia atrás (por defecto 30)")
    report.add_argument("--from", dest="from_date", metavar="AAAA-MM-DD")
    report.add_argument("--to", dest="to_date", metavar="AAAA-MM-DD")
    report.add_argument("--month", metavar="MES", help="exportar un mes: 10 o AAAA-MM")
    formats = report.add_mutually_exclusive_group()
    formats.add_argument("--csv", action="store_true", help="exportar en CSV")
    formats.add_argument("--md", action="store_true", help="exportar en Markdown")
    report.add_argument("-o", dest="output", metavar="SALIDA",
                        help="archivo de salida (solo con --project)")

    # Uso interno: el proceso de aviso. No aparece en la ayuda.
    commands.add_parser("notifier")
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "report":
        validate_report_args(parser, args)
    if deps is None:
        config = Config.from_env(os.environ)
        config.home.mkdir(parents=True, exist_ok=True)
        if args.command == "notifier":
            return serve_notifier(config)
        deps = build_deps(config)
    app = deps
    app.tempo.ensure_notifier()
    handler: Callable[[Deps, argparse.Namespace], int] = args.handler
    return handler(app, args)


def entry() -> None:
    """Punto de entrada del ejecutable (`python -m tempo` o el zipapp instalado)."""
    try:
        code = main()
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C o Ctrl+D en una pregunta: no es un error del programa.
        print("\nCancelado.", file=sys.stderr)
        code = 130
    sys.exit(code)
