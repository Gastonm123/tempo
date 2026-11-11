"""Funciones puras para leer y mostrar duraciones."""

from __future__ import annotations

import re

from tempo.result import Err, InvalidDuration, Ok, Result

# "1h", "90m", "1h30m", "1h 30m", "2H". Al menos una parte con unidad.
_DURATION = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*$", re.IGNORECASE)


def parse(text: str) -> Result[int, InvalidDuration]:
    """Convierte un texto como "1h30m" en segundos. Debe ser mayor a cero."""
    match = _DURATION.match(text)
    if match is None or (match.group(1) is None and match.group(2) is None):
        return Err(InvalidDuration(text))
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = hours * 3600 + minutes * 60
    if seconds <= 0:
        return Err(InvalidDuration(text))
    return Ok(seconds)


def format_hm(seconds: int) -> str:
    """Segundos como H:MM (minutos completos, sin redondear hacia arriba)."""
    total_minutes = max(seconds, 0) // 60
    return f"{total_minutes // 60}:{total_minutes % 60:02d}"


def format_hm_up(seconds: int) -> str:
    """Como format_hm pero redondeando hacia arriba: para tiempo restante
    (59 segundos se muestran como 0:01, no como 0:00)."""
    return format_hm(max(seconds, 0) + 59)


def format_duration(seconds: int) -> str:
    """Segundos como "1h30m", "5m" o "2h": el mismo formato con que se escriben.

    Minutos completos, sin redondear hacia arriba; las partes en cero se omiten
    salvo que todo sea cero ("0m").
    """
    total_minutes = max(seconds, 0) // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h{minutes}m"
    return f"{hours}h" if hours else f"{minutes}m"


def format_duration_up(seconds: int) -> str:
    """Como format_duration pero redondeando hacia arriba: para tiempo restante."""
    return format_duration(max(seconds, 0) + 59)


def format_hours(seconds: int) -> str:
    """Segundos como horas decimales con 2 cifras, por ejemplo 5400 -> "1.50"."""
    return f"{max(seconds, 0) / 3600:.2f}"
