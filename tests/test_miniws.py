"""Test di telemac/miniws.py: framing WebSocket minimale."""

from __future__ import annotations

import io
import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

import miniws  # noqa: E402


class TestAcceptKey(unittest.TestCase):
    def test_esempio_rfc6455(self):
        # Esempio preso pari pari dalla RFC 6455, sezione 1.3.
        self.assertEqual(miniws.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


class TestHandshakeResponse(unittest.TestCase):
    def test_e_http_1_1_101(self):
        resp = miniws.handshake_response("dGhlIHNhbXBsZSBub25jZQ==")
        self.assertTrue(resp.startswith(b"HTTP/1.1 101 Switching Protocols\r\n"))

    def test_contiene_accept_corretto(self):
        resp = miniws.handshake_response("dGhlIHNhbXBsZSBub25jZQ==").decode("ascii")
        self.assertIn("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", resp)
        self.assertIn("Upgrade: websocket", resp)
        self.assertIn("Connection: Upgrade", resp)

    def test_termina_con_riga_vuota(self):
        resp = miniws.handshake_response("dGhlIHNhbXBsZSBub25jZQ==")
        self.assertTrue(resp.endswith(b"\r\n\r\n"))


def _frame_bytes(opcode, payload, fin=True, masked=False):
    b0 = (0x80 if fin else 0) | opcode
    header = bytes([b0])
    n = len(payload)
    mask_bit = 0x80 if masked else 0
    if n < 126:
        header += bytes([mask_bit | n])
    elif n < 65536:
        header += bytes([mask_bit | 126]) + struct.pack(">H", n)
    else:
        header += bytes([mask_bit | 127]) + struct.pack(">Q", n)
    if masked:
        mask = b"\x01\x02\x03\x04"
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header += mask
    return header + payload


class TestReadWriteFrame(unittest.TestCase):
    def test_round_trip_non_mascherato(self):
        wfile = io.BytesIO()
        miniws.write_frame(wfile, miniws.OPCODE_TEXT, b"ciao")
        wfile.seek(0)
        fin, opcode, payload = miniws.read_frame(wfile)
        self.assertTrue(fin)
        self.assertEqual(opcode, miniws.OPCODE_TEXT)
        self.assertEqual(payload, b"ciao")

    def test_legge_frame_mascherato(self):
        rfile = io.BytesIO(_frame_bytes(miniws.OPCODE_TEXT, b"segreto", masked=True))
        fin, opcode, payload = miniws.read_frame(rfile)
        self.assertTrue(fin)
        self.assertEqual(payload, b"segreto")

    def test_lunghezza_estesa_16_bit(self):
        payload = b"x" * 200
        rfile = io.BytesIO(_frame_bytes(miniws.OPCODE_BINARY, payload))
        fin, opcode, got = miniws.read_frame(rfile)
        self.assertEqual(got, payload)

    def test_payload_oltre_il_limite_chiude(self):
        # dichiariamo una lunghezza (nel frame) superiore a MAX_PAYLOAD
        header = bytes([0x80 | miniws.OPCODE_BINARY, 127]) + struct.pack(">Q", miniws.MAX_PAYLOAD + 1)
        rfile = io.BytesIO(header)
        with self.assertRaises(miniws.ConnectionClosed):
            miniws.read_frame(rfile)

    def test_connessione_troncata_solleva_connection_closed(self):
        rfile = io.BytesIO(b"\x81")  # un solo byte: manca tutto il resto
        with self.assertRaises(miniws.ConnectionClosed):
            miniws.read_frame(rfile)


class TestReadMessage(unittest.TestCase):
    def test_messaggio_singolo(self):
        data = _frame_bytes(miniws.OPCODE_TEXT, b"ciao")
        rfile = io.BytesIO(data)
        wfile = io.BytesIO()
        opcode, payload = miniws.read_message(rfile, wfile)
        self.assertEqual(opcode, miniws.OPCODE_TEXT)
        self.assertEqual(payload, b"ciao")

    def test_frame_di_continuazione_vengono_riassemblati(self):
        data = (
            _frame_bytes(miniws.OPCODE_TEXT, b"cia", fin=False)
            + _frame_bytes(miniws.OPCODE_CONTINUATION, b"o mon", fin=False)
            + _frame_bytes(miniws.OPCODE_CONTINUATION, b"do", fin=True)
        )
        rfile = io.BytesIO(data)
        wfile = io.BytesIO()
        opcode, payload = miniws.read_message(rfile, wfile)
        self.assertEqual(opcode, miniws.OPCODE_TEXT)
        self.assertEqual(payload, b"ciao mondo")

    def test_continuazione_accumulata_oltre_il_limite_chiude(self):
        big_chunk = b"x" * 1000
        n_chunks = miniws.MAX_PAYLOAD // len(big_chunk) + 2
        frames = _frame_bytes(miniws.OPCODE_TEXT, big_chunk, fin=False)
        for _ in range(n_chunks - 1):
            frames += _frame_bytes(miniws.OPCODE_CONTINUATION, big_chunk, fin=False)
        rfile = io.BytesIO(frames)
        wfile = io.BytesIO()
        with self.assertRaises(miniws.ConnectionClosed):
            miniws.read_message(rfile, wfile)

    def test_ping_riceve_pong_automaticamente_e_non_e_un_messaggio(self):
        data = _frame_bytes(miniws.OPCODE_PING, b"abc") + _frame_bytes(miniws.OPCODE_TEXT, b"ciao")
        rfile = io.BytesIO(data)
        wfile = io.BytesIO()
        opcode, payload = miniws.read_message(rfile, wfile)
        self.assertEqual((opcode, payload), (miniws.OPCODE_TEXT, b"ciao"))
        wfile.seek(0)
        fin, pong_opcode, pong_payload = miniws.read_frame(wfile)
        self.assertEqual(pong_opcode, miniws.OPCODE_PONG)
        self.assertEqual(pong_payload, b"abc")

    def test_close_solleva_connection_closed(self):
        rfile = io.BytesIO(_frame_bytes(miniws.OPCODE_CLOSE, b""))
        wfile = io.BytesIO()
        with self.assertRaises(miniws.ConnectionClosed):
            miniws.read_message(rfile, wfile)

    def test_continuazione_senza_inizio_e_un_errore(self):
        rfile = io.BytesIO(_frame_bytes(miniws.OPCODE_CONTINUATION, b"boh", fin=True))
        wfile = io.BytesIO()
        with self.assertRaises(miniws.ConnectionClosed):
            miniws.read_message(rfile, wfile)


class TestSendHelpers(unittest.TestCase):
    def test_send_json(self):
        wfile = io.BytesIO()
        miniws.send_json(wfile, {"type": "pong"})
        wfile.seek(0)
        fin, opcode, payload = miniws.read_frame(wfile)
        self.assertEqual(opcode, miniws.OPCODE_TEXT)
        self.assertEqual(json.loads(payload.decode("utf-8")), {"type": "pong"})

    def test_send_close_include_codice(self):
        wfile = io.BytesIO()
        miniws.send_close(wfile, 4001, "auth")
        wfile.seek(0)
        fin, opcode, payload = miniws.read_frame(wfile)
        self.assertEqual(opcode, miniws.OPCODE_CLOSE)
        code = struct.unpack(">H", payload[:2])[0]
        self.assertEqual(code, 4001)
        self.assertEqual(payload[2:], b"auth")

    def test_send_close_non_solleva_su_socket_chiuso(self):
        class Boom:
            def write(self, data):
                raise OSError("chiuso")

            def flush(self):
                pass

        miniws.send_close(Boom(), 1000)  # non deve sollevare


if __name__ == "__main__":
    unittest.main()
