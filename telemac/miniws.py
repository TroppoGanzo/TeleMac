"""Implementazione minima del protocollo WebSocket (RFC 6455), solo libreria standard.

Basta per il nostro caso: un client per volta, messaggi testo/JSON, niente frammentazione
in entrata oltre al minimo necessario. Non è pensata per uso generico.
"""

from __future__ import annotations

import base64
import hashlib
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


class ConnectionClosed(Exception):
    pass


def _read_exact(rfile, n):
    data = rfile.read(n)
    if data is None or len(data) < n:
        raise ConnectionClosed()
    return data


def read_frame(rfile) -> tuple[int, bytes]:
    """Legge un frame e restituisce (opcode, payload). Gestisce solo frame non frammentati."""
    head = _read_exact(rfile, 2)
    b0, b1 = head[0], head[1]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", _read_exact(rfile, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read_exact(rfile, 8))[0]
    mask = _read_exact(rfile, 4) if masked else b"\x00\x00\x00\x00"
    payload = _read_exact(rfile, length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def write_frame(wfile, opcode: int, payload: bytes = b""):
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < 65536:
        header += bytes([126]) + struct.pack(">H", n)
    else:
        header += bytes([127]) + struct.pack(">Q", n)
    wfile.write(header + payload)
    wfile.flush()


OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


def send_text(wfile, text: str):
    write_frame(wfile, OPCODE_TEXT, text.encode("utf-8"))


def iter_text_messages(rfile, wfile):
    """Generatore che restituisce i messaggi testuali in arrivo, rispondendo da solo ai ping."""
    while True:
        opcode, payload = read_frame(rfile)
        if opcode == OPCODE_CLOSE:
            raise ConnectionClosed()
        if opcode == OPCODE_PING:
            write_frame(wfile, OPCODE_PONG, payload)
            continue
        if opcode == OPCODE_PONG:
            continue
        if opcode == OPCODE_TEXT:
            yield payload.decode("utf-8", "replace")
        # I frame binari non sono previsti nel nostro protocollo: li ignoriamo.
