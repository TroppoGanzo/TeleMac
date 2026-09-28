#!/usr/bin/env python3
"""TeleMac — server sul Mac. Avvialo con TeleMac.command, inquadra il QR con l'iPhone e via.

Non richiede pip install: usa solo la libreria standard di Python 3.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import socket
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import miniws
import qr
from backends import create_backend
from certs import ensure_certificate
from icon import make_icon_png
from keys import KEYCODES, MEDIA_KEYS, MOUSE_BUTTONS, MODIFIERS

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8765


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # non invia nulla, serve solo a scegliere l'interfaccia
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def make_handler(backend, token, lock):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TeleMac/1.0"

        def log_message(self, fmt, *args):
            pass  # niente rumore nel terminale mentre si guarda la serie

        def _check_token(self, query) -> bool:
            got = query.get("t", [None])[0]
            return got is not None and secrets.compare_digest(got, token)

        def _send_bytes(self, data: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            path = parts.path

            if path == "/icon.png":
                try:
                    size = max(32, min(512, int(query.get("size", ["180"])[0])))
                except ValueError:
                    size = 180
                return self._send_bytes(make_icon_png(size), "image/png")

            if path == "/ws":
                return self._handle_websocket(query)

            if not self._check_token(query) and not self._has_cookie_ok():
                return self._send_bytes(
                    b"Manca il codice di accesso.", "text/plain; charset=utf-8", status=403
                )
            self._set_cookie_ok()
            self._serve_static(path)

        def _has_cookie_ok(self):
            cookie = self.headers.get("Cookie", "")
            return f"telemac_t={token}" in cookie

        def _set_cookie_ok(self):
            self._extra_headers = [("Set-Cookie", f"telemac_t={token}; Path=/; Max-Age=2592000")]

        def _serve_static(self, path):
            if path == "/":
                path = "/index.html"
            rel = path.lstrip("/")
            fpath = (WEB_DIR / rel).resolve()
            if WEB_DIR not in fpath.parents and fpath != WEB_DIR:
                return self.send_error(404)
            if not fpath.is_file():
                return self.send_error(404)
            ctype = mimetypes.guess_type(str(fpath))[0] or "application/octet-stream"
            data = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in getattr(self, "_extra_headers", []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _handle_websocket(self, query):
            if not self._check_token(query) and not self._has_cookie_ok():
                self.send_response(403)
                self.end_headers()
                return
            key = self.headers.get("Sec-WebSocket-Key")
            if not key or self.headers.get("Upgrade", "").lower() != "websocket":
                self.send_response(400)
                self.end_headers()
                return
            accept = miniws.accept_key(key)
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self._run_ws_loop()

        def _run_ws_loop(self):
            try:
                for message in miniws.iter_text_messages(self.rfile, self.wfile):
                    handle_message(backend, lock, message)
            except (miniws.ConnectionClosed, ConnectionError, OSError):
                pass

    return Handler


def handle_message(backend, lock, raw_message: str):
    try:
        msg = json.loads(raw_message)
        kind = msg.get("type")
        with lock:
            if kind == "move":
                backend.move(float(msg["dx"]), float(msg["dy"]))
            elif kind == "scroll":
                backend.scroll(float(msg["dx"]), float(msg["dy"]))
            elif kind == "click":
                button = msg["button"]
                if button in MOUSE_BUTTONS:
                    backend.click(button)
            elif kind == "button":
                button = msg["button"]
                if button in MOUSE_BUTTONS:
                    backend.button(button, bool(msg["down"]))
            elif kind == "key":
                name = msg["name"]
                mods = [m for m in msg.get("mods", []) if m in MODIFIERS]
                if name in KEYCODES:
                    backend.key(name, mods)
            elif kind == "text":
                backend.text(str(msg["text"])[:2000])
            elif kind == "media":
                name = msg["name"]
                if name in MEDIA_KEYS:
                    backend.media(name)
            elif kind == "sleep":
                backend.display_sleep()
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        pass  # messaggio malformato: lo ignoriamo, non blocchiamo il telecomando


def print_banner(url: str):
    print()
    print(qr.render_terminal(url))
    print()
    print("  TeleMac è pronto.")
    print(f"  Inquadra il QR con la fotocamera dell'iPhone, oppure apri:\n  {url}")
    print()
    print("  (Ctrl+C per fermare il server)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Server TeleMac per controllare il Mac dall'iPhone.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dry-run", action="store_true", help="non tocca il mouse/tastiera, stampa solo i comandi")
    parser.add_argument("--no-tls", action="store_true", help="solo per debug in locale, niente https")
    args = parser.parse_args()

    backend = create_backend(dry_run=args.dry_run)
    lock = threading.Lock()
    token = secrets.token_urlsafe(16)

    handler = make_handler(backend, token, lock)
    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    httpd.daemon_threads = True

    scheme = "http"
    if not args.no_tls:
        cert_file, key_file = ensure_certificate()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_file), str(key_file))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"

    ip = local_ip()
    url = f"{scheme}://{ip}:{args.port}/?t={token}"
    print_banner(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCiao!")


if __name__ == "__main__":
    main()
