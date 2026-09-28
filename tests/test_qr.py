"""Test di telemac/qr.py: rendering ANSI del QR nel terminale."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

from qr import render_svg, render_terminal  # noqa: E402

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class TestQr(unittest.TestCase):
    def test_e_un_blocco_rettangolare_non_vuoto(self):
        art = render_terminal("https://example.com/?t=abc123")
        lines = art.splitlines()
        self.assertGreater(len(lines), 5)
        visible_lengths = {len(_ANSI_RE.sub("", line)) for line in lines}
        self.assertEqual(len(visible_lengths), 1, "tutte le righe devono avere la stessa larghezza visibile")

    def test_usa_colori_256_neri_e_bianchi_espliciti(self):
        art = render_terminal("ciao")
        self.assertIn("38;5;16", art)  # nero
        self.assertIn("48;5;231", art)  # bianco

    def test_reset_a_fine_riga(self):
        art = render_terminal("ciao")
        for line in art.splitlines():
            self.assertTrue(line.endswith("\x1b[0m"))

    def test_testi_diversi_producono_qr_diversi(self):
        a = render_terminal("https://a.example/")
        b = render_terminal("https://b.example/completamente-diverso")
        self.assertNotEqual(a, b)

    def test_bordo_di_quiet_zone(self):
        import qrcodegen
        qrc = qrcodegen.QrCode.encode_text("ciao", qrcodegen.QrCode.Ecc.MEDIUM)
        size = qrc.get_size()
        art = render_terminal("ciao")
        # ogni riga rappresenta 2 moduli in altezza; con bordo 4 ci aspettiamo
        # più righe di quelle strettamente necessarie per il solo simbolo.
        lines = art.splitlines()
        expected_rows = -(-(size + 2 * 4) // 2)  # ceil
        self.assertEqual(len(lines), expected_rows)


class TestQrSvg(unittest.TestCase):
    def test_svg_con_bordo_e_moduli(self):
        svg = render_svg("http://192.168.1.20:8766/")
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(svg.endswith("</svg>"))
        self.assertIn('fill="#ffffff"', svg)
        self.assertIn("h1v1h-1z", svg)
        # Il primo modulo scuro (angolo del QR) sta dopo il bordo di 4 moduli.
        self.assertIn('d="M4,4h1v1h-1z', svg)

    def test_stesso_testo_stesso_svg(self):
        self.assertEqual(render_svg("ciao"), render_svg("ciao"))
        self.assertNotEqual(render_svg("ciao"), render_svg("ciao!"))


if __name__ == "__main__":
    unittest.main()
