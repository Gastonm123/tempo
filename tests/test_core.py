import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path

from tempo.clock import FakeClock
from tempo.core import Tempo
from tempo.db import SqliteStore
from tempo.store_files import MemoryReportStore
from tempo.supervisor import FakeSupervisor

from tempo.result import (
    AlreadyPaused,
    ClientArchived,
    ClientHasActiveProjects,
    Err,
    InvalidInterval,
    InvalidName,
    MonthLocked,
    NotPaused,
    ProjectArchived,
    SessionAlreadyActive,
    SessionExpired,
    SessionIsActive,
)
from tests.helpers import HOUR, Rig, at, make_rig, ok


class SessionFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig: Rig = make_rig(at(11, 9, 10))
        self.project = ok(self.rig.tempo.add_project("web", None, ""))

    def advance(self, minutes: int) -> None:
        self.rig.clock.advance(timedelta(minutes=minutes))

    def test_start_and_status(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, "  maquetar  "))
        self.advance(20)
        status = ok(self.rig.tempo.status())
        self.assertEqual(status.state.kind, "running")
        self.assertEqual(status.state.remaining_s, 40 * 60)
        self.assertEqual(status.session.description, "maquetar")

    def test_second_start_rejected(self) -> None:
        first = ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        again = self.rig.tempo.start(self.project.id, HOUR, "")
        self.assertEqual(again, Err(SessionAlreadyActive(first.session.id)))

    def test_pause_freezes_remaining(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.advance(20)
        ok(self.rig.tempo.pause())
        self.advance(90)
        status = ok(self.rig.tempo.status())
        self.assertEqual(status.state.kind, "paused")
        self.assertEqual(status.state.remaining_s, 40 * 60)
        self.assertEqual(self.rig.tempo.pause(), Err(AlreadyPaused(at(11, 9, 10, 20))))
        ok(self.rig.tempo.resume())
        self.assertEqual(ok(self.rig.tempo.status()).state.expires_at, at(11, 9, 12, 30))

    def test_resume_when_not_paused(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.assertEqual(self.rig.tempo.resume(), Err(NotPaused()))

    def test_cannot_pause_expired(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.advance(70)
        self.assertEqual(self.rig.tempo.pause(), Err(SessionExpired(at(11, 9, 11))))

    def test_stop_after_expiry_ends_at_expiry(self) -> None:
        # Vence 11:00; stop a las 11:40 -> fin 11:00 y 1h trabajada.
        ok(self.rig.tempo.start(self.project.id, HOUR, "objetivo"))
        self.advance(100)
        session = ok(self.rig.tempo.stop("hecho"))
        self.assertEqual(session.end_at, at(11, 9, 11))
        self.assertEqual(session.description, "hecho")
        self.assertEqual(self.rig.tempo.worked_s(session), HOUR)

    def test_stop_while_paused_closes_pause(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.advance(20)
        ok(self.rig.tempo.pause())
        self.advance(15)
        session = ok(self.rig.tempo.stop(None))
        self.assertEqual(session.end_at, at(11, 9, 10, 35))
        self.assertEqual(self.rig.tempo.worked_s(session), 20 * 60)
        self.assertEqual(self.rig.tempo.paused_s(session), 15 * 60)

    def test_extend_after_expiry_gap_does_not_count(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.advance(100)  # 11:40, vencida a las 11:00
        status = ok(self.rig.tempo.extend(30 * 60))
        self.assertEqual(status.state.kind, "running")
        self.assertEqual(status.state.expires_at, at(11, 9, 12, 10))
        self.advance(30)
        session = ok(self.rig.tempo.stop(None))
        self.assertEqual(session.end_at, at(11, 9, 12, 10))
        self.assertEqual(self.rig.tempo.worked_s(session), HOUR + 30 * 60)
        self.assertEqual(self.rig.tempo.paused_s(session), 40 * 60)

    def test_archived_project_cannot_start(self) -> None:
        ok(self.rig.tempo.archive_project(self.project.id))
        self.assertEqual(
            self.rig.tempo.start(self.project.id, HOUR, ""), Err(ProjectArchived("web"))
        )

    def test_every_write_syncs_supervisor(self) -> None:
        before = self.rig.supervisor.calls
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        ok(self.rig.tempo.pause())
        ok(self.rig.tempo.resume())
        ok(self.rig.tempo.extend(60))
        ok(self.rig.tempo.stop(None))
        self.assertEqual(self.rig.supervisor.calls - before, 5)
        # Las lecturas no sincronizan.
        self.rig.tempo.status()
        self.rig.tempo.list_projects()
        self.assertEqual(self.rig.supervisor.calls - before, 5)


class EditDeleteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = make_rig(at(10, 15, 10))
        self.project = ok(self.rig.tempo.add_project("web", None, ""))
        ok(self.rig.tempo.start(self.project.id, 2 * HOUR, "orig"))
        self.rig.clock.advance(timedelta(minutes=30))
        ok(self.rig.tempo.pause())
        self.rig.clock.advance(timedelta(minutes=30))
        ok(self.rig.tempo.resume())
        self.rig.clock.advance(timedelta(minutes=30))
        self.session = ok(self.rig.tempo.stop(None))  # 10:00-11:30, pausa 10:30-11:00
        self.rig.clock.set(at(11, 9, 10))

    def edit(self, **changes: object) -> object:
        fields: dict[str, object] = dict(
            project_id=self.project.id,
            description="nueva",
            start_at=self.session.start_at,
            end_at=self.session.end_at,
        )
        fields.update(changes)
        return self.rig.tempo.edit_session(self.session.id, **fields)  # type: ignore[arg-type]

    def test_edit_clips_pauses(self) -> None:
        edited = ok(self.rig.tempo.edit_session(
            self.session.id, project_id=self.project.id, description="nueva",
            start_at=at(10, 15, 10, 45), end_at=at(10, 15, 11, 30),
        ))
        self.assertEqual(edited.description, "nueva")
        self.assertEqual(self.rig.tempo.paused_s(edited), 15 * 60)
        self.assertEqual(self.rig.tempo.worked_s(edited), 30 * 60)

    def test_invalid_interval(self) -> None:
        result = self.edit(end_at=at(10, 15, 9))
        self.assertEqual(result, Err(InvalidInterval(self.session.start_at, at(10, 15, 9))))

    def test_locked_month_blocks_edit_and_delete(self) -> None:
        self.rig.store.add_export("2026-10", at(11, 1, 9))
        self.assertEqual(self.edit(), Err(MonthLocked("2026-10")))
        self.assertEqual(self.rig.tempo.delete_session(self.session.id), Err(MonthLocked("2026-10")))

    def test_cannot_move_session_into_locked_month(self) -> None:
        self.rig.store.add_export("2026-09", at(10, 1, 9))
        result = self.edit(start_at=at(9, 30, 10), end_at=at(9, 30, 11))
        self.assertEqual(result, Err(MonthLocked("2026-09")))

    def test_open_session_cannot_be_edited(self) -> None:
        active = ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        result = self.rig.tempo.edit_session(
            active.session.id, project_id=self.project.id, description="",
            start_at=at(11, 9, 10), end_at=at(11, 9, 11),
        )
        self.assertEqual(result, Err(SessionIsActive(active.session.id)))

    def test_delete(self) -> None:
        ok(self.rig.tempo.delete_session(self.session.id))
        self.assertEqual(self.rig.tempo.list_sessions(at(1, 1, 0)), [])


class ClientsProjectsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = make_rig()
        self.tempo = self.rig.tempo
        self.acme = ok(self.tempo.add_client("Acme"))
        self.tienda = ok(self.tempo.add_project("tienda", self.acme.id, ""))
        self.blog = ok(self.tempo.add_project("blog", self.acme.id, ""))

    def test_empty_names_rejected(self) -> None:
        self.assertEqual(self.tempo.add_client("  "), Err(InvalidName("  ")))
        self.assertEqual(self.tempo.add_project("", None, ""), Err(InvalidName("")))

    def test_archive_client_requires_cascade_decision(self) -> None:
        self.assertEqual(
            self.tempo.archive_client(self.acme.id, cascade=False),
            Err(ClientHasActiveProjects(("blog", "tienda"))),
        )
        self.assertEqual(len(self.tempo.list_projects()), 2)  # no se archivo nada

    def test_archive_client_cascade(self) -> None:
        archived = ok(self.tempo.archive_client(self.acme.id, cascade=True))
        self.assertEqual({p.name for p in archived}, {"tienda", "blog"})
        self.assertEqual(self.tempo.list_projects(), [])
        self.assertEqual(self.tempo.list_clients(), [])
        self.assertEqual(len(self.tempo.list_clients(include_archived=True)), 1)

    def test_unarchive_client_only_reactivates_client(self) -> None:
        ok(self.tempo.archive_client(self.acme.id, cascade=True))
        ok(self.tempo.unarchive_client(self.acme.id))
        self.assertEqual([c.name for c in self.tempo.list_clients()], ["Acme"])
        self.assertEqual(self.tempo.list_projects(), [])

    def test_unarchive_project_with_archived_client(self) -> None:
        ok(self.tempo.archive_client(self.acme.id, cascade=True))
        self.assertEqual(
            self.tempo.unarchive_project(self.tienda.id, with_client=False),
            Err(ClientArchived("Acme")),
        )
        ok(self.tempo.unarchive_project(self.tienda.id, with_client=True))
        self.assertEqual([p.name for p in self.tempo.list_projects()], ["tienda"])
        self.assertEqual([c.name for c in self.tempo.list_clients()], ["Acme"])

    def test_cannot_add_project_to_archived_client(self) -> None:
        ok(self.tempo.archive_client(self.acme.id, cascade=True))
        self.assertEqual(
            self.tempo.add_project("nuevo", self.acme.id, ""), Err(ClientArchived("Acme"))
        )

    def test_edit_keeps_sessions(self) -> None:
        ok(self.tempo.start(self.tienda.id, HOUR, ""))
        ok(self.tempo.edit_project(self.tienda.id, name="tienda-online", description=" Shop "))
        ok(self.tempo.edit_client(self.acme.id, "Acme SRL"))
        session = ok(self.tempo.status()).session
        self.assertEqual((session.project_name, session.client_name), ("tienda-online", "Acme SRL"))
        self.assertEqual(ok(self.tempo.find_project("tienda-online")).description, "Shop")

    def test_exported_month_blocks_edit(self) -> None:
        ok(self.tempo.start(self.tienda.id, HOUR, ""))
        ok(self.tempo.stop(None))
        self.rig.store.add_export("2026-11", at(11, 9, 12))
        locked = Err(MonthLocked("2026-11"))
        self.assertEqual(self.tempo.edit_project(self.tienda.id, name="x", description=""), locked)
        self.assertEqual(self.tempo.edit_client(self.acme.id, "x"), locked)
        ok(self.tempo.edit_project(self.blog.id, name="blog-2", description=""))  # sin sesiones

    def test_find_projects(self) -> None:
        found = ok(self.tempo.find_projects(["tienda", "blog"]))
        self.assertEqual([p.name for p in found], ["tienda", "blog"])
        self.assertIsInstance(self.tempo.find_projects(["tienda", "nope"]), Err)


class ConcurrencyTest(unittest.TestCase):
    """La validacion corre dentro de BEGIN IMMEDIATE: otro escritor no puede
    cambiar el estado entre la validacion y la escritura."""

    def test_pause_waits_for_concurrent_writer_and_revalidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tempo.db"
            other = SqliteStore(path)
            project = ok(other.add_project("web", None, ""))
            session = ok(other.add_session(project.id, "", at(11, 9, 10), HOUR))
            results: list[object] = []

            def pause_from_another_connection() -> None:
                store = SqliteStore(path)  # sqlite3: una conexion por hilo
                tempo = Tempo(store, FakeClock(at(11, 9, 10, 30)), FakeSupervisor(),
                              saved_reports=MemoryReportStore(), loose_reports=MemoryReportStore())
                results.append(tempo.pause())
                store.close()

            with other.transaction():  # el otro escritor toma el lock y pausa
                other.open_pause(session.id, at(11, 9, 10, 20))
                worker = threading.Thread(target=pause_from_another_connection)
                worker.start()
                time.sleep(0.3)  # el hilo queda esperando el lock en BEGIN IMMEDIATE
            worker.join(timeout=5)
            other.close()
            self.assertEqual(results, [Err(AlreadyPaused(at(11, 9, 10, 20)))])


if __name__ == "__main__":
    unittest.main()
