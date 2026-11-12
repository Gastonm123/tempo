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
            "start",
            answers=("web", "2", "Acme", "Tienda online", "1h30x", "1h30m", " ", "Maquetar"))
        self.assertEqual(code, 0)
        self.assertIn("Elige un cliente:\n  1) Omitir\n  2) Crear cliente\n", term.output)
        self.assertIn("  El trabajo realizado no puede estar vacio.", term.lines)
        self.assertIn("Sesion iniciada.\n"
                      "  Proyecto: web (Acme)\n"
                      "  Duracion: 1h30m\n"
                      "  Vence:    11:30", term.output)
        project = ok(self.rig.tempo.find_project("web"))
        self.assertEqual((project.client_name, project.description), ("Acme", "Tienda online"))

    def test_start_picks_existing_project_by_number(self) -> None:
        ok(self.rig.tempo.add_project("app", None, ""))
        ok(self.rig.tempo.add_project("web", None, ""))
        code, term = self.run_cli("start", answers=("2", "1h", "trabajo"))
        self.assertEqual(code, 0)
        self.assertEqual(term.lines[:4],
                         ["Elige un proyecto:", "  1) app", "  2) web", "  3) Crear proyecto"])
        self.assertEqual(ok(self.rig.tempo.status()).session.project_name, "web")

    def test_start_validates_each_answer(self) -> None:
        ok(self.rig.tempo.add_project("app", None, ""))
        code, term = self.run_cli(
            "start", answers=("nada", "2", "app", "web", "1", "", "1h", "trabajo"))
        self.assertEqual(code, 0)
        self.assertIn("  Opcion invalida.", term.lines)
        self.assertIn("  Ya existe un proyecto llamado 'app'.", term.lines)
        self.assertEqual(ok(self.rig.tempo.status()).session.project_name, "web")

    def test_start_offers_to_unarchive(self) -> None:
        acme = ok(self.rig.tempo.add_client("Acme"))
        project = ok(self.rig.tempo.add_project("web", acme.id, ""))
        ok(self.rig.tempo.archive_client(acme.id, cascade=True))
        code, _ = self.run_cli("start", answers=("web", "s", "s", "1h", "trabajo"))
        self.assertEqual(code, 0)
        self.assertFalse(ok(self.rig.tempo.find_project("web")).archived)
        self.assertEqual(ok(self.rig.tempo.status()).session.project_id, project.id)

    def test_stop_keeps_description(self) -> None:
        project = ok(self.rig.tempo.add_project("web", None, ""))
        ok(self.rig.tempo.start(project.id, HOUR, "objetivo"))
        self.rig.clock.advance(timedelta(minutes=30))
        code, term = self.run_cli("stop")
        self.assertEqual(code, 0)
        self.assertEqual(term.lines,
                         ["Sesion cerrada: 2026-11-09 10:00 -> 2026-11-09 10:30, trabajado 30m."])
        self.assertEqual(self.rig.tempo.list_sessions(at(11, 1, 0))[0].description, "objetivo")

    def test_every_command_ensures_notifier(self) -> None:
        # Tambien las lecturas: un proceso de aviso caido se relanza con `status`.
        before = self.rig.supervisor.calls
        self.run_cli("status")
        self.run_cli("list")
        self.assertEqual(self.rig.supervisor.calls - before, 2)

    def test_errors_are_messages_with_exit_1(self) -> None:
        code, term = self.run_cli("pause")
        self.assertEqual((code, term.errors), (1, ["No hay ninguna sesion activa."]))

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
        self.assertEqual(term.lines[:3], ["Elige un proyecto:", "  1) app", "  2) web"])
        self.assertIn("Cliente: Acme", term.lines)
        project = ok(self.rig.tempo.find_project("web"))
        self.assertEqual((project.client_name, project.description), ("Acme", "Tienda online"))

    def test_project_archive_and_unarchive_from_menu(self) -> None:
        ok(self.rig.tempo.add_project("web", None, ""))
        code, _ = self.run_cli("project", "archive", answers=("1",))
        self.assertEqual(code, 0)
        code, term = self.run_cli("project", "archive", "web")
        self.assertEqual((code, term.errors), (1, ["El proyecto 'web' esta archivado."]))
        code, term = self.run_cli("project", "unarchive", answers=("1",))
        self.assertEqual(code, 0)
        self.assertIn("Proyecto 'web' desarchivado.", term.lines)

    def test_client_edit_blocked_by_exported_month(self) -> None:
        acme = ok(self.rig.tempo.add_client("Acme"))
        project = ok(self.rig.tempo.add_project("web", acme.id, ""))
        ok(self.rig.tempo.start(project.id, HOUR, "trabajo"))
        ok(self.rig.tempo.stop(None))
        self.rig.store.add_export("2026-11", at(11, 9, 12))
        code, term = self.run_cli("client", "edit", "Acme")  # falla sin preguntar nada
        self.assertEqual(code, 1)
        self.assertEqual(term.errors,
                         ["El mes 2026-11 ya se exporto y esta bloqueado:"
                          " sus sesiones no se pueden modificar."])

    def test_edit_with_defaults_and_locked_month(self) -> None:
        project = ok(self.rig.tempo.add_project("web", None, ""))
        ok(self.rig.tempo.start(project.id, HOUR, "x"))
        self.rig.clock.advance(timedelta(minutes=40))
        session = ok(self.rig.tempo.stop(None))
        code, term = self.run_cli("edit", str(session.id),
                                  answers=("1", "corregido", "", "2026-11-09 10:50"))
        self.assertEqual(code, 0)
        self.assertEqual(term.lines[0].split(), ["Id", "Proyecto", "Inicio", "Fin", "Trabajado",
                                                 "Trabajo", "realizado"])
        self.assertIn("  1) Mantener web", term.lines)
        edited = ok(self.rig.tempo.get_session(session.id))
        self.assertEqual((edited.description, edited.end_at), ("corregido", at(11, 9, 10, 50)))

        self.rig.store.add_export("2026-11", at(11, 9, 12))
        code, term = self.run_cli("edit", str(session.id))  # falla antes de preguntar
        self.assertEqual(code, 1)
        self.assertIn("ya se exporto y esta bloqueado", term.errors[0])

    def test_edit_validates_each_answer(self) -> None:
        project = ok(self.rig.tempo.add_project("web", None, ""))
        ok(self.rig.tempo.start(project.id, HOUR, "x"))
        session = ok(self.rig.tempo.stop(None))
        code, term = self.run_cli("edit", str(session.id), answers=(
            "1", "", "ayer", "2026-11-09 09:00", "2026-11-09 08:00", "2026-11-09 09:30"))
        self.assertEqual(code, 0)
        self.assertIn("  Fecha invalida. Usa AAAA-MM-DD HH:MM.", term.lines)
        self.assertIn("  El fin tiene que ser posterior al inicio.", term.lines)
        edited = ok(self.rig.tempo.get_session(session.id))
        self.assertEqual((edited.start_at, edited.end_at), (at(11, 9, 9), at(11, 9, 9, 30)))

    def test_report_export_prints_path(self) -> None:
        project = ok(self.rig.tempo.add_project("web", None, ""))
        self.rig.clock.set(at(10, 5, 9))
        ok(self.rig.tempo.start(project.id, HOUR, "trabajo"))
        self.rig.clock.set(at(10, 5, 11))
        ok(self.rig.tempo.stop(None))
        self.rig.clock.set(at(11, 9, 10))
        code, term = self.run_cli("report", "--month", "10", "--md")
        self.assertEqual((code, term.lines), (0, ["Reporte guardado en /reports/tempo-2026-10.md."
                                                 " El mes 2026-10 quedo bloqueado."]))
        code, term = self.run_cli("report", "--month", "10", "--csv", "--project", "web",
                                  "-o", "web.csv")
        self.assertEqual((code, term.lines), (0, ["Reporte guardado en /cwd/web.csv."]))
        self.assertIn(Path("/cwd/web.csv"), self.rig.loose.files)

    def test_invalid_report_combinations(self) -> None:
        for argv in (["report", "--month", "10"], ["report", "--csv"],
                     ["report", "--month", "10", "--csv", "-o", "x.csv"],
                     ["report", "--days", "5", "--from", "2026-10-01"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_:
                    self.run_cli(*argv)
                self.assertEqual(exit_.exception.code, 2)

    def test_argument_errors_in_spanish_with_own_usage(self) -> None:
        for argv, expected in ((["foo"], "tempo: error: argumento COMANDO: opcion invalida: 'foo'"),
                               (["report", "--csv"], "tempo report: error: --csv y --md"),
                               (["edit"], "tempo edit: error: faltan los argumentos: ID")):
            stderr = io.StringIO()
            with self.subTest(argv=argv), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    self.run_cli(*argv)
                self.assertIn(expected, stderr.getvalue())
                self.assertNotIn("notifier", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
