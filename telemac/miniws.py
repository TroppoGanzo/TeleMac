"""Implementazione minima del protocollo WebSocket (RFC 6455), solo libreria standard.

Basta per il nostro caso: un client per volta, messaggi testo/JSON, con
gestione minima della frammentazione in entrata (frame di continuazione) e un
limite di dimensione per messaggio. Non è pensata per uso generico.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Oltre questa dimensione (per messaggio, anche riassemblato da più frame di
# continuazione) chiudiamo la connessione: ai nostri comandi bastano poche
# decine di byte, non serve di più.
MAX_PAYLOAD = 64 * 1024

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_response(key: str) -> bytes:
    """Risposta di upgrade completa. Deve essere HTTP/1.1 (non 1.0): Safari su
    iOS rifiuta l'handshake se la versione HTTP non è quella giusta."""
    accept = accept_key(key)
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Accept: {accept}",
        "",
        "",
    ]
    return "\r\n".join(lines).encode("ascii")


class ConnectionClosed(Exception):
    pass


def _read_exact(rfile, n):
    data = rfile.read(n)
    if data is None or len(data) < n:
        raise ConnectionClosed()
    return data


def read_frame(rfile):
    """Legge un singolo frame sul filo: restituisce (fin, opcode, payload)."""
    head = _read_exact(rfile, 2)
    b0, b1 = head[0], head[1]
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", _read_exact(rfile, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read_exact(rfile, 8))[0]
    if length > MAX_PAYLOAD:
        raise ConnectionClosed()
    mask = _read_exact(rfile, 4) if masked else b"\x00\x00\x00\x00"
    payload = _read_exact(rfile, length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


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


def send_text(wfile, text: str):
    write_frame(wfile, OPCODE_TEXT, text.encode("utf-8"))


def send_json(wfile, obj) -> None:
    send_text(wfile, json.dumps(obj))


def send_close(wfile, code: int, reason: str = "") -> None:
    payload = struct.pack(">H", code) + reason.encode("utf-8")
    try:
        write_frame(wfile, OPCODE_CLOSE, payload)
    except OSError:
        pass  # il client potrebbe aver già chiuso dal suo lato


def read_message(rfile, wfile):
    """Legge un messaggio logico completo, riassemblando i frame di
    continuazione e rispondendo da solo ai ping. Restituisce (opcode, payload)
    oppure solleva ConnectionClosed."""
    opcode = None
    buffer = bytearray()
    while True:
        fin, frame_opcode, payload = read_frame(rfile)
        if frame_opcode == OPCODE_CLOSE:
            raise ConnectionClosed()
        if frame_opcode == OPCODE_PING:
            write_frame(wfile, OPCODE_PONG, payload)
            continue
        if frame_opcode == OPCODE_PONG:
            continue
        if frame_opcode == OPCODE_CONTINUATION:
            if opcode is None:
                raise ConnectionClosed()  # continuazione senza un frame iniziale: protocollo violato
        else:
            opcode = frame_opcode
        buffer += payload
        if len(buffer) > MAX_PAYLOAD:
            raise ConnectionClosed()
        if fin:
            return opcode, bytes(buffer)


def iter_text_messages(rfile, wfile):
    """Generatore che restituisce i messaggi testuali in arrivo (anche
    frammentati su più frame)."""
    while True:
        opcode, payload = read_message(rfile, wfile)
        if opcode == OPCODE_TEXT:
            yield payload.decode("utf-8", "replace")
        # I frame binari non sono previsti nel nostro protocollo: li ignoriamo.
