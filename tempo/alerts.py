"""Notificaciones de escritorio: interfaz `Alerter` e implementaciones."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Protocol

from tempo.result import AlertFailed, Err, Ok, Result


class Alerter(Protocol):
    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        """Muestra un aviso comun."""
        ...

    def alarm(self, title: str, body: str, duration: timedelta) -> Result[None, AlertFailed]:
        """Muestra un aviso que suena en loop hasta `silence()` o hasta `duration`."""
        ...

    def silence(self) -> Result[None, AlertFailed]:
        """Corta la alarma, si hay una sonando."""
        ...


# Los textos llegan por variables de entorno: asi no hay que escapar comillas
# ni caracteres especiales dentro de los scripts.
_PRELUDE = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
# "tempo" si install.sh lo registro (nombre e icono propios); si no, el de PowerShell.
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
if (Test-Path 'HKCU:\Software\Classes\AppUserModelId\tempo') { $appId = 'tempo' }
function New-Toast($xmlText) {
    $xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
    $xml.LoadXml($xmlText)
    $texts = $xml.GetElementsByTagName('text')
    $texts.Item(0).AppendChild($xml.CreateTextNode($env:TEMPO_TITLE)) > $null
    $texts.Item(1).AppendChild($xml.CreateTextNode($env:TEMPO_BODY)) > $null
    return [Windows.UI.Notifications.ToastNotification]::new($xml)
}
"""

_TOAST_SCRIPT = _PRELUDE + r"""
$toast = New-Toast '<toast><visual><binding template="ToastGeneric"><text/><text/></binding></visual></toast>'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""

# scenario="alarm" deja el toast en pantalla y el audio en loop hasta que se
# cierra; Windows exige al menos un boton para respetar ese escenario.
# ExpirationTime lo saca aunque el proceso de aviso muera mientras suena.
_ALARM_SCRIPT = _PRELUDE + r"""
$toast = New-Toast @'
<toast scenario="alarm">
  <visual><binding template="ToastGeneric"><text/><text/></binding></visual>
  <audio src="ms-winsoundevent:Notification.Looping.Alarm" loop="true"/>
  <actions><action content="Detener" arguments="dismiss" activationType="system"/></actions>
</toast>
'@
$toast.Tag = 'alarm'
$toast.Group = 'tempo'
$toast.ExpirationTime = [DateTimeOffset]::Now.AddSeconds([int]$env:TEMPO_ALARM_SECONDS)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""

# Sacar el toast del historial lo cierra y corta el audio, aunque lo haya
# mostrado otro powershell.exe. Si no esta (ya vencio o se cerro), no falla.
_SILENCE_SCRIPT = _PRELUDE + r"""
[Windows.UI.Notifications.ToastNotificationManager]::History.Remove('alarm', 'tempo', $appId)
"""


def _run(command: Sequence[str], env: dict[str, str] | None = None) -> Result[None, AlertFailed]:
    try:
        done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return Err(AlertFailed(f"{command[0]}: {error}"))
    if done.returncode != 0:
        return Err(AlertFailed(f"{command[0]} terminó con {done.returncode}: {done.stderr.strip()}"))
    return Ok(None)


class WindowsToastAlerter(Alerter):
    """Toast de Windows desde WSL, usando powershell.exe."""

    def _powershell(self, script: str, **values: str) -> Result[None, AlertFailed]:
        executable = shutil.which("powershell.exe")
        if executable is None:
            return Err(AlertFailed("no se encontró powershell.exe"))
        # WSLENV indica que variables pasan del entorno de WSL al de Windows.
        env = {**os.environ, **values, "WSLENV": ":".join(values)}
        return _run([executable, "-NoProfile", "-NonInteractive", "-Command", script], env)

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        return self._powershell(_TOAST_SCRIPT, TEMPO_TITLE=title, TEMPO_BODY=body)

    def alarm(self, title: str, body: str, duration: timedelta) -> Result[None, AlertFailed]:
        return self._powershell(_ALARM_SCRIPT, TEMPO_TITLE=title, TEMPO_BODY=body,
                                TEMPO_ALARM_SECONDS=str(int(duration.total_seconds())))

    def silence(self) -> Result[None, AlertFailed]:
        return self._powershell(_SILENCE_SCRIPT)


class NotifySendAlerter(Alerter):
    """Notificacion de Linux (libnotify)."""

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        executable = shutil.which("notify-send")
        if executable is None:
            return Err(AlertFailed("no se encontró notify-send"))
        return _run([executable, title, body])

    def alarm(self, title: str, body: str, duration: timedelta) -> Result[None, AlertFailed]:
        # libnotify no tiene alarma en loop: queda como aviso comun.
        return self.send(title, body)

    def silence(self) -> Result[None, AlertFailed]:
        return Ok(None)


class FirstWorkingAlerter(Alerter):
    """Prueba cada alerter en orden hasta que uno funcione."""

    def __init__(self, alerters: Sequence[Alerter]) -> None:
        self._alerters = list(alerters)

    def _first(self, show: Callable[[Alerter], Result[None, AlertFailed]]
               ) -> Result[None, AlertFailed]:
        reasons: list[str] = []
        for alerter in self._alerters:
            match show(alerter):
                case Ok():
                    return Ok(None)
                case Err(AlertFailed(reason)):
                    reasons.append(reason)
        return Err(AlertFailed("; ".join(reasons) or "no hay alerters"))

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        return self._first(lambda alerter: alerter.send(title, body))

    def alarm(self, title: str, body: str, duration: timedelta) -> Result[None, AlertFailed]:
        return self._first(lambda alerter: alerter.alarm(title, body, duration))

    def silence(self) -> Result[None, AlertFailed]:
        # No se sabe cual mostro la alarma: se le pide a todos.
        reasons: list[str] = []
        for alerter in self._alerters:
            match alerter.silence():
                case Ok():
                    pass
                case Err(AlertFailed(reason)):
                    reasons.append(reason)
        return Err(AlertFailed("; ".join(reasons))) if reasons else Ok(None)


class NullAlerter(Alerter):
    """No muestra nada (TEMPO_NO_NOTIFY=1)."""

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        return Ok(None)

    def alarm(self, title: str, body: str, duration: timedelta) -> Result[None, AlertFailed]:
        return Ok(None)

    def silence(self) -> Result[None, AlertFailed]:
        return Ok(None)


class RecordingAlerter(Alerter):
    """Para tests: registra en orden los avisos pedidos."""

    def __init__(self) -> None:
        self.events: list[tuple[str, ...]] = []

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        self.events.append(("send", title, body))
        return Ok(None)

    def alarm(self, title: str, body: str, duration: timedelta) -> Result[None, AlertFailed]:
        self.events.append(("alarm", title, body))
        return Ok(None)

    def silence(self) -> Result[None, AlertFailed]:
        self.events.append(("silence",))
        return Ok(None)
