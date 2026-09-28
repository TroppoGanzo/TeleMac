#!/usr/bin/env python3
"""TeleMac — server sul Mac.

Di solito lo avvia l'app TeleMac.app (con --app): la finestra dell'app mostra
il QR e il codice di abbinamento. Per sviluppo si può lanciare anche a mano,
`python3 telemac/server.py`, e allora QR e messaggi escono nel terminale.

Non richiede pip install: usa solo la libreria standard di Python 3.
"""

from __future__ import annotations

import argparse
import errno
import json
import math
import mimetypes
import os
import secrets
import signal
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import certs
import miniws
import netinfo
import panel
import qr
from backends import accessibility_status, create_backend
from keys import KEYCODES, MEDIA_KEYS, MODIFIERS, MOUSE_BUTTONS
from pairing import DeviceStore, PairingManager

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_APP_PORT = 8765
DEFAULT_SETUP_PORT = 8766

MOVE_LIMIT = 500.0
SCROLL_LIMIT = 2000.0
TEXT_LIMIT = 2000
CURSOR_SCALE_MAX = 4.0

# Segnaposto nell'insieme `pressed` di una connessione: questa connessione ha
# ingrandito il cursore, quindi quando si chiude va rimesso com'era.
CURSOR_MARK = "cursor-scaled"
PAIR_FINISH_BODY_LIMIT = 4096

WS_IDLE_TIMEOUT = 30  # secondi senza dati: il client manda un ping ogni 10s
TLS_HANDSHAKE_TIMEOUT = 10

ACCESSIBILITY_INSTRUCTIONS = (
    "\n"
    "  TeleMac ha bisogno del permesso di Accessibilità per muovere mouse e tastiera.\n"
    "  Vai su: Impostazioni di Sistema → Privacy e sicurezza → Accessibilità\n"
    "  e attiva l'interruttore per l'app che ha avviato il server (Terminale o TeleMac).\n"
)

# Righe per l'app per Mac: su stdout, una per evento, "@telemac {json}".
EVENT_PREFIX = "@telemac "
EXIT_PORT_IN_USE = 3


def state_dir() -> Path:
    return Path(os.environ.get("TELEMAC_HOME", str(Path.home() / ".telemac")))


def web_directory() -> Path:
    return Path(os.environ.get("TELEMAC_WEB_DIR", str(REPO_ROOT / "web")))


class SharedState:
    """Piccolo stato condiviso fra le richieste (una sola scrittura alla volta,
    dal thread principale o dal watcher dell'Accessibilità).

    `emit`, se c'è, riceve gli eventi da passare all'app per Mac (codice di
    abbinamento comparso, iPhone abbinato...)."""

    def __init__(self, emit: Optional[Callable[[dict], None]] = None, clock: Callable[[], float] = time.monotonic):
        self.accessibility: Optional[bool] = None
        self.pairing_code: Optional[str] = None
        self.pairing_expires = 0.0
        self.last_paired = None  # (nome del dispositivo, istante)
        self.emit = emit
        self._clock = clock

    def notify(self, event: dict) -> None:
        if self.emit is None:
            return
        try:
            self.emit(event)
        except Exception:
            pass

    def show_code(self, code: str, seconds_left: float) -> None:
        self.pairing_code = code
        self.pairing_expires = self._clock() + seconds_left
        self.notify({"event": "pair_code"})

    def hide_code(self) -> None:
        if self.pairing_code is None:
            return
        self.pairing_code = None
        self.pairing_expires = 0.0
        self.notify({"event": "pair_done"})

    def paired(self, device_name: str) -> None:
        self.last_paired = (device_name, self._clock())
        self.notify({"event": "paired", "device": device_name})


# --------------------------------------------------------------------------
# Validazione e applicazione dei messaggi WebSocket (funzione pura, testabile)
# --------------------------------------------------------------------------

def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _finite_float(value) -> float:
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("numero non finito")
    return f


def handle_message(backend, lock, raw: str, pressed: set) -> Optional[dict]:
    """Applica un messaggio testuale ricevuto dal client al backend.

    Non solleva mai eccezioni verso l'esterno: un messaggio malformato viene
    semplicemente ignorato. Restituisce l'eventuale risposta da rimandare al
    client (es. {"type": "pong"}), oppure None.
    """
    try:
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            return None
        kind = msg.get("type")

        if kind == "move":
            dx = _clamp(_finite_float(msg["dx"]), MOVE_LIMIT)
            dy = _clamp(_finite_float(msg["dy"]), MOVE_LIMIT)
            with lock:
                backend.move(dx, dy)
        elif kind == "scroll":
            dx = _clamp(_finite_float(msg["dx"]), SCROLL_LIMIT)
            dy = _clamp(_finite_float(msg["dy"]), SCROLL_LIMIT)
            with lock:
                backend.scroll(dx, dy)
        elif kind == "click":
            button = msg["button"]
            if button in MOUSE_BUTTONS:
                with lock:
                    backend.click(button)
        elif kind == "button":
            button = msg["button"]
            down = bool(msg["down"])
            if button in MOUSE_BUTTONS:
                with lock:
                    backend.button(button, down)
                if down:
                    pressed.add(button)
                else:
                    pressed.discard(button)
        elif kind == "key":
            name = msg["name"]
            mods_in = msg.get("mods", [])
            mods = [m for m in mods_in if m in MODIFIERS] if isinstance(mods_in, list) else []
            if name in KEYCODES:
                with lock:
                    backend.key(name, mods)
        elif kind == "text":
            text = msg["text"]
            if isinstance(text, str):
                with lock:
                    backend.text(text[:TEXT_LIMIT])
        elif kind == "media":
            name = msg["name"]
            if name in MEDIA_KEYS:
                with lock:
                    backend.media(name)
        elif kind == "sleep":
            with lock:
                backend.display_sleep()
        elif kind == "cursor":
            scale = min(CURSOR_SCALE_MAX, max(1.0, _finite_float(msg["scale"])))
            with lock:
                ok = bool(backend.cursor_scale(scale)) if hasattr(backend, "cursor_scale") else False
            if scale > 1:
                pressed.add(CURSOR_MARK)
            else:
                pressed.discard(CURSOR_MARK)
            return {"type": "cursor", "ok": ok}
        elif kind == "ping":
            return {"type": "pong"}
        return None
    except Exception:
        return None  # messaggio malformato: lo ignoriamo, non blocchiamo il telecomando


def release_pressed(backend, lock, pressed: set) -> None:
    """Quando una connessione finisce: rilascia i tasti del mouse ancora premuti
    e, se l'aveva ingrandito, rimette il cursore com'era."""
    with lock:
        for button in list(pressed):
            try:
                if button == CURSOR_MARK:
                    if hasattr(backend, "cursor_restore"):
                        backend.cursor_restore()
                else:
                    backend.button(button, False)
            except Exception:
                pass
    pressed.clear()


# --------------------------------------------------------------------------
# Server HTTPS ("app"): file statici, /api/ping, /api/pair/*, /ws
# --------------------------------------------------------------------------

def make_app_handler(
    *, backend, lock, pairing: PairingManager, device_store: DeviceStore,
    shared: SharedState, computer_name: str, web_dir: Path,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TeleMac/2.0"
        timeout = WS_IDLE_TIMEOUT

        def log_message(self, fmt, *args):
            pass  # niente rumore nel terminale

        # ---- utilità di risposta ----

        def _send_bytes(self, data: bytes, content_type: str, status: int = 200, extra_headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (extra_headers or []):
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

        def _send_json(self, obj, status: int = 200, extra_headers=None):
            self._send_bytes(
                json.dumps(obj).encode("utf-8"),
                "application/json; charset=utf-8",
                status=status,
                extra_headers=extra_headers,
            )

        def _send_404(self):
            self.close_connection = True
            self._send_bytes(b"Non trovato.", "text/plain; charset=utf-8", status=404)

        # ---- GET ----

        def do_GET(self):
            parts = urlsplit(self.path)
            path = parts.path
            if path == "/api/ping":
                return self._handle_ping()
            if path == "/ws":
                return self._handle_ws(parse_qs(parts.query))
            if path in ("/setup.html", "/panel.html"):
                return self._send_404()
            self._serve_static(path)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == "/api/pair/start":
                return self._handle_pair_start()
            if path == "/api/pair/finish":
                return self._handle_pair_finish()
            self._send_404()

        def _handle_ping(self):
            body = {
                "ok": True,
                "name": computer_name,
                "accessibility": shared.accessibility,
                "version": 2,
            }
            self._send_json(body, extra_headers=[("Access-Control-Allow-Origin", "*")])

        def _read_up_to(self, limit: int) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            length = max(0, min(length, limit))
            return self.rfile.read(length) if length else b""

        def _handle_pair_start(self):
            self._read_up_to(65536)  # corpo ignorato, ma va comunque svuotato dal socket
            ok, info = pairing.start()
            if ok:
                self._send_json({"ok": True, "expires_in": info["expires_in"]})
            else:
                self._send_json({"ok": False, "error": info["error"]}, status=429)

        def _handle_pair_finish(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0:
                self.close_connection = True
                return self._send_json({"ok": False, "error": "bad_request"}, status=400)
            # Leggiamo comunque il corpo (fino a un tetto ragionevole) anche se
            # supera il limite: altrimenti il client resta a metà dell'invio
            # quando chiudiamo la connessione, e la risposta non gli arriva mai.
            raw = self.rfile.read(min(length, 1_000_000)) if length else b""
            if length > PAIR_FINISH_BODY_LIMIT:
                self.close_connection = True
                return self._send_json({"ok": False, "error": "bad_request"}, status=400)
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("corpo non valido")
                code = payload["code"]
                if not isinstance(code, str):
                    raise ValueError("codice non valido")
                device_name = payload.get("device", "iPhone")
                if not isinstance(device_name, str) or not device_name.strip():
                    device_name = "iPhone"
                device_name = device_name.strip()[:100]
            except Exception:
                return self._send_json({"ok": False, "error": "bad_request"}, status=400)

            ok, error, attempts_left = pairing.finish(code)
            if ok:
                token = device_store.add(device_name)
                shared.paired(device_name)
                return self._send_json({"ok": True, "token": token, "name": computer_name})
            body = {"ok": False, "error": error}
            if attempts_left is not None:
                body["attempts_left"] = attempts_left
            self._send_json(body, status=403)

        def _serve_static(self, path: str):
            if path == "/":
                path = "/index.html"
            base = web_dir.resolve()
            candidate = (base / path.lstrip("/")).resolve()
            try:
                candidate.relative_to(base)
            except ValueError:
                return self._send_404()  # tentativo di path traversal
            if candidate.name in ("setup.html", "panel.html") or not candidate.is_file():
                return self._send_404()
            ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            try:
                data = candidate.read_bytes()
            except OSError:
                return self._send_404()
            # no-cache: Safari ricontrolla sempre, così dopo un aggiornamento
            # l'iPhone vede subito la versione nuova dell'app.
            self._send_bytes(data, ctype, extra_headers=[("Cache-Control", "no-cache")])

        # ---- WebSocket ----

        def _handle_ws(self, query):
            key = self.headers.get("Sec-WebSocket-Key")
            if not key or self.headers.get("Upgrade", "").lower() != "websocket":
                self.close_connection = True
                return self._send_bytes(b"Serve un upgrade WebSocket.", "text/plain; charset=utf-8", status=400)

            self.close_connection = True  # da qui in poi gestiamo noi il socket a mano
            try:
                self.wfile.write(miniws.handshake_response(key))
                self.wfile.flush()
            except OSError:
                return

            token = query.get("token", [None])[0]
            device = device_store.verify(token) if token else None

            if device is None:
                try:
                    miniws.send_json(self.wfile, {"type": "auth", "ok": False})
                    miniws.send_close(self.wfile, 4001)
                except OSError:
                    pass
                return

            try:
                miniws.send_json(self.wfile, {
                    "type": "hello",
                    "name": computer_name,
                    "accessibility": shared.accessibility,
                })
            except OSError:
                return

            pressed: set = set()
            try:
                while True:
                    opcode, payload = miniws.read_message(self.rfile, self.wfile)
                    if opcode != miniws.OPCODE_TEXT:
                        continue
                    reply = handle_message(backend, lock, payload.decode("utf-8", "replace"), pressed)
                    if reply is not None:
                        miniws.send_json(self.wfile, reply)
            except (miniws.ConnectionClosed, OSError):
                pass
            finally:
                release_pressed(backend, lock, pressed)

    return Handler


class TlsThreadingHTTPServer(ThreadingHTTPServer):
    """Come ThreadingHTTPServer, ma l'handshake TLS avviene nel thread del
    worker (in finish_request), non nel thread che accetta le connessioni:
    un client TCP che non parla (o parla piano) blocca solo se stesso, non gli
    altri."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, ssl_context: ssl.SSLContext):
        self.ssl_context = ssl_context
        super().__init__(server_address, RequestHandlerClass)

    def finish_request(self, request, client_address):
        request.settimeout(TLS_HANDSHAKE_TIMEOUT)
        try:
            tls_socket = self.ssl_context.wrap_socket(request, server_side=True)
        except OSError:
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            self.RequestHandlerClass(tls_socket, client_address, self)
        finally:
            try:
                tls_socket.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):
        pass  # un client che si disconnette bruscamente non deve riempire il terminale


def make_ssl_context(cert_paths: certs.CertPaths) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_paths.server_cert), str(cert_paths.server_key))
    return ctx


# --------------------------------------------------------------------------
# Server HTTP ("setup"): pagina di configurazione, profilo, certificato CA
# --------------------------------------------------------------------------

def build_setup_config(computer_name: str, hostname: str, ip: Optional[str], app_port: int, ca_fp: str) -> dict:
    urls = [f"https://{hostname}:{app_port}/"]
    if ip:
        urls.append(f"https://{ip}:{app_port}/")
    return {
        "appUrls": urls,
        "computerName": computer_name,
        "caFingerprint": ca_fp,
        "profileUrl": "/TeleMac.mobileconfig",
    }


def inject_setup_config(html: str, config: dict) -> str:
    payload = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    return html.replace('"__TELEMAC_SETUP_CONFIG__"', payload)


def make_setup_handler(*, config_provider: Callable[[], dict], state_path: Path, computer_name: str, web_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TeleMac/2.0"
        timeout = WS_IDLE_TIMEOUT

        def log_message(self, fmt, *args):
            pass

        def _send_bytes(self, data: bytes, content_type: str, status: int = 200, extra_headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (extra_headers or []):
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                return self._serve_setup_page()
            if path == "/TeleMac.mobileconfig":
                return self._serve_mobileconfig()
            if path == "/TeleMac-CA.cer":
                return self._serve_ca_cert()
            self._send_bytes(b"Non trovato.", "text/plain; charset=utf-8", status=404)

        def _serve_setup_page(self):
            page = web_dir / "setup.html"
            try:
                html = page.read_text(encoding="utf-8")
            except OSError:
                return self._send_bytes(
                    b"web/setup.html non trovato.", "text/plain; charset=utf-8", status=404,
                )
            html = inject_setup_config(html, config_provider())
            self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")

        def _serve_mobileconfig(self):
            data = certs.mobileconfig(state_path, computer_name)
            self._send_bytes(
                data,
                "application/x-apple-aspen-config",
                extra_headers=[("Content-Disposition", 'attachment; filename="TeleMac.mobileconfig"')],
            )

        def _serve_ca_cert(self):
            data = certs.ca_der(state_path)
            self._send_bytes(data, "application/x-x509-ca-cert")

    return Handler


# --------------------------------------------------------------------------
# Avvio, banner, CLI
# --------------------------------------------------------------------------

def _truncated_fingerprint(fp: str) -> str:
    pairs = fp.split(":")
    return ":".join(pairs[:8]) + "…"


def print_banner(*, hostname: str, ip: Optional[str], app_port: int, setup_port: int, ca_fp: str):
    setup_host = ip or hostname
    setup_url = f"http://{setup_host}:{setup_port}/"
    app_urls = [f"https://{hostname}:{app_port}/"]
    if ip:
        app_urls.append(f"https://{ip}:{app_port}/")
    print()
    print(qr.render_terminal(setup_url))
    print()
    print(f"  Pagina di configurazione: {setup_url}")
    print("  App: " + " oppure ".join(app_urls))
    print(f"  Impronta certificato CA: {_truncated_fingerprint(ca_fp)}")
    print(f"  Profilo per AirDrop: {state_dir() / 'TeleMac.mobileconfig'}")
    print()
    print("  1. Sull'iPhone apri la pagina di configurazione e installa il certificato.")
    print("  2. Attiva la fiducia nel certificato (Impostazioni → Generali → Info).")
    print("  3. Apri TeleMac e abbina il Mac con il codice che comparirà qui.")
    print()
    print("  (Ctrl+C per fermare il server)")
    print()


def _start_accessibility_watcher(shared: SharedState):
    def poll():
        while True:
            time.sleep(2)
            if accessibility_status(prompt=False):
                shared.accessibility = True
                shared.notify({"event": "accessibility", "granted": True})
                print("  ✅ Permesso Accessibilità concesso.")
                return
    threading.Thread(target=poll, daemon=True).start()


def _make_pairing(*, dry_run: bool, shared: SharedState, app_mode: bool = False) -> PairingManager:
    dialog = {"proc": None}

    def on_code(code: str, seconds_left: float):
        shared.show_code(code, seconds_left)
        if app_mode:
            return  # il codice lo mostra la finestra dell'app, nel log non serve
        pretty = f"{code[:3]} {code[3:]}"
        print(f"  Codice di abbinamento: {pretty}")  # una riga per evento, anche in --background
        if dry_run or sys.platform != "darwin":
            return
        try:
            dialog["proc"] = subprocess.Popen([
                "osascript", "-e",
                'display dialog "Codice TeleMac per l’iPhone:\n\n' + pretty
                + '" with title "TeleMac" buttons {"OK"} default button 1 giving up after 120',
            ])
        except Exception:
            pass

    def on_done():
        shared.hide_code()
        proc = dialog.get("proc")
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        dialog["proc"] = None

    return PairingManager(on_code=on_code, on_done=on_done)


def _make_emitter(out=None) -> Callable[[dict], None]:
    """Scrive gli eventi per l'app per Mac su stdout, una riga JSON ciascuno."""
    lock = threading.Lock()

    def emit(event: dict) -> None:
        line = EVENT_PREFIX + json.dumps(event, ensure_ascii=False)
        with lock:
            print(line, file=out or sys.stdout, flush=True)

    return emit


def _watch_parent(stop: threading.Event) -> None:
    """In modalità app: quando l'app si chiude (anche se va in crash) il nostro
    stdin arriva alla fine, e allora ci fermiamo anche noi."""
    def run():
        try:
            while sys.stdin.readline():
                pass
        except Exception:
            pass
        stop.set()
    threading.Thread(target=run, daemon=True).start()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Server TeleMac per controllare il Mac dall'iPhone.")
    parser.add_argument("--port", type=int, default=DEFAULT_APP_PORT, help="porta dell'app HTTPS")
    parser.add_argument("--setup-port", type=int, default=DEFAULT_SETUP_PORT, help="porta della pagina di setup HTTP")
    parser.add_argument("--dry-run", action="store_true", help="non tocca mouse/tastiera, stampa solo i comandi")
    parser.add_argument("--background", action="store_true", help="niente QR, log ridotti al minimo")
    parser.add_argument("--forget-devices", action="store_true", help="svuota l'elenco dei dispositivi abbinati ed esce")
    parser.add_argument("--app", action="store_true",
                        help="avviato da TeleMac.app: eventi su stdout, pannello locale per la finestra")
    parser.add_argument("--panel-port", type=int, default=0, help="porta locale del pannello (solo con --app)")
    args = parser.parse_args(argv)
    quiet = args.background or args.app

    state = state_dir()
    state.mkdir(parents=True, exist_ok=True)

    if args.forget_devices:
        store = DeviceStore(state / "devices.json")
        n = store.count()
        store.clear()
        print(f"Rimossi {n} dispositivi abbinati.")
        return 0

    emit = _make_emitter() if args.app else None
    stop = threading.Event()

    web = web_directory()

    hostname = netinfo.local_hostname()
    computer_name = netinfo.computer_name()
    ip = netinfo.lan_ipv4()

    if not quiet:
        print(f"TeleMac su {computer_name} ({hostname})...")

    cert_paths = certs.ensure_certificates(state, [hostname], [ip] if ip else [])
    ca_fp = certs.ca_fingerprint(state)
    # Copia del profilo su disco, da mandare all'iPhone con AirDrop in alternativa alla pagina web.
    profile_path = state / "TeleMac.mobileconfig"
    try:
        profile_path.write_bytes(certs.mobileconfig(state, computer_name))
    except OSError:
        pass

    backend = create_backend(dry_run=args.dry_run)
    lock = threading.Lock()

    shared = SharedState(emit=emit)
    if not args.dry_run and sys.platform == "darwin":
        shared.accessibility = accessibility_status(prompt=False)
        if shared.accessibility is False:
            # Con --app la richiesta di permesso la fa l'app stessa (è lei che
            # compare nell'elenco di Accessibilità), qui aspettiamo e basta.
            if not args.app:
                shared.accessibility = accessibility_status(prompt=True)
            if not shared.accessibility:
                if args.background:
                    print("  Permesso Accessibilità mancante: Impostazioni di Sistema → Privacy e sicurezza → Accessibilità.")
                elif not args.app:
                    print(ACCESSIBILITY_INSTRUCTIONS)
                _start_accessibility_watcher(shared)

    device_store = DeviceStore(state / "devices.json")
    pairing = _make_pairing(dry_run=args.dry_run, shared=shared, app_mode=args.app)

    app_handler = make_app_handler(
        backend=backend, lock=lock, pairing=pairing, device_store=device_store,
        shared=shared, computer_name=computer_name, web_dir=web,
    )
    ssl_context = make_ssl_context(cert_paths)

    try:
        httpd = TlsThreadingHTTPServer(("0.0.0.0", args.port), app_handler, ssl_context)
    except OSError as exc:
        if args.port != 0 and exc.errno == errno.EADDRINUSE:
            if args.app:
                emit({"event": "error", "code": "port_in_use", "port": args.port})
                return EXIT_PORT_IN_USE
            print("TeleMac è già in esecuzione.")
            if args.background:
                print(f"  Pagina di configurazione: http://{ip or hostname}:{args.setup_port}/")
            else:
                print_banner(hostname=hostname, ip=ip, app_port=args.port, setup_port=args.setup_port, ca_fp=ca_fp)
            return 0
        raise

    app_port = httpd.server_address[1]

    def config_provider():
        return build_setup_config(computer_name, hostname, ip, app_port, ca_fp)

    setup_handler = make_setup_handler(
        config_provider=config_provider, state_path=state, computer_name=computer_name, web_dir=web,
    )
    try:
        setupd = ThreadingHTTPServer(("0.0.0.0", args.setup_port), setup_handler)
    except OSError as exc:
        # Senza pagina di configurazione l'app funziona lo stesso per chi è già abbinato.
        print(f"  Attenzione: porta {args.setup_port} occupata ({exc.strerror}), pagina di configurazione non disponibile.")
        setupd = None
    else:
        setupd.daemon_threads = True
    setup_port = setupd.server_address[1] if setupd else args.setup_port

    servers = [httpd] + ([setupd] if setupd else [])

    if args.app:
        token = os.environ.get("TELEMAC_PANEL_TOKEN") or secrets.token_urlsafe(32)
        qr_cache = panel.QrCache()

        def status_provider():
            return panel.build_panel_status(
                shared=shared, device_store=device_store, computer_name=computer_name,
                hostname=hostname, ip=ip, app_port=app_port,
                setup_port=setup_port if setupd else None, ca_fp=ca_fp,
                profile_path=profile_path, qr_cache=qr_cache, dry_run=args.dry_run,
            )

        panel_handler = panel.make_panel_handler(
            token=token, status_provider=status_provider, device_store=device_store, web_dir=web,
        )
        paneld = ThreadingHTTPServer(("127.0.0.1", args.panel_port), panel_handler)
        paneld.daemon_threads = True
        servers.append(paneld)
        panel_url = f"http://127.0.0.1:{paneld.server_address[1]}/?token={token}"

    for srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    if args.app:
        emit({"event": "ready", "panel": panel_url, "appPort": app_port, "setupPort": setup_port})
        _watch_parent(stop)
    elif args.background:
        print(f"TeleMac in esecuzione in background (app :{app_port}, setup :{setup_port}).")
    else:
        print_banner(hostname=hostname, ip=ip, app_port=app_port, setup_port=setup_port, ca_fp=ca_fp)

    # L'app per Mac ci chiude con SIGTERM: usciamo per bene (cursore compreso).
    try:
        signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
    except ValueError:
        pass  # non siamo nel thread principale (succede nei test)

    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        if not quiet:
            print("\nCiao!")
    try:
        with lock:
            backend.cursor_restore()
    except Exception:
        pass
    for srv in servers:
        srv.shutdown()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
