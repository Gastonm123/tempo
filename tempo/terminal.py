"""Interfaz `Terminal`: todo lo que la CLI pregunta y muestra pasa por aca.

La interpretacion de lo que se tipea vive en funciones puras (`read_choice`,
`read_yes_no`), asi las implementaciones la comparten sin heredar codigo.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from tempo import durations
from tempo.result import Err, Ok


@dataclass(frozen=True, slots=True)
class Picked:
    """Se eligio una opcion de la lista."""

    index: int


@dataclass(frozen=True, slots=True)
class Typed:
    """Se escribio un valor que no esta en la lista."""

    text: str


type Choice = Picked | Typed


class Terminal(Protocol):
    def write(self, text: str) -> None:
        """Muestra una linea."""
        ...

    def error(self, text: str) -> None:
        """Muestra un mensaje de error."""
        ...

    def width(self) -> int:
        """Ancho disponible en caracteres."""
        ...

    def text(self, prompt: str, default: str = "") -> str:
        """Pide un texto. Enter sin escribir nada devuelve `default`."""
        ...

    def choose(self, prompt: str, options: Sequence[str], *, title: str,
               allow_typed: bool) -> Choice:
        """Muestra `title` y debajo una lista numerada, y pide elegir un numero.

        Con allow_typed=True tambien acepta un texto que no este en la lista.
        Repite la pregunta hasta obtener una respuesta valida.
        """
        ...

    def confirm(self, prompt: str) -> bool:
        """Pregunta si o no. Enter sin escribir nada es "no"."""
        ...

    def duration(self, prompt: str) -> int:
        """Pide una duracion como "1h30m" y la devuelve en segundos.

        Repite la pregunta hasta obtener una duracion valida.
        """
        ...


# --- Interpretacion pura de respuestas -------------------------------------


def read_choice(answer: str, options: Sequence[str], *, allow_typed: bool) -> Choice | None:
    """None = respuesta invalida (hay que volver a preguntar)."""
    value = answer.strip()
    if value.isdigit() and 1 <= int(value) <= len(options):
        return Picked(int(value) - 1)
    if value in options:
        return Picked(list(options).index(value))
    if allow_typed and value and not value.isdigit():
        return Typed(value)
    return None


def read_yes_no(answer: str) -> bool | None:
    """None = respuesta invalida."""
    value = answer.strip().lower()
    if value in ("s", "si", "sí", "y", "yes"):
        return True
    if value in ("", "n", "no"):
        return False
    return None


def choice_lines(title: str, options: Sequence[str]) -> list[str]:
    return [f"{title}:",
            *(f"  {number}) {option}" for number, option in enumerate(options, start=1))]


def choose_hint(allow_typed: bool) -> str:
    return "número o nombre nuevo" if allow_typed else "número"


# --- Implementaciones ------------------------------------------------------


class PosixTerminal(Terminal):
    """Terminal real: lee de stdin y escribe en stdout/stderr."""

    def write(self, text: str) -> None:
        print(text)

    def error(self, text: str) -> None:
        print(f"Error: {text}", file=sys.stderr)

    def width(self) -> int:
        return shutil.get_terminal_size().columns

    def text(self, prompt: str, default: str = "") -> str:
        shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
        return input(shown).strip() or default

    def choose(self, prompt: str, options: Sequence[str], *, title: str,
               allow_typed: bool) -> Choice:
        for line in choice_lines(title, options):
            print(line)
        while True:
            choice = read_choice(input(f"{prompt} ({choose_hint(allow_typed)}): "), options,
                                 allow_typed=allow_typed)
            if choice is not None:
                return choice
            print("  Opción inválida.")

    def confirm(self, prompt: str) -> bool:
        while True:
            answer = read_yes_no(input(f"{prompt} [s/N]: "))
            if answer is not None:
                return answer
            print("  Respondé s o n.")

    def duration(self, prompt: str) -> int:
        while True:
            match durations.parse(input(f"{prompt} (p. ej. 1h, 90m, 1h30m): ")):
                case Ok(seconds):
                    return seconds
                case Err():
                    print("  Duración inválida.")


class DummyTerminal(Terminal):
    """Para tests: responde con una lista guionada y registra todo lo mostrado."""

    def __init__(self, answers: Sequence[str] = (), width: int = 200) -> None:
        self._answers = list(answers)
        self._width = width
        self.lines: list[str] = []
        self.errors: list[str] = []

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def _next(self, prompt: str) -> str:
        if not self._answers:
            raise AssertionError(f"el test no previó una respuesta para: {prompt!r}")
        answer = self._answers.pop(0)
        self.lines.append(f"{prompt}> {answer}")
        return answer

    def write(self, text: str) -> None:
        self.lines.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)

    def width(self) -> int:
        return self._width

    def text(self, prompt: str, default: str = "") -> str:
        return self._next(prompt).strip() or default

    def choose(self, prompt: str, options: Sequence[str], *, title: str,
               allow_typed: bool) -> Choice:
        self.lines.extend(choice_lines(title, options))
        while True:
            choice = read_choice(self._next(prompt), options, allow_typed=allow_typed)
            if choice is not None:
                return choice

    def confirm(self, prompt: str) -> bool:
        while True:
            answer = read_yes_no(self._next(prompt))
            if answer is not None:
                return answer

    def duration(self, prompt: str) -> int:
        while True:
            match durations.parse(self._next(prompt)):
                case Ok(seconds):
                    return seconds
                case Err():
                    pass

    @property
    def unused_answers(self) -> list[str]:
        return list(self._answers)
