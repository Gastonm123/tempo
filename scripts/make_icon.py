"""Genera el icono de tempo (PNG 256x256) solo con la stdlib.

Uso: python3 scripts/make_icon.py salida.png
Dibuja un reloj blanco sobre un cuadrado redondeado con degrade, con
suavizado por supermuestreo (4x4 muestras por pixel).
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

SIZE = 256
SAMPLES = 4

TOP = (20, 184, 166)  # verde azulado
BOTTOM = (37, 99, 235)  # azul
FACE = (255, 255, 255)
HANDS = (30, 41, 59)
ACCENT = (249, 115, 22)  # naranja


def rounded_square(x: float, y: float, margin: float = 12, radius: float = 56) -> bool:
    lo, hi = margin, SIZE - margin
    if not (lo <= x <= hi and lo <= y <= hi):
        return False
    cx = min(max(x, lo + radius), hi - radius)
    cy = min(max(y, lo + radius), hi - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def near_segment(x: float, y: float, angle_deg: float, length: float, width: float,
                 start: float = 0.0) -> bool:
    """Segmento desde el centro en la direccion de una aguja (0 grados = las 12)."""
    angle = math.radians(angle_deg)
    dx, dy = math.sin(angle), -math.cos(angle)
    cx = cy = SIZE / 2
    t = (x - cx) * dx + (y - cy) * dy
    if not start <= t <= length:
        return False
    px, py = cx + t * dx, cy + t * dy
    return (x - px) ** 2 + (y - py) ** 2 <= (width / 2) ** 2


def color_at(x: float, y: float) -> tuple[int, int, int, int]:
    if not rounded_square(x, y):
        return (0, 0, 0, 0)
    r = math.hypot(x - SIZE / 2, y - SIZE / 2)
    if r <= 10:
        return (*ACCENT, 255)
    if r <= 84:
        # Agujas en 10:10: horaria a 305 grados, minutero a 60 grados.
        if near_segment(x, y, 305, 48, 14) or near_segment(x, y, 60, 70, 10):
            return (*HANDS, 255)
        for hour in range(12):
            long_tick = hour % 3 == 0
            if near_segment(x, y, hour * 30, 76, 8 if long_tick else 5,
                            start=62 if long_tick else 68):
                return (*HANDS, 255)
        return (*FACE, 255)
    t = y / SIZE

    def mix(a: int, b: int) -> int:
        return round(a + (b - a) * t)

    return (mix(TOP[0], BOTTOM[0]), mix(TOP[1], BOTTOM[1]), mix(TOP[2], BOTTOM[2]), 255)


def render() -> bytes:
    rows = bytearray()
    step = 1 / SAMPLES
    for py in range(SIZE):
        rows.append(0)  # filtro PNG "None" para cada fila
        for px in range(SIZE):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    r, g, b, a = color_at(px + (sx + 0.5) * step, py + (sy + 0.5) * step)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            alpha = acc[3] / SAMPLES**2
            if acc[3] == 0:
                rows += bytes(4)
            else:
                rows += bytes((round(acc[0] / acc[3]), round(acc[1] / acc[3]),
                               round(acc[2] / acc[3]), round(alpha)))
    return bytes(rows)


def png(raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8 bits, RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    Path(sys.argv[1]).write_bytes(png(render()))
