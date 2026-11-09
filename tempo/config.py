"""Configuracion: un objeto de datos inmutable que se arma una sola vez."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

DEFAULT_HOME = "~/.local/tempo"


@dataclass(frozen=True, slots=True)
class Config:
    home: Path
    notify_enabled: bool = True
    pause_reminders: tuple[timedelta, ...] = (timedelta(minutes=30), timedelta(hours=1))
    alarm_duration: timedelta = timedelta(minutes=5)
    list_days: int = 7
    report_days: int = 30

    @property
    def db_path(self) -> Path:
        return self.home / "tempo.db"

    @property
    def pid_path(self) -> Path:
        return self.home / "notifier.pid"

    @property
    def lock_path(self) -> Path:
        return self.home / "notifier.lock"

    @property
    def reports_dir(self) -> Path:
        return self.home / "reports"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        """Unica lectura del entorno en todo el programa."""
        home = Path(env.get("TEMPO_HOME", DEFAULT_HOME)).expanduser().resolve()
        return cls(home=home, notify_enabled=env.get("TEMPO_NO_NOTIFY") != "1")
