"""Reportes: el dato `Report` (puro) y los `Renderer` que le dan formato.

`build_report` arma el reporte a partir de sesiones cerradas, sin tocar nada
externo. Cada sesion pertenece al mes (hora local) en que empezo y va en una
sola fila, aunque cruce la medianoche o el fin de mes.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Protocol, TextIO

from tempo import sessions as calc
from tempo.durations import format_duration, format_hm, format_hours
from tempo.models import Session
from tempo.result import Err, InvalidMonth, Ok, Result

MONTH_NAMES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)



# --- Meses ----------------------------------------------------------------


def parse_month(text: str, today: datetime) -> Result[str, InvalidMonth]:
    """'10' -> mes 10 del ano de `today`; '2025-10' -> ese mes. Devuelve 'AAAA-MM'."""
    value = text.strip()
    year, month = today.year, None
    if value.isdigit() and len(value) <= 2:
        month = int(value)
    elif len(value) == 7 and value[4] == "-" and value[:4].isdigit() and value[5:].isdigit():
        year, month = int(value[:4]), int(value[5:])
    if month is None or not 1 <= month <= 12:
        return Err(InvalidMonth(text))
    return Ok(f"{year:04d}-{month:02d}")


def month_bounds(month: str, tz: tzinfo | None) -> tuple[datetime, datetime]:
    """Inicio del mes y del mes siguiente, en hora local."""
    year, number = int(month[:4]), int(month[5:])
    start = datetime(year, number, 1, tzinfo=tz)
    following = datetime(year + number // 12, number % 12 + 1, 1, tzinfo=tz)
    return start, following


def month_title(month: str) -> str:
    return f"{MONTH_NAMES[int(month[5:]) - 1]} {month[:4]}"


# --- El dato Report -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportRow:
    session_id: int
    project: str
    client: str | None
    start: datetime  # hora local
    end: datetime  # hora local
    worked_s: int
    paused_s: int
    description: str

    @property
    def duration_s(self) -> int:
        return int((self.end - self.start).total_seconds())


@dataclass(frozen=True, slots=True)
class MonthGroup:
    month: str  # 'AAAA-MM'
    rows: tuple[ReportRow, ...]

    @property
    def worked_s(self) -> int:
        return sum(row.worked_s for row in self.rows)


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    project: str
    client: str | None
    sessions: int
    worked_s: int


@dataclass(frozen=True, slots=True)
class Report:
    months: tuple[MonthGroup, ...]
    projects: tuple[str, ...] | None  # None = todos los proyectos
    summary: tuple[ProjectSummary, ...]  # vacio si hay un solo proyecto
    generated_at: datetime

    @property
    def rows(self) -> tuple[ReportRow, ...]:
        return tuple(row for group in self.months for row in group.rows)

    @property
    def worked_s(self) -> int:
        return sum(group.worked_s for group in self.months)

    @property
    def is_empty(self) -> bool:
        return not self.months


def build_report(
    sessions: Sequence[Session],
    *,
    tz: tzinfo | None,
    generated_at: datetime,
    projects: Sequence[str] | None,
) -> Report:
    """Arma el reporte con las sesiones cerradas; las abiertas se ignoran."""
    rows: list[ReportRow] = []
    for session in sorted(sessions, key=lambda s: s.start_at):
        if session.end_at is None:
            continue
        rows.append(
            ReportRow(
                session_id=session.id,
                project=session.project_name,
                client=session.client_name,
                start=session.start_at.astimezone(tz),
                end=session.end_at.astimezone(tz),
                worked_s=calc.worked_seconds(session.start_at, session.pauses, session.end_at),
                paused_s=calc.paused_seconds(session.pauses, session.end_at),
                description=session.description,
            )
        )
    groups: dict[str, list[ReportRow]] = {}
    for row in rows:
        groups.setdefault(row.start.strftime("%Y-%m"), []).append(row)
    months = tuple(MonthGroup(month, tuple(items)) for month, items in groups.items())

    totals: dict[str, ProjectSummary] = {}
    for row in rows:
        previous = totals.get(row.project)
        totals[row.project] = ProjectSummary(
            project=row.project,
            client=row.client,
            sessions=(previous.sessions if previous else 0) + 1,
            worked_s=(previous.worked_s if previous else 0) + row.worked_s,
        )
    summary = tuple(sorted(totals.values(), key=lambda p: p.project)) if len(totals) > 1 else ()
    return Report(
        months=months,
        projects=None if projects is None else tuple(projects),
        summary=summary,
        generated_at=generated_at,
    )


# --- Renderers ------------------------------------------------------------


class Renderer(Protocol):
    def render(self, report: Report, out: TextIO) -> None: ...


def _when(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def _projects_label(report: Report) -> str:
    if report.projects is None:
        return "todos"
    if len(report.projects) == 1 and report.rows:
        client = report.rows[0].client
        return f"{report.projects[0]} ({client})" if client else report.projects[0]
    return ", ".join(report.projects)


_GAP = "  "
_MIN_LAST_COLUMN = 10


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def text_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    right: set[int],
    max_width: int | None = None,
) -> list[str]:
    """Tabla de texto. Con `max_width`, la ultima columna se recorta con '...'
    para que la linea no supere ese ancho (sin bajar de un minimo legible)."""
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    if max_width is not None:
        others = sum(widths[:-1]) + len(_GAP) * (len(widths) - 1)
        widths[-1] = max(min(widths[-1], max_width - others), _MIN_LAST_COLUMN)

    def line(cells: Sequence[str]) -> str:
        parts = [_truncate(c, w).rjust(w) if i in right else _truncate(c, w).ljust(w)
                 for i, (c, w) in enumerate(zip(cells, widths, strict=True))]
        return _GAP.join(parts).rstrip()

    return [line(headers), _GAP.join("-" * w for w in widths), *(line(r) for r in rows)]


# La terminal va en ASCII; el Markdown exportado, con la ortografia normal.
_TERMINAL_HEADERS = ("Inicio", "Fin", "Proyecto", "Trabajado", "En pausa", "Duracion",
                     "Trabajo realizado")
_MARKDOWN_HEADERS = ("Inicio", "Fin", "Proyecto", "Trabajado", "En pausa", "Duración",
                     "Trabajo realizado")
_SUMMARY_HEADERS = ("Proyecto", "Cliente", "Sesiones", "Trabajado")
_TIME_COLUMNS = {3, 4, 5}


def _detail_cells(row: ReportRow, duration: Callable[[int], str]) -> list[str]:
    return [
        _when(row.start),
        _when(row.end),
        row.project,
        duration(row.worked_s),
        duration(row.paused_s),
        duration(row.duration_s),
        row.description,
    ]


class TerminalRenderer(Renderer):
    """`width`: ancho de la terminal; la descripcion se recorta para no pasarlo.
    None = sin recortar. Cada mes calcula sus propios anchos de columna."""

    def __init__(self, width: int | None = None) -> None:
        self._width = width

    def render(self, report: Report, out: TextIO) -> None:
        if report.is_empty:
            out.write("No hay sesiones en el periodo.\n")
            return
        for group in report.months:
            out.write(f"\n── {month_title(group.month)} ──\n")
            rows = [_detail_cells(r, format_duration) for r in group.rows]
            for text in text_table(_TERMINAL_HEADERS, rows, _TIME_COLUMNS, self._width):
                out.write(text + "\n")
            out.write(f"Total del mes: {format_duration(group.worked_s)}\n")
        if report.summary:
            out.write("\n── Resumen por proyecto ──\n")
            cells = [[p.project, p.client or "", str(p.sessions), format_duration(p.worked_s)]
                     for p in report.summary]
            for text in text_table(_SUMMARY_HEADERS, cells, {2, 3}):
                out.write(text + "\n")
        out.write(f"\nTotal: {format_duration(report.worked_s)}\n")


class CsvRenderer(Renderer):
    """RFC 4180, UTF-8 con BOM, una fila por sesion, sin totales."""

    HEADERS = ("id", "proyecto", "cliente", "inicio", "fin", "horas_trabajadas",
               "horas_pausa", "descripcion")

    def render(self, report: Report, out: TextIO) -> None:
        out.write("\ufeff")
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow(self.HEADERS)
        for row in report.rows:
            writer.writerow([
                row.session_id,
                row.project,
                row.client or "",
                _when(row.start),
                _when(row.end),
                format_hours(row.worked_s),
                format_hours(row.paused_s),
                row.description,
            ])


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]], right: set[int]) -> list[str]:
    align = ["---:" if i in right else "---" for i in range(len(headers))]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(align) + "|"]
    lines += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows]
    return lines


class MarkdownRenderer(Renderer):
    """Reporte de un mes: encabezado, resumen por proyecto (si hay varios) y detalle."""

    def render(self, report: Report, out: TextIO) -> None:
        lines = [
            f"# Reporte de horas — {month_title(report.months[0].month)}",
            "",
            f"- **Proyectos:** {_projects_label(report)}",
            f"- **Generado:** {_when(report.generated_at)}",
            f"- **Total del mes:** {format_hm(report.worked_s)}",
        ]
        if report.summary:
            cells = [[p.project, p.client or "", str(p.sessions), format_hm(p.worked_s)]
                     for p in report.summary]
            cells.append(["**Total**", "", f"**{sum(p.sessions for p in report.summary)}**",
                          f"**{format_hm(report.worked_s)}**"])
            lines += ["", "## Resumen por proyecto", "",
                      *_md_table(_SUMMARY_HEADERS, cells, {2, 3})]
        lines += ["", "## Detalle de sesiones", "",
                  *_md_table(_MARKDOWN_HEADERS, [_detail_cells(r, format_hm) for r in report.rows],
                             _TIME_COLUMNS)]
        out.write("\n".join(lines) + "\n")


def render_to_text(renderer: Renderer, report: Report) -> str:
    buffer = io.StringIO()
    renderer.render(report, buffer)
    return buffer.getvalue()


def default_since(now: datetime, days: int) -> datetime:
    """Inicio del periodo por defecto: `days` dias hacia atras desde ahora."""
    return now - timedelta(days=days)
