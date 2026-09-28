"""Pannello della finestra di TeleMac sul Mac.

L'app per Mac (TeleMac.app) mostra questa pagina nella sua finestra: QR da
inquadrare, codice di abbinamento, stato dei permessi e dei dispositivi.

Il server del pannello ascolta SOLO su 127.0.0.1 e ogni richiesta deve portare
il gettone segreto che l'app genera a ogni avvio (TELEMAC_PANEL_TOKEN): gli
altri programmi del Mac, e tanto meno la rete, non possono leggere il codice di
abbinamento né scollegare gli iPhone. Controlliamo anche l'header Host, contro
i trucchi di "DNS rebinding" da una pagina web aperta nel browser.
"""

from __future__ import annotations

import json
import math
import secrets
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

import qr

TOKEN_HEADER = "X-TeleMac-Token"
PAIRED_BANNER_SECONDS = 15
STATIC_FILES = {"/icon-192.png": "image/png", "/icon-512.png": "image/png"}


class QrCache:
    """Il QR cambia solo se cambia l'indirizzo: inutile ridisegnarlo a ogni richiesta."""

    def __init__(self):
        self._text: Optional[str] = None
        self._svg = ""

    def svg(self, text: str) -> str:
        if text != self._text:
            self._svg = qr.render_svg(text)
            self._text = text
        return self._svg


def build_panel_status(
    *, shared, device_store, computer_name: str, hostname: str, ip: Optional[str],
    app_port: int, setup_port: Optional[int], ca_fp: str, profile_path: Path,
    qr_cache: QrCache, dry_run: bool = False, clock: Callable[[], float] = time.monotonic,
) -> dict:
    now = clock()

    pairing = None
    code = shared.pairing_code
    if code and now < shared.pairing_expires:
        pairing = {"code": code, "expiresIn": max(0, int(math.ceil(shared.pairing_expires - now)))}

    paired = None
    last = shared.last_paired
    if last and now - last[1] < PAIRED_BANNER_SECONDS:
        paired = {"device": last[0]}

    setup_url = f"http://{ip or hostname}:{setup_port}/" if setup_port else None
    app_urls = [f"https://{hostname}:{app_port}/"]
    if ip:
        app_urls.append(f"https://{ip}:{app_port}/")

    return {
        "ok": True,
        "computerName": computer_name,
        "hostname": hostname,
        "ip": ip,
        "setupUrl": setup_url,
        "qrSvg": qr_cache.svg(setup_url) if setup_url else None,
        "appUrls": app_urls,
        "caFingerprint": ca_fp,
        "profilePath": str(profile_path),
        "accessibility": shared.accessibility,
        "pairing": pairing,
        "paired": paired,
        "devices": device_store.devices(),
        "dryRun": dry_run,
    }


def make_panel_handler(*, token: str, status_provider: Callable[[], dict], device_store, web_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TeleMac/2.0"
        timeout = 30

        def log_message(self, fmt, *args):
            pass

        def _send_bytes(self, data: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

        def _send_json(self, obj, status: int = 200):
            self._send_bytes(json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", status)

        def _deny(self, status: int = 403):
            self.close_connection = True
            self._send_bytes(b"Accesso negato.", "text/plain; charset=utf-8", status)

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _token_ok(self, candidate: Optional[str]) -> bool:
            return isinstance(candidate, str) and secrets.compare_digest(
                candidate.encode("utf-8"), token.encode("utf-8"),
            )

        def do_GET(self):
            if not self._host_ok():
                return self._deny()
            parts = urlsplit(self.path)
            if parts.path == "/":
                if not self._token_ok(parse_qs(parts.query).get("token", [None])[0]):
                    return self._deny()
                return self._serve_page()
            if parts.path in STATIC_FILES:
                try:
                    data = (web_dir / parts.path.lstrip("/")).read_bytes()
                except OSError:
                    return self._deny(404)
                return self._send_bytes(data, STATIC_FILES[parts.path])
            if not self._token_ok(self.headers.get(TOKEN_HEADER)):
                return self._deny()
            if parts.path == "/api/status":
                return self._send_json(status_provider())
            self._deny(404)

        def do_POST(self):
            if not self._host_ok() or not self._token_ok(self.headers.get(TOKEN_HEADER)):
                return self._deny()
            try:
                length = max(0, min(int(self.headers.get("Content-Length", "0")), 65536))
            except ValueError:
                length = 0
            if length:
                self.rfile.read(length)
            if urlsplit(self.path).path == "/api/forget-devices":
                n = device_store.count()
                device_store.clear()
                return self._send_json({"ok": True, "removed": n})
            self._deny(404)

        def _serve_page(self):
            try:
                html = (web_dir / "panel.html").read_bytes()
            except OSError:
                return self._send_bytes(b"web/panel.html non trovato.", "text/plain; charset=utf-8", 404)
            self._send_bytes(html, "text/html; charset=utf-8")

    return Handler
