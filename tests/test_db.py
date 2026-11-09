import sqlite3
import unittest
from datetime import timedelta

from tempo.db import SqliteStore
from tempo.result import Err, NameTaken
from tempo.sessions import Interval
from tests.helpers import HOUR, at, ok


class SchemaRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SqliteStore(":memory:")
        self.project = ok(self.store.add_project("web", None, ""))

    def test_unique_names(self) -> None:
        self.assertEqual(self.store.add_project("web", None, ""), Err(NameTaken("web")))
        ok(self.store.add_client("Acme"))
        self.assertEqual(self.store.add_client("Acme"), Err(NameTaken("Acme")))

    def test_project_client_is_immutable(self) -> None:
        acme = ok(self.store.add_client("Acme"))
        other = ok(self.store.add_client("Otro"))
        project = ok(self.store.add_project("tienda", acme.id, ""))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._conn.execute(
                "UPDATE projects SET client_id = ? WHERE id = ?", (other.id, project.id)
            )

    def test_transaction_rolls_back_on_crash(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                ok(self.store.add_client("Fantasma"))
                raise RuntimeError("falla inesperada")
        self.assertIsNone(self.store.find_client("Fantasma"))


class RoundTripTest(unittest.TestCase):
    def test_dates_keep_the_instant(self) -> None:
        store = SqliteStore(":memory:")
        project = ok(store.add_project("web", None, ""))
        start = at(11, 9, 23, 30)
        session = ok(store.add_session(project.id, "x", start, HOUR))
        store.add_closed_pause(session.id, start + timedelta(minutes=5), start + timedelta(minutes=10))
        loaded = store.get_session(session.id)
        assert loaded is not None
        self.assertEqual(loaded.start_at, start)
        [pause] = loaded.pauses
        self.assertEqual(
            (pause.start, pause.end, pause.reminded),
            (start + timedelta(minutes=5), start + timedelta(minutes=10), False),
        )
        self.assertIsInstance(pause.id, int)

    def test_sessions_between_and_limit(self) -> None:
        store = SqliteStore(":memory:")
        project = ok(store.add_project("web", None, ""))
        for day in (1, 2, 3):
            s = ok(store.add_session(project.id, "", at(11, day, 10), HOUR))
            store.update_session(
                s.id, project_id=project.id, description="", start_at=s.start_at,
                duration_s=HOUR, end_at=at(11, day, 11),
            )
        all_days = store.sessions_between(at(11, 1, 0), at(11, 4, 0))
        self.assertEqual([s.start_at.day for s in all_days], [1, 2, 3])
        last_two = store.sessions_between(at(11, 1, 0), at(11, 4, 0), limit=2)
        self.assertEqual([s.start_at.astimezone(at(11, 1, 0).tzinfo).day for s in last_two], [2, 3])

if __name__ == "__main__":
    unittest.main()
