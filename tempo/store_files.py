"""Donde se escriben los reportes: interfaz `ReportStore` e implementaciones."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from tempo.result import Err, FileExists, Ok, Result


class ReportStore(Protocol):
    def save(self, name: str, content: str, *, overwrite: bool) -> Result[Path, FileExists]:
        """Escribe `content` en `name` (relativo al destino, o una ruta absoluta).

        Con overwrite=False, si el archivo ya existe no escribe nada.
        """
        ...


class DiskReportStore(ReportStore):
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def save(self, name: str, content: str, *, overwrite: bool) -> Result[Path, FileExists]:
        path = (self._directory / name).resolve()  # una ruta absoluta en `name` gana
        path.parent.mkdir(parents=True, exist_ok=True)
        # "x" crea el archivo solo si no existe, de forma atomica.
        mode = "w" if overwrite else "x"
        try:
            with path.open(mode, encoding="utf-8", newline="") as file:
                file.write(content)
        except FileExistsError:
            return Err(FileExists(path))
        return Ok(path)


class MemoryReportStore(ReportStore):
    """Para tests: guarda los archivos en un diccionario."""

    def __init__(self, directory: Path = Path("/memoria")) -> None:
        self._directory = directory
        self.files: dict[Path, str] = {}

    def save(self, name: str, content: str, *, overwrite: bool) -> Result[Path, FileExists]:
        path = self._directory / name
        if path in self.files and not overwrite:
            return Err(FileExists(path))
        self.files[path] = content
        return Ok(path)
