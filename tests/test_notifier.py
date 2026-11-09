import unittest
from datetime import timedelta

from tempo.alerts import RecordingAlerter
from tempo.notifier import Notifier
from tests.helpers import HOUR, at, make_rig, ok


class NotifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = make_rig(at(11, 9, 10))
        self.project = ok(self.rig.tempo.add_project("web", None, ""))
        self.alerter = RecordingAlerter()

    def notifier(self) -> Notifier:
        return Notifier(self.rig.store, self.rig.clock, self.alerter, timedelta(hours=1))

    def advance(self, minutes: int) -> None:
        self.rig.clock.advance(timedelta(minutes=minutes))

    def test_expiry_alert_once_and_rearmed_by_extend(self) -> None:
        notifier = self.notifier()
        self.assertIsNone(notifier.tick())  # sin sesion: espera sin limite
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.assertEqual(notifier.tick(), HOUR)  # despierta justo al vencer
        self.advance(60)
        self.assertIsNone(notifier.tick())
        self.assertIsNone(notifier.tick())
        self.assertEqual(len(self.alerter.sent), 1)
        self.assertEqual(self.alerter.sent[0][0], "Sesión terminada")
        self.assertTrue(self.alerter.sent[0][1].startswith("Se cumplió el tiempo de web"))

        ok(self.rig.tempo.extend(30 * 60))  # el proceso real recibiria SIGHUP
        self.assertEqual(notifier.tick(), 30 * 60)
        self.advance(30)
        notifier.tick()
        self.assertEqual(len(self.alerter.sent), 2)

    def test_no_alert_for_expiry_not_seen_coming(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.advance(90)
        self.notifier().tick()  # el proceso arranco con la sesion ya vencida
        self.assertEqual(self.alerter.sent, [])

    def test_pause_reminder_once_even_after_restart(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        ok(self.rig.tempo.pause())
        notifier = self.notifier()
        self.assertEqual(notifier.tick(), HOUR)
        self.advance(60)
        self.assertIsNone(notifier.tick())
        self.assertEqual([title for title, _ in self.alerter.sent], ["Pausa larga"])
        self.advance(120)
        self.notifier().tick()  # un proceso nuevo no repite el aviso: quedo en la base
        self.assertEqual(len(self.alerter.sent), 1)

if __name__ == "__main__":
    unittest.main()
