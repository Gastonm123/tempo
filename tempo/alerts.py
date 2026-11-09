"""Notificaciones de escritorio: interfaz `Alerter` e implementaciones."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from typing import Protocol

from tempo.result import AlertFailed, Err, Ok, Result


class Alerter(Protocol):
    def send(self, title: str, body: str) -> Result[None, AlertFailed]: ...


# El titulo y el cuerpo llegan por variables de entorno: asi no hay que
# escapar comillas ni caracteres especiales dentro del script.
_TOAST_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($env:TEMPO_TITLE)) > $null
$texts.Item(1).AppendChild($template.CreateTextNode($env:TEMPO_BODY)) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
# "tempo" si install.sh lo registro (nombre e icono propios); si no, el de PowerShell.
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
if (Test-Path 'HKCU:\Software\Classes\AppUserModelId\tempo') { $appId = 'tempo' }
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
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

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        executable = shutil.which("powershell.exe")
        if executable is None:
            return Err(AlertFailed("no se encontró powershell.exe"))
        env = {**os.environ, "TEMPO_TITLE": title, "TEMPO_BODY": body,
               "WSLENV": "TEMPO_TITLE:TEMPO_BODY"}  # WSLENV pasa estas variables a Windows
        return _run([executable, "-NoProfile", "-NonInteractive", "-Command", _TOAST_SCRIPT], env)


class NotifySendAlerter(Alerter):
    """Notificacion de Linux (libnotify)."""

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        executable = shutil.which("notify-send")
        if executable is None:
            return Err(AlertFailed("no se encontró notify-send"))
        return _run([executable, title, body])


class FirstWorkingAlerter(Alerter):
    """Prueba cada alerter en orden hasta que uno funcione."""

    def __init__(self, alerters: Sequence[Alerter]) -> None:
        self._alerters = list(alerters)

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        reasons: list[str] = []
        for alerter in self._alerters:
            match alerter.send(title, body):
                case Ok():
                    return Ok(None)
                case Err(AlertFailed(reason)):
                    reasons.append(reason)
        return Err(AlertFailed("; ".join(reasons) or "no hay alerters"))


class NullAlerter(Alerter):
    """No muestra nada (TEMPO_NO_NOTIFY=1)."""

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        return Ok(None)


class RecordingAlerter(Alerter):
    """Para tests: registra los avisos pedidos."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, body: str) -> Result[None, AlertFailed]:
        self.sent.append((title, body))
        return Ok(None)
