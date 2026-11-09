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
        return Notifier(self.rig.store, self.rig.clock, self.alerter,
                        (timedelta(minutes=30), timedelta(hours=1)),
                        alarm_duration=timedelta(minutes=5))

    def advance(self, minutes: int) -> None:
        self.rig.clock.advance(timedelta(minutes=minutes))

    def kinds(self) -> list[str]:
        return [event[0] for event in self.alerter.events]

    def test_expiry_alarm_once_silenced_and_rearmed_by_extend(self) -> None:
        notifier = self.notifier()
        self.assertIsNone(notifier.tick())  # sin sesion: espera sin limite
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.assertEqual(notifier.tick(), HOUR)  # despierta justo al vencer
        self.advance(60)
        self.assertEqual(notifier.tick(), 300)  # despierta para cortar la alarma
        self.assertEqual(notifier.tick(), 300)
        _, title, body = self.alerter.events[0]
        self.assertEqual(title, "Sesión terminada")
        self.assertTrue(body.startswith("Se cumplió el tiempo de web"))
        self.advance(5)
        self.assertIsNone(notifier.tick())
        self.assertEqual(self.kinds(), ["alarm", "silence"])

        ok(self.rig.tempo.extend(30 * 60))  # el proceso real recibiria SIGHUP
        self.assertEqual(notifier.tick(), 30 * 60)
        self.advance(30)
        notifier.tick()
        self.advance(2)
        ok(self.rig.tempo.extend(30 * 60))  # mientras suena: la corta
        self.assertEqual(notifier.tick(), 30 * 60)
        self.assertEqual(self.kinds(), ["alarm", "silence", "alarm", "silence"])

    def test_stop_silences_alarm(self) -> None:
        notifier = self.notifier()
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        notifier.tick()
        self.advance(60)
        notifier.tick()
        ok(self.rig.tempo.stop(None))
        self.assertIsNone(notifier.tick())
        self.assertEqual(self.kinds(), ["alarm", "silence"])

    def test_no_alert_for_expiry_not_seen_coming(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        self.advance(90)
        self.notifier().tick()  # el proceso arranco con la sesion ya vencida
        self.assertEqual(self.alerter.events, [])

    def test_pause_reminders_at_30_and_60_minutes(self) -> None:
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        ok(self.rig.tempo.pause())
        notifier = self.notifier()
        self.assertEqual(notifier.tick(), 30 * 60)
        self.advance(30)
        self.assertEqual(notifier.tick(), 30 * 60)
        self.assertEqual(notifier.tick(), 30 * 60)  # no repite el de los 30
        self.assertEqual(self.alerter.events[0][:2], ("send", "Pausa larga"))
        self.advance(30)
        self.assertIsNone(notifier.tick())
        self.assertIsNone(notifier.tick())
        self.assertEqual(self.kinds(), ["send", "send"])

    def test_pause_reminders_after_restart(self) -> None:
        # Un proceso nuevo avisa una vez por los umbrales ya pasados y sigue con el proximo.
        ok(self.rig.tempo.start(self.project.id, HOUR, ""))
        ok(self.rig.tempo.pause())
        self.advance(40)
        notifier = self.notifier()
        self.assertEqual(notifier.tick(), 20 * 60)
        self.advance(20)
        notifier.tick()
        self.assertEqual(self.kinds(), ["send", "send"])
        self.advance(30)
        self.assertIsNone(self.notifier().tick())
        self.assertEqual(self.kinds(), ["send", "send", "send"])

if __name__ == "__main__":
    unittest.main()
