#!/usr/bin/env python3
"""Genera le icone di TeleMac: i PNG per l'iPhone (180/192/512 px) e
l'icona dell'app per Mac (macos/AppIcon.icns).

Piccolo motore di disegno vettoriale scritto a mano, con antialiasing per
supersampling e compressione PNG via `zlib`: nessuna dipendenza esterna, solo
libreria standard.

Disegno: sfondo blu ciano con un gradiente morbido in diagonale e, al centro,
la sagoma bianca di un telecomando stile Siri Remote (clickpad circolare in
alto e due tasti tondi sotto), con un'ombra leggera sfumata.

iOS arrotonda da solo gli angoli delle icone della schermata Home: lì NON
arrotondiamo nulla e non usiamo trasparenza (PNG RGB pieno). macOS invece
vuole l'icona già "a mattonella": quadrato arrotondato con margine
trasparente e ombra, come le icone di sistema (PNG RGBA dentro l'.icns).
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import Tuple

Color = Tuple[int, int, int]

# --- Palette ---------------------------------------------------------------
# Tutto deriva dal blu ciano di TeleMac (#1AB8EF), lo stesso delle app.
BG_A: Color = (0x5F, 0xDD, 0xFF)       # ciano chiaro (in alto a sinistra)
BG_B: Color = (0x0A, 0x7C, 0xC9)       # blu ciano profondo (in basso a destra)
SHADOW: Color = (0x05, 0x3F, 0x6B)     # ombra del telecomando
BODY: Color = (0xFF, 0xFF, 0xFF)       # corpo del telecomando
PAD: Color = (0xD4, 0xF1, 0xFB)        # clickpad e tasti, ciano tenue
PAD_CENTER: Color = (0xF2, 0xFB, 0xFF) # pulsante centrale del clickpad

ICON_SIZES = (180, 192, 512)
_SUB = 4  # supersampling 4x4 = 16 campioni per pixel
_SUB_OFFSETS = tuple((i + 0.5) / _SUB for i in range(_SUB))

# Icona per Mac: la "mattonella" occupa 824 px su 1024 (griglia Apple), con
# angoli arrotondati e un'ombra morbida sotto.
MAC_TILE = 824 / 1024
MAC_RADIUS = 185 / 1024
MAC_SHADOW_ALPHA = 0.32
# Tipi di blocco dell'.icns (PNG dentro) e lato in pixel di ciascuno.
ICNS_TYPES = (
    (b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256),
    (b"ic09", 512), (b"ic10", 1024), (b"ic11", 32), (b"ic12", 64), (b"ic13", 256),
    (b"ic14", 512),
)


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


def _rounded_rect_dist(u: float, v: float, cx: float, cy: float, hw: float, hh: float, r: float) -> float:
    """Distanza con segno da un rettangolo arrotondato (negativa dentro)."""
    qx = abs(u - cx) - (hw - r)
    qy = abs(v - cy) - (hh - r)
    ox, oy = max(qx, 0.0), max(qy, 0.0)
    return (ox * ox + oy * oy) ** 0.5 + min(max(qx, qy), 0.0) - r


def _in_circle(u: float, v: float, cx: float, cy: float, r: float) -> bool:
    dx, dy = u - cx, v - cy
    return dx * dx + dy * dy <= r * r


def _sample(u: float, v: float, size: float) -> Color:
    """Colore nel punto continuo (u, v) (coordinate pixel, 0..size)."""
    # Sfondo: gradiente diagonale morbido.
    color = _mix(BG_A, BG_B, (u + v) / (2 * size))

    # Telecomando: rettangolo verticale molto arrotondato, centrato.
    cx, cy = size * 0.5, size * 0.5
    hw, hh, r = size * 0.175, size * 0.33, size * 0.175

    # Ombra: stessa forma spostata in basso, sfumata sulla distanza.
    shadow_d = _rounded_rect_dist(u, v, cx, cy + size * 0.03, hw, hh, r)
    blur = size * 0.06
    if shadow_d < blur:
        strength = 0.35 * min(1.0, (blur - shadow_d) / blur) ** 2
        color = _mix(color, SHADOW, strength)

    if _rounded_rect_dist(u, v, cx, cy, hw, hh, r) > 0.0:
        return color

    color = BODY
    top = cy - hh
    pad_cy = top + size * 0.175
    if _in_circle(u, v, cx, pad_cy, size * 0.128):
        color = PAD
        if _in_circle(u, v, cx, pad_cy, size * 0.05):
            color = PAD_CENTER
        return color
    for bx in (cx - size * 0.068, cx + size * 0.068):
        if _in_circle(u, v, bx, top + size * 0.39, size * 0.042):
            return PAD
    if _in_circle(u, v, cx, top + size * 0.51, size * 0.042):
        return PAD
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


def _sample_mac(u: float, v: float, size: float) -> Tuple[float, float, float, float]:
    """Colore RGBA nel punto (u, v) dell'icona per Mac di lato `size`."""
    half = size * MAC_TILE / 2
    radius = size * MAC_RADIUS
    c = size / 2
    d = _rounded_rect_dist(u, v, c, c, half, half, radius)
    if d <= 0.0:
        # Dentro la mattonella: lo stesso disegno dell'icona per iPhone.
        tile = 2 * half
        r, g, b = _sample(u - (c - half), v - (c - half), tile)
        return r, g, b, 1.0
    # Fuori: solo l'ombra, sfumata e spostata un po' in basso.
    blur = size * 0.035
    sd = _rounded_rect_dist(u, v, c, c + size * 0.012, half, half, radius)
    if sd < blur:
        t = 1.0 - max(0.0, sd) / blur
        return 0.0, 0.0, 0.0, MAC_SHADOW_ALPHA * t * t
    return 0.0, 0.0, 0.0, 0.0


def make_mac_icon_png(size: int) -> bytes:
    """Icona per Mac `size`x`size`, RGBA 8 bit (angoli trasparenti)."""
    sub = 4 if size <= 256 else 2  # le taglie grandi sono già lisce con meno campioni
    offsets = tuple((i + 0.5) / sub for i in range(sub))
    n = sub * sub
    rows = []
    for y in range(size):
        row = bytearray(1 + size * 4)
        for x in range(size):
            r_sum = g_sum = b_sum = a_sum = 0.0
            for oy in offsets:
                for ox in offsets:
                    r, g, b, a = _sample_mac(x + ox, y + oy, size)
                    r_sum += r * a
                    g_sum += g * a
                    b_sum += b * a
                    a_sum += a
            i = 1 + x * 4
            if a_sum > 0:
                row[i] = min(255, int(r_sum / a_sum + 0.5))
                row[i + 1] = min(255, int(g_sum / a_sum + 0.5))
                row[i + 2] = min(255, int(b_sum / a_sum + 0.5))
            row[i + 3] = min(255, int(a_sum / n * 255 + 0.5))
        rows.append(bytes(row))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # RGBA, 8 bit/canale
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + _png_chunk(b"IEND", b"")
    )


def make_icns(pngs: dict) -> bytes:
    """Impacchetta i PNG (lato -> dati) in un file .icns."""
    blocks = b""
    for kind, side in ICNS_TYPES:
        data = pngs[side]
        blocks += kind + struct.pack(">I", 8 + len(data)) + data
    return b"icns" + struct.pack(">I", 8 + len(blocks)) + blocks


def generate_mac_icon(path: Path) -> None:
    sides = sorted({side for _, side in ICNS_TYPES})
    pngs = {side: make_mac_icon_png(side) for side in sides}
    path.parent.mkdir(parents=True, exist_ok=True)
    data = make_icns(pngs)
    path.write_bytes(data)
    print(f"  {path} ({len(data)} byte)")


def generate_icons(out_dir: Path, sizes=ICON_SIZES) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for size in sizes:
        data = make_icon_png(size)
        path = out_dir / f"icon-{size}.png"
        path.write_bytes(data)
        print(f"  {path} ({len(data)} byte)")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    print("Genero le icone TeleMac...")
    generate_icons(root / "web")
    generate_mac_icon(root / "macos" / "AppIcon.icns")
    print("Fatto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
