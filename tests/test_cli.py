import contextlib
import io
import unittest
from datetime import timedelta
from pathlib import Path

from tempo.cli import Deps, main
from tempo.terminal import DummyTerminal
from tests.helpers import HOUR, Rig, at, make_rig, ok


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig: Rig = make_rig(at(11, 9, 10))

    def run_cli(self, *argv: str, answers: tuple[str, ...] = ()) -> tuple[int, DummyTerminal]:
        term = DummyTerminal(answers)
        code = main(list(argv), Deps(term, self.rig.tempo, list_days=7, report_days=30))
        self.assertEqual(term.unused_answers, [], "sobraron respuestas guionadas")
        return code, term

    def test_start_creates_project_and_client(self) -> None:
        code, term = self.run_cli(
            "start", answers=("web", "Acme", "Tienda online", "1h30x", "1h30m", "Maquetar"))
        self.assertEqual(code, 0)
        self.assertIn("Clientes:\n  1) (sin cliente)\nCliente>", term.output)
        self.assertIn("Sesión iniciada en web: 1:30, vence a las 11:30.", term.output)
        project = ok(self.rig.tempo.find_project("web"))
        self.assertEqual((project.client_name, project.description), ("Acme", "Tienda online"))

    def test_start_picks_existing_project_by_number(self) -> None:
        ok(self.rig.tempo.add_project("app", None, ""))
        ok(self.rig.tempo.add_project("web", None, ""))
        code, term = self.run_cli("start", answers=("2", "1h", ""))
        self.assertEqual(code, 0)
        self.assertEqual(term.lines[:3], ["Proyectos:", "  1) app", "  2) web"])
        self.assertEqual(ok(self.rig.tempo.status()).session.project_name, "web")

    def test_start_offers_to_unarchive(self) -> None:
        acme = ok(self.rig.tempo.add_client("Acme"))
        project = ok(self.rig.tempo.add_project("web", acme.id, ""))
        ok(self.rig.tempo.archive_client(acme.id, cascade=True))
        code, _ = self.run_cli("start", answers=("web", "s", "s", "1h", ""))
        self.assertEqual(code, 0)
        self.assertFalse(ok(self.rig.tempo.find_project("web")).archived)
        self.assertEqual(ok(self.rig.tempo.status()).session.project_id, project.id)

    def test_stop_enter_keeps_description(self) -> None:
        project = ok(self.rig.tempo.add_project("web", None, ""))
        ok(self.rig.tempo.start(project.id, HOUR, "objetivo"))
        self.rig.clock.advance(timedelta(minutes=30))
        code, term = self.run_cli("stop", answers=("",))
        self.assertEqual(code, 0)
        self.assertIn("trabajado 0:30", term.output)
        self.assertEqual(self.rig.tempo.list_sessions(at(11, 1, 0))[0].description, "objetivo")

    def test_every_command_ensures_notifier(self) -> None:
        # Tambien las lecturas: un proceso de aviso caido se relanza con `status`.
        before = self.rig.supervisor.calls
        self.run_cli("status")
        self.run_cli("list")
        self.assertEqual(self.rig.supervisor.calls - before, 2)

    def test_errors_are_messages_with_exit_1(self) -> None:
        code, term = self.run_cli("pause")
        self.assertEqual((code, term.errors), (1, ["No hay ninguna sesión activa."]))

    def test_client_archive_asks_before_cascade(self) -> None:
        acme = ok(self.rig.tempo.add_client("Acme"))
        ok(self.rig.tempo.add_project("web", acme.id, ""))
        code, _ = self.run_cli("client", "archive", "Acme", answers=("n",))
        self.assertEqual(code, 1)
        self.assertEqual(len(self.rig.tempo.list_projects()), 1)
        code, _ = self.run_cli("client", "archive", "Acme", answers=("s",))
        self.assertEqual(code, 0)
        self.assertEqual(self.rig.tempo.list_projects(), [])

    def test_project_edit_from_menu_keeps_client(self) -> None:
        acme = ok(self.rig.tempo.add_client("Acme"))
        ok(self.rig.tempo.add_project("app", None, ""))
        ok(self.rig.tempo.add_project("web", acme.id, "vieja"))
        code, term = self.run_cli("project", "edit", answers=("2", "", "Tienda online"))
        self.assertEqual(code, 0)
        self.assertEqual(term.lines[:3], ["Proyectos:", "  1) app", "  2) web"])
        self.assertIn("Cliente: Acme (no se puede cambiar)", term.output)
        project = ok(self.rig.tempo.find_project("web"))
        self.assertEqual((project.client_name, project.description), ("Acme", "Tienda online"))

    def test_client_edit_blocked_by_exported_month(self) -> None:
        acme = ok(self.rig.tempo.add_client("Acme"))
        project = ok(self.rig.tempo.add_project("web", acme.id, ""))
        ok(self.rig.tempo.start(project.id, HOUR, ""))
        ok(self.rig.tempo.stop(None))
        self.rig.store.add_export("2026-11", at(11, 9, 12))
        code, term = self.run_cli("client", "edit", "Acme")  # falla sin preguntar nada
        self.assertEqual(code, 1)
        self.assertEqual(term.errors,
                         ["El mes 2026-11 tiene un reporte guardado y no se puede modificar."])

    def test_edit_with_defaults_and_locked_month(self) -> None:
        project = ok(self.rig.tempo.add_project("web", None, ""))
        ok(self.rig.tempo.start(project.id, HOUR, "x"))
        self.rig.clock.advance(timedelta(minutes=40))
        session = ok(self.rig.tempo.stop(None))
        code, _ = self.run_cli("edit", str(session.id),
                               answers=("", "corregido", "", "2026-11-09 10:50"))
        self.assertEqual(code, 0)
        edited = ok(self.rig.tempo.get_session(session.id))
        self.assertEqual((edited.description, edited.end_at), ("corregido", at(11, 9, 10, 50)))

        self.rig.store.add_export("2026-11", at(11, 9, 12))
        code, term = self.run_cli("edit", str(session.id))  # falla antes de preguntar
        self.assertEqual(code, 1)
        self.assertIn("tiene un reporte guardado", term.errors[0])

    def test_report_export_prints_path(self) -> None:
        project = ok(self.rig.tempo.add_project("web", None, ""))
        self.rig.clock.set(at(10, 5, 9))
        ok(self.rig.tempo.start(project.id, HOUR, ""))
        self.rig.clock.set(at(10, 5, 11))
        ok(self.rig.tempo.stop(None))
        self.rig.clock.set(at(11, 9, 10))
        code, term = self.run_cli("report", "--month", "10", "--md")
        self.assertEqual((code, term.lines), (0, ["Reporte guardado: /reports/tempo-2026-10.md"]))
        code, term = self.run_cli("report", "--month", "10", "--csv", "--project", "web",
                                  "-o", "web.csv")
        self.assertEqual((code, term.lines), (0, ["Archivo creado: /cwd/web.csv"]))
        self.assertIn(Path("/cwd/web.csv"), self.rig.loose.files)

    def test_invalid_report_combinations(self) -> None:
        for argv in (["report", "--month", "10"], ["report", "--csv"],
                     ["report", "--month", "10", "--csv", "-o", "x.csv"],
                     ["report", "--days", "5", "--from", "2026-10-01"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_:
                    self.run_cli(*argv)
                self.assertEqual(exit_.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
