"""Disegna il QR code: nel terminale, due moduli per riga di testo (▀,
colorato) con colori ANSI espliciti (moduli neri su sfondo bianco sempre, anche
con terminale a tema scuro), oppure come SVG per la finestra dell'app per Mac."""

from __future__ import annotations

import qrcodegen

# Colori a 256 tinte: 16 = nero, 231 = quasi-bianco. Espliciti apposta, non ci
# affidiamo alla palette del terminale dell'utente.
_BLACK = "16"
_WHITE = "231"
_RESET = "\x1b[0m"
BORDER = 4  # quiet zone consigliata per i QR: 4 moduli


def _sgr(fg: str, bg: str) -> str:
    return f"\x1b[38;5;{fg};48;5;{bg}m"


def render_terminal(text: str) -> str:
    qr = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    lo, hi = -BORDER, size + BORDER

    def dark(x, y):
        if x < 0 or y < 0 or x >= size or y >= size:
            return False
        return qr.get_module(x, y)

    lines = []
    for y in range(lo, hi, 2):
        row = []
        for x in range(lo, hi):
            top = _BLACK if dark(x, y) else _WHITE
            bottom = _BLACK if dark(x, y + 1) else _WHITE
            row.append(_sgr(top, bottom) + "▀")
        row.append(_RESET)
        lines.append("".join(row))
    return "\n".join(lines)


def render_svg(text: str, dark: str = "#03111a", light: str = "#ffffff", border: int = BORDER) -> str:
    """Lo stesso QR come immagine SVG (un solo <path>), per la finestra dell'app."""
    qr = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    full = size + 2 * border
    parts = []
    for y in range(size):
        for x in range(size):
            if qr.get_module(x, y):
                parts.append(f"M{x + border},{y + border}h1v1h-1z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full} {full}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR code">'
        f'<rect width="{full}" height="{full}" fill="{light}"/>'
        f'<path d="{"".join(parts)}" fill="{dark}"/></svg>'
    )
