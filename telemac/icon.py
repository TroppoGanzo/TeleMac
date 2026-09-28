"""Genera al volo una semplice icona PNG (monogramma 'M' su sfondo colorato),
usando solo la libreria standard (zlib per il PNG), per l'icona della schermata Home."""

from __future__ import annotations

import struct
import zlib

BG = (0x1E, 0x2A, 0x38)  # blu-grigio scuro
FG = (0xE8, 0xF0, 0xFF)  # quasi bianco

# Disegno della "M" su una griglia 7x7, poi ingrandita.
_M = [
    "1000001",
    "1100011",
    "1010101",
    "1010101",
    "1000001",
    "1000001",
    "1000001",
]


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def make_icon_png(size: int = 180) -> bytes:
    grid = len(_M)
    cell = size // (grid + 2)  # un po' di margine
    offset = (size - cell * grid) // 2

    rows = []
    for y in range(size):
        row = bytearray()
        gy = (y - offset) // cell if cell else -1
        for x in range(size):
            gx = (x - offset) // cell if cell else -1
            on = 0 <= gy < grid and 0 <= gx < grid and _M[gy][gx] == "1"
            r, g, b = FG if on else BG
            row += bytes((r, g, b))
        rows.append(b"\x00" + bytes(row))  # filtro "None" a inizio riga

    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # RGB, 8 bit
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )
