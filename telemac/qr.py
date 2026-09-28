"""Disegna un QR code leggibile nel terminale, usando due moduli per carattere (▀▄)."""

from __future__ import annotations

import qrcodegen


def render_terminal(text: str) -> str:
    qr = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    border = 2
    lo, hi = -border, size + border

    def dark(x, y):
        if x < 0 or y < 0 or x >= size or y >= size:
            return False
        return qr.get_module(x, y)

    lines = []
    for y in range(lo, hi, 2):
        row = []
        for x in range(lo, hi):
            top = dark(x, y)
            bottom = dark(x, y + 1)
            if top and bottom:
                row.append("█")
            elif top:
                row.append("▀")
            elif bottom:
                row.append("▄")
            else:
                row.append(" ")
        lines.append("".join(row))
    return "\n".join(lines)
