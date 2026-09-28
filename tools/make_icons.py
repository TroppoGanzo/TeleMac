#!/usr/bin/env python3
"""Genera le icone PNG di TeleMac (180/192/512 px).

Piccolo motore di disegno vettoriale scritto a mano, con antialiasing per
supersampling (4x4 campioni per pixel) e compressione PNG via `zlib`: nessuna
dipendenza esterna, solo libreria standard.

Disegno: quadrato con un leggero gradiente verticale grafite -> nero, un
telecomando stilizzato (clickpad circolare grigio chiaro con pulsante
centrale e 4 puntini per le zone direzionali) e un piccolo arco "onde"
azzurro in alto a destra che richiama il segnale senza fili.

iOS arrotonda da solo gli angoli delle icone della schermata Home: qui NON
arrotondiamo nulla e non usiamo trasparenza (l'immagine è RGB piena, niente
canale alpha).
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import Tuple

Color = Tuple[int, int, int]

# --- Palette (vedi SPEC §5) -------------------------------------------------
BG_TOP: Color = (0x2C, 0x2C, 0x2E)
BG_BOTTOM: Color = (0x00, 0x00, 0x00)
PAD_COLOR: Color = (0xE4, 0xE4, 0xE8)      # clickpad, grigio chiaro
PAD_RIM: Color = (0xC6, 0xC6, 0xCC)        # bordo interno leggermente più scuro
CENTER_COLOR: Color = (0xB6, 0xB6, 0xBE)   # pulsante centrale
DOT_COLOR: Color = (0x8E, 0x8E, 0x93)      # puntini direzionali
SIGNAL_COLOR: Color = (0x0A, 0x84, 0xFF)   # onde di segnale azzurre

ICON_SIZES = (180, 192, 512)
_SUB = 4  # supersampling 4x4 = 16 campioni per pixel
_SUB_OFFSETS = tuple((i + 0.5) / _SUB for i in range(_SUB))


def _mix(c1: Color, c2: Color, t: float) -> Color:
    """Miscela due colori: t=0 -> c1, t=1 -> c2."""
    if t <= 0.0:
        return c1
    if t >= 1.0:
        return c2
    return (
        c1[0] + (c2[0] - c1[0]) * t,
        c1[1] + (c2[1] - c1[1]) * t,
        c1[2] + (c2[2] - c1[2]) * t,
    )


def _sample(u: float, v: float, size: float) -> Color:
    """Colore nel punto continuo (u, v) (coordinate pixel, 0..size)."""
    # Sfondo: gradiente verticale grafite -> nero.
    color = _mix(BG_TOP, BG_BOTTOM, v / size)

    # Onde di segnale: archi concentrici che si irradiano dall'angolo in alto
    # a destra verso il centro dell'icona (un quarto di cerchio per arco).
    # Disegnate PRIMA del clickpad, che deve restare sempre un cerchio pulito
    # anche dove le due forme si sovrappongono.
    sx, sy = size * 0.86, size * 0.14
    ex, ey = u - sx, v - sy
    if ex <= 0.0 and ey >= 0.0:
        e2 = ex * ex + ey * ey
        stroke = size * 0.026
        for radius in (size * 0.085, size * 0.155, size * 0.225):
            lo, hi = radius - stroke, radius + stroke
            if lo * lo <= e2 <= hi * hi:
                color = SIGNAL_COLOR
                break
        else:
            dot_r = size * 0.024
            if e2 <= dot_r * dot_r:
                color = SIGNAL_COLOR

    # Clickpad circolare, centrato leggermente sotto il centro del quadrato.
    cx, cy = size * 0.5, size * 0.565
    pad_r = size * 0.335
    dx, dy = u - cx, v - cy
    d2 = dx * dx + dy * dy

    if d2 <= pad_r * pad_r:
        color = PAD_COLOR
        rim_r = pad_r * 0.9
        if d2 >= rim_r * rim_r:
            color = PAD_RIM

        center_r = size * 0.125
        if d2 <= center_r * center_r:
            color = CENTER_COLOR
        else:
            dot_r = size * 0.028
            dot_dist = size * 0.225
            for ddx, ddy in ((0.0, -1.0), (0.0, 1.0), (-1.0, 0.0), (1.0, 0.0)):
                px, py = cx + ddx * dot_dist, cy + ddy * dot_dist
                ddx2, ddy2 = u - px, v - py
                if ddx2 * ddx2 + ddy2 * ddy2 <= dot_r * dot_r:
                    color = DOT_COLOR
                    break

    return color


def _render_rows(size: int):
    """Genera, riga per riga, i byte RGB (con filtro PNG "None") dell'icona."""
    for y in range(size):
        row = bytearray(1 + size * 3)  # 1 byte di filtro + 3 byte per pixel
        for x in range(size):
            r_sum = g_sum = b_sum = 0.0
            for oy in _SUB_OFFSETS:
                v = y + oy
                for ox in _SUB_OFFSETS:
                    u = x + ox
                    r, g, b = _sample(u, v, size)
                    r_sum += r
                    g_sum += g
                    b_sum += b
            n = _SUB * _SUB
            i = 1 + x * 3
            row[i] = min(255, max(0, int(r_sum / n + 0.5)))
            row[i + 1] = min(255, max(0, int(g_sum / n + 0.5)))
            row[i + 2] = min(255, max(0, int(b_sum / n + 0.5)))
        yield bytes(row)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def make_icon_png(size: int) -> bytes:
    """Disegna e comprime un'icona quadrata `size`x`size`, RGB 8 bit, senza alpha."""
    raw = b"".join(_render_rows(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # RGB, 8 bit/canale
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def generate_icons(out_dir: Path, sizes=ICON_SIZES) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for size in sizes:
        data = make_icon_png(size)
        path = out_dir / f"icon-{size}.png"
        path.write_bytes(data)
        print(f"  {path} ({len(data)} byte)")


def main() -> int:
    web_dir = Path(__file__).resolve().parent.parent / "web"
    print("Genero le icone TeleMac...")
    generate_icons(web_dir)
    print("Fatto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
