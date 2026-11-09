import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tempo.models import Project
from tempo.report import (
    CsvRenderer,
    MarkdownRenderer,
    TerminalRenderer,
    month_bounds,
    parse_month,
    render_to_text,
)
from tempo.result import (
    EmptyMonth,
    Err,
    FileExists,
    InvalidMonth,
    MonthNotClosed,
    Ok,
    OpenSessionInMonth,
)
from tempo.store_files import DiskReportStore
from tests.helpers import HOUR, TZ, Rig, at, make_rig, ok


def closed(rig: Rig, project: Project, start: datetime, end: datetime, text: str = "") -> None:
    rig.clock.set(start)
    ok(rig.tempo.start(project.id, 24 * HOUR, text))
    rig.clock.set(end)
    ok(rig.tempo.stop(None))


class MonthTest(unittest.TestCase):
    def test_parse(self) -> None:
        today = at(11, 9, 10)
        self.assertEqual(parse_month("10", today), Ok("2026-10"))
        self.assertEqual(parse_month("2025-03", today), Ok("2025-03"))
        for text in ["13", "0", "2025-13", "oct", "2025/10"]:
            with self.subTest(text=text):
                self.assertEqual(parse_month(text, today), Err(InvalidMonth(text)))

    def test_bounds_cross_year(self) -> None:
        self.assertEqual(month_bounds("2026-12", TZ), (at(12, 1, 0), at(1, 1, 0, year=2027)))


class BuildAndRenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = make_rig()
        acme = ok(self.rig.tempo.add_client("Acme"))
        self.web = ok(self.rig.tempo.add_project("web", acme.id, ""))
        self.app = ok(self.rig.tempo.add_project("app", None, ""))
        # Cruza el fin de mes: pertenece completa a septiembre.
        closed(self.rig, self.web, at(9, 30, 22), at(10, 1, 1), "fix | urgente")
        closed(self.rig, self.web, at(10, 2, 9), at(10, 2, 11), "carrito")
        closed(self.rig, self.app, at(10, 3, 14), at(10, 3, 15, 30), "login")
        self.rig.clock.set(at(11, 9, 10))

    def test_session_belongs_to_start_month(self) -> None:
        sept = ok(self.rig.tempo.month_report("2026-09", None))
        octo = ok(self.rig.tempo.month_report("2026-10", None))
        self.assertEqual([r.description for r in sept.rows], ["fix | urgente"])
        self.assertEqual(sept.worked_s, 3 * HOUR)
        self.assertEqual([r.description for r in octo.rows], ["carrito", "login"])

    def test_summary_only_with_several_projects(self) -> None:
        both = self.rig.tempo.report(at(10, 1, 0), at(11, 1, 0))
        self.assertEqual([(p.project, p.worked_s) for p in both.summary],
                         [("app", int(1.5 * HOUR)), ("web", 2 * HOUR)])
        only_web = self.rig.tempo.report(at(10, 1, 0), at(11, 1, 0), [self.web])
        self.assertEqual(only_web.summary, ())

    def test_terminal_groups_by_month(self) -> None:
        text = render_to_text(TerminalRenderer(), self.rig.tempo.report(at(9, 1, 0), at(11, 1, 0)))
        self.assertIn("── Septiembre 2026 ──", text)
        self.assertIn("── Octubre 2026 ──", text)
        self.assertIn("Total del mes: 3:30", text)
        self.assertIn("Total: 6:30", text)
        self.assertIn("2026-09-30 22:00  2026-10-01 01:00", text)

    def test_terminal_truncates_description_to_width(self) -> None:
        # Las otras columnas ocupan 75 caracteres: a la descripcion le quedan 10.
        report = self.rig.tempo.report(at(9, 1, 0), at(10, 1, 0))
        lines = render_to_text(TerminalRenderer(width=85), report).splitlines()
        self.assertTrue(all(len(line) <= 85 for line in lines))
        self.assertIn("fix | urg…", next(line for line in lines if "2026-09-30" in line))

    def test_csv(self) -> None:
        report = self.rig.tempo.report(at(9, 1, 0), at(10, 1, 0))
        text = render_to_text(CsvRenderer(), report)
        self.assertEqual(text.split("\r\n"), [
            "﻿id,proyecto,cliente,inicio,fin,horas_trabajadas,horas_pausa,descripcion",
            "1,web,Acme,2026-09-30 22:00,2026-10-01 01:00,3.00,0.00,fix | urgente",
            "",
        ])

    def test_markdown(self) -> None:
        text = render_to_text(MarkdownRenderer(), ok(self.rig.tempo.month_report("2026-10", None)))
        self.assertIn("# Reporte de horas — Octubre 2026", text)
        self.assertIn("- **Total del mes:** 3:30", text)
        self.assertIn("## Resumen por proyecto", text)
        self.assertIn("| app | — | 1 | 1:30 |", text)
        sept = render_to_text(MarkdownRenderer(), ok(self.rig.tempo.month_report("2026-09", None)))
        self.assertNotIn("## Resumen por proyecto", sept)
        self.assertIn("fix \\| urgente", sept)


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = make_rig()
        self.web = ok(self.rig.tempo.add_project("web", None, ""))
        closed(self.rig, self.web, at(10, 2, 9), at(10, 2, 11))
        self.rig.clock.set(at(11, 9, 10))

    def test_only_closed_months(self) -> None:
        self.assertEqual(self.rig.tempo.export_month("2026-11", "csv"),
                         Err(MonthNotClosed("2026-11")))
        self.assertEqual(self.rig.tempo.export_month("2026-12", "csv"),
                         Err(MonthNotClosed("2026-12")))

    def test_open_session_blocks_export(self) -> None:
        rig = make_rig(at(10, 31, 23))
        project = ok(rig.tempo.add_project("x", None, ""))
        active = ok(rig.tempo.start(project.id, 24 * HOUR, ""))
        rig.clock.set(at(11, 2, 10))
        self.assertEqual(rig.tempo.export_month("2026-10", "md"),
                         Err(OpenSessionInMonth("2026-10", active.session.id)))

    def test_empty_month(self) -> None:
        self.assertEqual(self.rig.tempo.export_month("2026-09", "csv"), Err(EmptyMonth("2026-09")))
        self.assertFalse(self.rig.tempo.is_month_locked("2026-09"))

    def test_saved_export_locks_and_overwrites(self) -> None:
        path = ok(self.rig.tempo.export_month("2026-10", "csv"))
        self.assertEqual(path, Path("/reports/tempo-2026-10.csv"))
        self.assertTrue(self.rig.tempo.is_month_locked("2026-10"))
        self.assertEqual(ok(self.rig.tempo.export_month("2026-10", "csv")), path)
        ok(self.rig.tempo.export_month("2026-10", "md"))
        self.assertEqual(set(self.rig.saved.files),
                         {path, Path("/reports/tempo-2026-10.md")})

    def test_filtered_export_does_not_lock_or_overwrite(self) -> None:
        path = ok(self.rig.tempo.export_month_filtered("2026-10", "csv", [self.web], None))
        self.assertEqual(path, Path("/cwd/tempo-2026-10.csv"))
        self.assertFalse(self.rig.tempo.is_month_locked("2026-10"))
        self.assertEqual(self.rig.tempo.export_month_filtered("2026-10", "csv", [self.web], None),
                         Err(FileExists(path)))
        other = ok(self.rig.tempo.export_month_filtered("2026-10", "csv", [self.web], "otro.csv"))
        self.assertEqual(other, Path("/cwd/otro.csv"))


class DiskReportStoreTest(unittest.TestCase):
    def test_overwrite_rules_and_absolute_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DiskReportStore(Path(tmp) / "reports")
            path = ok(store.save("a.csv", "1", overwrite=False))
            self.assertEqual(path.read_text(encoding="utf-8"), "1")
            self.assertEqual(store.save("a.csv", "2", overwrite=False), Err(FileExists(path)))
            self.assertEqual(path.read_text(encoding="utf-8"), "1")
            ok(store.save("a.csv", "3", overwrite=True))
            self.assertEqual(path.read_text(encoding="utf-8"), "3")
            elsewhere = Path(tmp) / "otra" / "b.md"
            self.assertEqual(ok(store.save(str(elsewhere), "x", overwrite=False)), elsewhere)


if __name__ == "__main__":
    unittest.main()
