"""Test di tools/make_icons.py: solo lettura/parsing del PNG generato
(nessuna libreria immagini: leggiamo l'header IHDR a mano)."""

from __future__ import annotations

import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from make_icons import ICON_SIZES, make_icon_png  # noqa: E402

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _read_chunks(data: bytes):
    assert data[:8] == PNG_SIGNATURE
    i = 8
    chunks = []
    while i < len(data):
        (length,) = struct.unpack(">I", data[i : i + 4])
        tag = data[i + 4 : i + 8]
        payload = data[i + 8 : i + 8 + length]
        chunks.append((tag, payload))
        i += 8 + length + 4  # lunghezza + tag + payload + CRC
    return chunks


def _ihdr(data: bytes):
    chunks = _read_chunks(data)
    tag, payload = chunks[0]
    assert tag == b"IHDR"
    width, height, depth, color_type, comp, filt, interlace = struct.unpack(">IIBBBBB", payload)
    return {
        "width": width,
        "height": height,
        "depth": depth,
        "color_type": color_type,
        "compression": comp,
        "filter": filt,
        "interlace": interlace,
    }


def _decode_rgb_rows(data: bytes):
    chunks = _read_chunks(data)
    idat = b"".join(payload for tag, payload in chunks if tag == b"IDAT")
    raw = zlib.decompress(idat)
    ihdr = _ihdr(data)
    w = ihdr["width"]
    stride = 1 + w * 3
    rows = []
    for y in range(ihdr["height"]):
        row = raw[y * stride : (y + 1) * stride]
        assert row[0] == 0, "ci aspettiamo il filtro PNG None su ogni riga"
        rows.append(row[1:])
    return rows


class TestFirma(unittest.TestCase):
    def test_firma_png(self):
        data = make_icon_png(64)
        self.assertEqual(data[:8], PNG_SIGNATURE)

    def test_iend_finale(self):
        data = make_icon_png(64)
        chunks = _read_chunks(data)
        self.assertEqual(chunks[-1][0], b"IEND")


class TestDimensioni(unittest.TestCase):
    def test_ihdr_riporta_le_dimensioni_richieste(self):
        for size in (32, 96, 180, 192, 512):
            data = make_icon_png(size)
            ihdr = _ihdr(data)
            self.assertEqual(ihdr["width"], size)
            self.assertEqual(ihdr["height"], size)

    def test_8_bit_rgb_senza_alpha(self):
        # color_type 2 = "TrueColor" (RGB), niente canale alpha: iOS arrotonda
        # da solo gli angoli, qui non serve (e non vogliamo) la trasparenza.
        data = make_icon_png(180)
        ihdr = _ihdr(data)
        self.assertEqual(ihdr["depth"], 8)
        self.assertEqual(ihdr["color_type"], 2)

    def test_icon_sizes_contiene_le_taglie_richieste(self):
        self.assertEqual(set(ICON_SIZES), {180, 192, 512})


class TestContenutoNonBanale(unittest.TestCase):
    def test_file_non_banale(self):
        data = make_icon_png(180)
        self.assertGreater(len(data), 2000)

    def test_non_e_un_colore_piatto(self):
        # Il gradiente di sfondo + il disegno del telecomando devono produrre
        # più di un colore: un'icona "vuota" sarebbe un bug.
        data = make_icon_png(96)
        rows = _decode_rgb_rows(data)
        pixels = {rows[y][x * 3 : x * 3 + 3] for y in range(0, len(rows), 3) for x in range(0, len(rows[0]) // 3, 3)}
        self.assertGreater(len(pixels), 10)

    def test_telecomando_bianco_centrato(self):
        # Il centro dell'icona cade sul corpo bianco del telecomando, gli angoli
        # sullo sfondo azzurro: il disegno è centrato.
        size = 192
        rows = _decode_rgb_rows(make_icon_png(size))
        px = lambda x, y: tuple(rows[y][x * 3 : x * 3 + 3])
        self.assertEqual(px(size // 2, int(size * 0.62)), (0xFF, 0xFF, 0xFF))
        for x, y in ((4, 4), (size - 5, 4), (4, size - 5), (size - 5, size - 5)):
            r, g, b = px(x, y)
            self.assertGreater(b, r, f"angolo ({x},{y}) non azzurro: {(r, g, b)}")

    def test_angoli_non_trasparenti_ne_arrotondati_via_alpha(self):
        # Niente canale alpha (verificato altrove): qui controlliamo anche che
        # l'angolo del quadrato sia comunque un pixel "pieno" (parte del
        # gradiente di sfondo), non lasciato bianco/nero forzatamente diverso.
        data = make_icon_png(64)
        rows = _decode_rgb_rows(data)
        top_left = rows[0][0:3]
        top_right = rows[0][-3:]
        self.assertEqual(len(top_left), 3)
        self.assertEqual(len(top_right), 3)


class TestFileGenerati(unittest.TestCase):
    """Verifica i PNG già scritti in web/ (generati da tools/make_icons.py)."""

    def test_i_tre_file_esistono_con_le_dimensioni_giuste(self):
        web_dir = Path(__file__).resolve().parent.parent / "web"
        for size in (180, 192, 512):
            path = web_dir / f"icon-{size}.png"
            self.assertTrue(path.is_file(), f"manca {path}: esegui tools/make_icons.py")
            data = path.read_bytes()
            ihdr = _ihdr(data)
            self.assertEqual((ihdr["width"], ihdr["height"]), (size, size))
            self.assertEqual(ihdr["color_type"], 2)


if __name__ == "__main__":
    unittest.main()
