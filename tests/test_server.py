"""Test d'integrazione di telemac/server.py: avviamo l'app HTTPS e la pagina
di setup HTTP su porte effimere, in thread, con un backend "a secco" che
registra le chiamate, e parliamo con loro come farebbe un client vero."""

from __future__ import annotations

import base64
import errno
import http.client
import io
import json
import os
import plistlib
import socket
import ssl
import struct
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

import certs  # noqa: E402
import miniws  # noqa: E402
import pairing  # noqa: E402
import server  # noqa: E402
from backends import DryRunBackend  # noqa: E402


# --------------------------------------------------------------------------
# Utilità comuni
# --------------------------------------------------------------------------

class RecordingBackend(DryRunBackend):
    """Backend 'a secco' che oltre a stampare registra ogni chiamata."""

    def __init__(self):
        super().__init__(out=io.StringIO())
        self._call_lock = threading.Lock()
        self.calls = []

    def _record(self, *item):
        with self._call_lock:
            self.calls.append(item)

    def move(self, dx, dy):
        self._record("move", dx, dy)

    def scroll(self, dx, dy):
        self._record("scroll", dx, dy)

    def click(self, button):
        self._record("click", button)

    def button(self, button, down):
        self._record("button", button, down)

    def key(self, name, mods):
        self._record("key", name, tuple(mods))

    def text(self, s):
        self._record("text", s)

    def media(self, name):
        self._record("media", name)

    def display_sleep(self):
        self._record("sleep")

    def cursor_scale(self, scale):
        self._record("cursor_scale", scale)
        return True

    def cursor_restore(self):
        self._record("cursor_restore")

    def snapshot(self):
        with self._call_lock:
            return list(self.calls)


def _ws_handshake_request(sock, host, port, path):
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("connessione chiusa durante l'handshake WS")
        head += chunk
    return head.decode("iso-8859-1")


def _client_send_frame(sock, opcode, payload):
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    header = bytes([0x80 | opcode])
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", n)
    sock.sendall(header + mask + masked)


def _client_send_json(sock, obj):
    _client_send_frame(sock, miniws.OPCODE_TEXT, json.dumps(obj).encode("utf-8"))


def _poll_until(predicate, timeout=2.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


class ServerHarness:
    """Avvia i due server di TeleMac su porte effimere con uno stato tutto
    temporaneo, e li smonta con close()."""

    HOSTNAME = "telemac-test.local"
    COMPUTER_NAME = "Mac di Prova"

    def __init__(self):
        self.tmp_home = tempfile.TemporaryDirectory()
        self.tmp_web = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp_home.name)
        self.web = Path(self.tmp_web.name)
        self._write_web_files()

        self._old_home = os.environ.get("TELEMAC_HOME")
        self._old_web = os.environ.get("TELEMAC_WEB_DIR")
        os.environ["TELEMAC_HOME"] = str(self.state)
        os.environ["TELEMAC_WEB_DIR"] = str(self.web)

        self.cert_paths = certs.ensure_certificates(self.state, [self.HOSTNAME], [])
        self.ca_fp = certs.ca_fingerprint(self.state)

        self.backend = RecordingBackend()
        self.lock = threading.Lock()
        self.shared = server.SharedState()
        self.device_store = pairing.DeviceStore(self.state / "devices.json")

        self.pair_events = []
        self.pairing = pairing.PairingManager(
            on_code=lambda code, secs: self.pair_events.append(("code", code, secs)),
            on_done=lambda: self.pair_events.append(("done",)),
        )

        handler = server.make_app_handler(
            backend=self.backend, lock=self.lock, pairing=self.pairing,
            device_store=self.device_store, shared=self.shared,
            computer_name=self.COMPUTER_NAME, web_dir=self.web,
        )
        ctx = server.make_ssl_context(self.cert_paths)
        self.httpd = server.TlsThreadingHTTPServer(("127.0.0.1", 0), handler, ctx)
        self.app_port = self.httpd.server_address[1]

        def config_provider():
            return server.build_setup_config(self.COMPUTER_NAME, self.HOSTNAME, None, self.app_port, self.ca_fp)

        self._config_provider = config_provider
        setup_handler = server.make_setup_handler(
            config_provider=config_provider, state_path=self.state,
            computer_name=self.COMPUTER_NAME, web_dir=self.web,
        )
        self.setupd = server.ThreadingHTTPServer(("127.0.0.1", 0), setup_handler)
        self.setup_port = self.setupd.server_address[1]

        self._threads = [
            threading.Thread(target=self.httpd.serve_forever, daemon=True),
            threading.Thread(target=self.setupd.serve_forever, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _write_web_files(self):
        (self.web / "index.html").write_text("<html><body>ciao telemac</body></html>", encoding="utf-8")
        (self.web / "style.css").write_text("body { color: black; }", encoding="utf-8")
        (self.web / "secret").mkdir()
        (self.web / "secret" / "dentro.txt").write_text("non dovrebbe uscire dalla web dir", encoding="utf-8")
        (self.web / "setup.html").write_text(
            "<!doctype html><html><head><script>\n"
            'const CONFIG = "__TELEMAC_SETUP_CONFIG__";\n'
            "</script></head><body>setup</body></html>\n",
            encoding="utf-8",
        )

    # ---- client HTTP/HTTPS ----

    def https_connection(self, timeout=5):
        ctx = ssl._create_unverified_context()
        return http.client.HTTPSConnection("127.0.0.1", self.app_port, context=ctx, timeout=timeout)

    def setup_connection(self, timeout=5):
        return http.client.HTTPConnection("127.0.0.1", self.setup_port, timeout=timeout)

    # ---- client WebSocket "a mano" ----

    def ws_connect(self, token, timeout=5):
        ctx = ssl._create_unverified_context()
        raw = socket.create_connection(("127.0.0.1", self.app_port), timeout=timeout)
        ssock = ctx.wrap_socket(raw, server_hostname="127.0.0.1")
        path = f"/ws?token={token}" if token is not None else "/ws"
        head = _ws_handshake_request(ssock, "127.0.0.1", self.app_port, path)
        rfile = ssock.makefile("rb")
        return ssock, rfile, head

    def mute_tcp_client(self):
        """Apre una connessione TLS valida ma non manda mai la richiesta HTTP:
        deve bloccare solo se stesso, mai il resto del server."""
        ctx = ssl._create_unverified_context()
        raw = socket.create_connection(("127.0.0.1", self.app_port), timeout=5)
        return ctx.wrap_socket(raw, server_hostname="127.0.0.1")

    def close(self):
        try:
            self.httpd.shutdown()
            self.setupd.shutdown()
            self.httpd.server_close()
            self.setupd.server_close()
        finally:
            if self._old_home is None:
                os.environ.pop("TELEMAC_HOME", None)
            else:
                os.environ["TELEMAC_HOME"] = self._old_home
            if self._old_web is None:
                os.environ.pop("TELEMAC_WEB_DIR", None)
            else:
                os.environ["TELEMAC_WEB_DIR"] = self._old_web
            self.tmp_home.cleanup()
            self.tmp_web.cleanup()


class HarnessTestCase(unittest.TestCase):
    def setUp(self):
        self.h = ServerHarness()
        self.addCleanup(self.h.close)


# --------------------------------------------------------------------------
# App HTTPS: /api/ping, file statici
# --------------------------------------------------------------------------

class TestPing(HarnessTestCase):
    def test_ping_ok_con_cors(self):
        conn = self.h.https_connection()
        conn.request("GET", "/api/ping")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), "*")
        self.assertEqual(resp.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertIsNotNone(resp.getheader("Content-Length"))
        self.assertEqual(body, {
            "ok": True, "name": "Mac di Prova", "accessibility": None, "version": 2,
        })
        conn.close()


class TestStaticFiles(HarnessTestCase):
    def test_index_alla_radice(self):
        conn = self.h.https_connection()
        conn.request("GET", "/")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn(b"ciao telemac", resp.read())
        conn.close()

    def test_file_statico_generico(self):
        conn = self.h.https_connection()
        conn.request("GET", "/style.css")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        conn.close()

    def test_setup_html_non_servito_su_https(self):
        conn = self.h.https_connection()
        conn.request("GET", "/setup.html")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        resp.read()
        conn.close()

    def test_file_inesistente_404(self):
        conn = self.h.https_connection()
        conn.request("GET", "/non-esiste.html")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        resp.read()
        conn.close()

    def test_path_traversal_impedito(self):
        conn = self.h.https_connection()
        conn.request("GET", "/../../../../etc/passwd")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        resp.read()
        conn.close()

    def test_niente_icon_png(self):
        # Il server v2 non serve più /icon.png (le icone sono file statici
        # normali in web/, gestiti dal Blocco 3): senza il file, è un 404.
        conn = self.h.https_connection()
        conn.request("GET", "/icon.png")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        resp.read()
        conn.close()


# --------------------------------------------------------------------------
# Pagina di setup HTTP
# --------------------------------------------------------------------------

class TestSetupPage(HarnessTestCase):
    def test_config_iniettata(self):
        conn = self.h.setup_connection()
        conn.request("GET", "/")
        resp = conn.getresponse()
        html = resp.read().decode("utf-8")
        self.assertEqual(resp.status, 200)
        self.assertNotIn("__TELEMAC_SETUP_CONFIG__", html)
        marker = "const CONFIG = "
        start = html.index(marker) + len(marker)
        end = html.index(";", start)
        config = json.loads(html[start:end])
        self.assertEqual(config["computerName"], "Mac di Prova")
        self.assertEqual(config["profileUrl"], "/TeleMac.mobileconfig")
        self.assertIn(f"https://telemac-test.local:{self.h.app_port}/", config["appUrls"])
        self.assertEqual(config["caFingerprint"], self.h.ca_fp)
        conn.close()

    def test_nessun_token_richiesto(self):
        # niente Authorization, niente query string: deve rispondere comunque.
        conn = self.h.setup_connection()
        conn.request("GET", "/")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        resp.read()
        conn.close()

    def test_mobileconfig_scaricabile(self):
        conn = self.h.setup_connection()
        conn.request("GET", "/TeleMac.mobileconfig")
        resp = conn.getresponse()
        data = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "application/x-apple-aspen-config")
        self.assertIn('filename="TeleMac.mobileconfig"', resp.getheader("Content-Disposition"))
        parsed = plistlib.loads(data)
        self.assertEqual(bytes(parsed["PayloadContent"][0]["PayloadContent"]), certs.ca_der(self.h.state))
        conn.close()

    def test_ca_cer_scaricabile(self):
        conn = self.h.setup_connection()
        conn.request("GET", "/TeleMac-CA.cer")
        resp = conn.getresponse()
        data = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "application/x-x509-ca-cert")
        self.assertEqual(data, certs.ca_der(self.h.state))
        conn.close()

    def test_tutto_il_resto_404(self):
        conn = self.h.setup_connection()
        conn.request("GET", "/qualcosa-a-caso")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        resp.read()
        conn.close()


# --------------------------------------------------------------------------
# Abbinamento
# --------------------------------------------------------------------------

class TestPairing(HarnessTestCase):
    def _pair_start(self):
        conn = self.h.https_connection()
        conn.request("POST", "/api/pair/start", body=b"")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, body

    def _pair_finish(self, code, device="iPhone"):
        conn = self.h.https_connection()
        payload = json.dumps({"code": code, "device": device}).encode("utf-8")
        conn.request("POST", "/api/pair/finish", body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, body

    def test_flusso_completo(self):
        status, body = self._pair_start()
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["expires_in"], 120)

        codes = [e for e in self.h.pair_events if e[0] == "code"]
        self.assertEqual(len(codes), 1)
        code = codes[0][1]

        status, body = self._pair_finish(code)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["name"], "Mac di Prova")
        self.assertTrue(body["token"])

        device = self.h.device_store.verify(body["token"])
        self.assertIsNotNone(device)
        self.assertEqual(device["name"], "iPhone")

    def test_too_fast(self):
        self._pair_start()
        status, body = self._pair_start()
        self.assertEqual(status, 429)
        self.assertEqual(body, {"ok": False, "error": "too_fast"})

    def test_codice_sbagliato(self):
        self._pair_start()
        status, body = self._pair_finish("000000")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "wrong_code")
        self.assertIn("attempts_left", body)

    def test_blocco_dopo_cinque_tentativi(self):
        self._pair_start()
        for _ in range(5):
            status, body = self._pair_finish("000000")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "locked")

    def test_nessun_codice_richiesto(self):
        status, body = self._pair_finish("123456")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "no_code")

    def test_json_non_valido_da_bad_request(self):
        conn = self.h.https_connection()
        conn.request("POST", "/api/pair/finish", body=b"{non json", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 400)
        self.assertEqual(body, {"ok": False, "error": "bad_request"})
        conn.close()

    def test_corpo_troppo_grande_e_bad_request(self):
        conn = self.h.https_connection()
        payload = json.dumps({"code": "123456", "device": "x" * 10000}).encode("utf-8")
        self.assertGreater(len(payload), 4096)
        conn.request("POST", "/api/pair/finish", body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 400)
        self.assertEqual(body["error"], "bad_request")
        conn.close()


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------

class TestWebSocket(HarnessTestCase):
    def _paired_token(self):
        self.h.pairing.start()
        code = self.h.pair_events[-1][1]
        ok, error, _left = self.h.pairing.finish(code)
        self.assertTrue(ok, error)
        return self.h.device_store.add("iPhone di test")

    def test_handshake_e_hello_con_token_valido(self):
        token = self._paired_token()
        ssock, rfile, head = self.h.ws_connect(token)
        try:
            self.assertIn("101", head.splitlines()[0])
            self.assertIn("Upgrade: websocket", head)
            fin, opcode, payload = miniws.read_frame(rfile)
            msg = json.loads(payload.decode("utf-8"))
            self.assertEqual(msg, {"type": "hello", "name": "Mac di Prova", "accessibility": None})
        finally:
            rfile.close()
            ssock.close()

    def test_move_arriva_al_backend_con_clamp(self):
        token = self._paired_token()
        ssock, rfile, head = self.h.ws_connect(token)
        try:
            miniws.read_frame(rfile)  # hello
            _client_send_json(ssock, {"type": "move", "dx": 10.0, "dy": -5.0})
            _client_send_json(ssock, {"type": "move", "dx": 99999.0, "dy": 0.0})  # deve essere limitato

            def has_two_moves():
                return len([c for c in self.h.backend.snapshot() if c[0] == "move"]) >= 2

            self.assertTrue(_poll_until(has_two_moves))
            moves = [c for c in self.h.backend.snapshot() if c[0] == "move"]
            self.assertEqual(moves[0], ("move", 10.0, -5.0))
            self.assertEqual(moves[1][1], 500.0)  # MOVE_LIMIT
        finally:
            rfile.close()
            ssock.close()

    def test_ping_pong(self):
        token = self._paired_token()
        ssock, rfile, head = self.h.ws_connect(token)
        try:
            miniws.read_frame(rfile)  # hello
            _client_send_json(ssock, {"type": "ping"})
            fin, opcode, payload = miniws.read_frame(rfile)
            self.assertEqual(json.loads(payload.decode("utf-8")), {"type": "pong"})
        finally:
            rfile.close()
            ssock.close()

    def test_nan_e_inf_vengono_ignorati(self):
        token = self._paired_token()
        ssock, rfile, head = self.h.ws_connect(token)
        try:
            miniws.read_frame(rfile)  # hello
            # json.dumps non li produce, ma json.loads (Python) li accetta:
            # mandiamo testo grezzo, come farebbe un client bacato.
            _client_send_frame(ssock, miniws.OPCODE_TEXT, b'{"type":"move","dx":NaN,"dy":1.0}')
            _client_send_frame(ssock, miniws.OPCODE_TEXT, b'{"type":"move","dx":Infinity,"dy":1.0}')
            _client_send_json(ssock, {"type": "ping"})  # sentinella: quando arriva il pong, sappiamo che gli altri
            fin, opcode, payload = miniws.read_frame(rfile)  # sono già stati processati (in ordine)
            self.assertEqual(json.loads(payload.decode("utf-8")), {"type": "pong"})
            moves = [c for c in self.h.backend.snapshot() if c[0] == "move"]
            self.assertEqual(moves, [])
        finally:
            rfile.close()
            ssock.close()

    def test_token_non_valido_auth_false_e_close_4001(self):
        ssock, rfile, head = self.h.ws_connect("token-inventato")
        try:
            self.assertIn("101", head.splitlines()[0])
            fin, opcode, payload = miniws.read_frame(rfile)
            self.assertEqual(json.loads(payload.decode("utf-8")), {"type": "auth", "ok": False})
            fin, opcode, payload = miniws.read_frame(rfile)
            self.assertEqual(opcode, miniws.OPCODE_CLOSE)
            code = struct.unpack(">H", payload[:2])[0]
            self.assertEqual(code, 4001)
        finally:
            rfile.close()
            ssock.close()

    def test_token_assente_auth_false(self):
        ssock, rfile, head = self.h.ws_connect(None)
        try:
            fin, opcode, payload = miniws.read_frame(rfile)
            self.assertEqual(json.loads(payload.decode("utf-8")), {"type": "auth", "ok": False})
        finally:
            rfile.close()
            ssock.close()

    def test_disconnessione_con_tasto_premuto_rilascia(self):
        token = self._paired_token()
        ssock, rfile, head = self.h.ws_connect(token)
        miniws.read_frame(rfile)  # hello
        _client_send_json(ssock, {"type": "button", "button": "left", "down": True})

        def button_down_visto():
            return ("button", "left", True) in self.h.backend.snapshot()

        self.assertTrue(_poll_until(button_down_visto))
        # Disconnessione brusca, senza handshake di chiusura WS. 'rfile' tiene
        # un riferimento al socket (socket.makefile()): senza chiuderlo anche
        # lui, il file descriptor resta aperto e il server non si accorge
        # della disconnessione.
        rfile.close()
        ssock.close()

        def button_up_visto():
            return ("button", "left", False) in self.h.backend.snapshot()

        self.assertTrue(_poll_until(button_up_visto, timeout=5))

    def test_un_client_muto_non_blocca_le_altre_richieste(self):
        mute = self.h.mute_tcp_client()
        try:
            start = time.monotonic()
            conn = self.h.https_connection(timeout=5)
            conn.request("GET", "/api/ping")
            resp = conn.getresponse()
            elapsed = time.monotonic() - start
            self.assertEqual(resp.status, 200)
            resp.read()
            conn.close()
            self.assertLess(elapsed, 3.0, "il client muto ha bloccato il server")
        finally:
            mute.close()


# --------------------------------------------------------------------------
# handle_message: unità pure, senza rete
# --------------------------------------------------------------------------

class TestHandleMessage(unittest.TestCase):
    def setUp(self):
        self.backend = RecordingBackend()
        self.lock = threading.Lock()
        self.pressed = set()

    def send(self, obj):
        raw = obj if isinstance(obj, str) else json.dumps(obj)
        return server.handle_message(self.backend, self.lock, raw, self.pressed)

    def test_move(self):
        self.send({"type": "move", "dx": 1.5, "dy": -2})
        self.assertEqual(self.backend.calls, [("move", 1.5, -2.0)])

    def test_move_clamp(self):
        self.send({"type": "move", "dx": 99999, "dy": -99999})
        self.assertEqual(self.backend.calls, [("move", 500.0, -500.0)])

    def test_move_nan_ignorato(self):
        self.send('{"type":"move","dx":NaN,"dy":1.0}')
        self.assertEqual(self.backend.calls, [])

    def test_move_inf_ignorato(self):
        self.send('{"type":"move","dx":Infinity,"dy":1.0}')
        self.assertEqual(self.backend.calls, [])

    def test_scroll_clamp(self):
        self.send({"type": "scroll", "dx": 0, "dy": 999999})
        self.assertEqual(self.backend.calls, [("scroll", 0.0, 2000.0)])

    def test_click_rifiuta_bottone_sconosciuto(self):
        self.send({"type": "click", "button": "middle"})
        self.assertEqual(self.backend.calls, [])

    def test_click_bottone_valido(self):
        self.send({"type": "click", "button": "left"})
        self.assertEqual(self.backend.calls, [("click", "left")])

    def test_button_down_aggiorna_pressed(self):
        self.send({"type": "button", "button": "left", "down": True})
        self.assertIn("left", self.pressed)
        self.send({"type": "button", "button": "left", "down": False})
        self.assertNotIn("left", self.pressed)

    def test_key_scarta_modificatore_sconosciuto(self):
        self.send({"type": "key", "name": "a", "mods": ["cmd", "hyper"]})
        self.assertEqual(self.backend.calls, [("key", "a", ("cmd",))])

    def test_key_nome_sconosciuto_ignorato(self):
        self.send({"type": "key", "name": "nonesiste", "mods": []})
        self.assertEqual(self.backend.calls, [])

    def test_media_nome_sconosciuto_ignorato(self):
        self.send({"type": "media", "name": "teletrasporto"})
        self.assertEqual(self.backend.calls, [])

    def test_media_valido(self):
        self.send({"type": "media", "name": "play"})
        self.assertEqual(self.backend.calls, [("media", "play")])

    def test_text_troncato(self):
        self.send({"type": "text", "text": "a" * 5000})
        self.assertEqual(self.backend.calls, [("text", "a" * 2000)])

    def test_sleep(self):
        self.send({"type": "sleep"})
        self.assertEqual(self.backend.calls, [("sleep",)])

    def test_ping_risponde_pong(self):
        reply = self.send({"type": "ping"})
        self.assertEqual(reply, {"type": "pong"})
        self.assertEqual(self.backend.calls, [])

    def test_json_malformato_ignorato(self):
        self.send("{non json")
        self.assertEqual(self.backend.calls, [])

    def test_non_e_un_oggetto(self):
        self.send("[1, 2, 3]")
        self.assertEqual(self.backend.calls, [])

    def test_campo_mancante_ignorato(self):
        self.send({"type": "move", "dx": 1.0})  # manca dy
        self.assertEqual(self.backend.calls, [])

    def test_tipo_sconosciuto_ignorato(self):
        self.send({"type": "boh"})
        self.assertEqual(self.backend.calls, [])


class TestReleasePressed(unittest.TestCase):
    def test_rilascia_tutti_i_tasti_premuti(self):
        backend = RecordingBackend()
        lock = threading.Lock()
        pressed = {"left", "right"}
        server.release_pressed(backend, lock, pressed)
        self.assertEqual(set(pressed), set())
        self.assertIn(("button", "left", False), backend.calls)
        self.assertIn(("button", "right", False), backend.calls)

    def test_rimette_il_cursore_com_era(self):
        backend = RecordingBackend()
        lock = threading.Lock()
        pressed = set()
        reply = server.handle_message(backend, lock, json.dumps({"type": "cursor", "scale": 2.5}), pressed)
        self.assertEqual(reply, {"type": "cursor", "ok": True})
        server.release_pressed(backend, lock, pressed)
        self.assertEqual(backend.calls, [("cursor_scale", 2.5), ("cursor_restore",)])
        self.assertNotIn(("button", server.CURSOR_MARK, False), backend.calls)


class TestCursorMessage(unittest.TestCase):
    def setUp(self):
        self.backend = RecordingBackend()
        self.lock = threading.Lock()
        self.pressed = set()

    def send(self, obj):
        return server.handle_message(self.backend, self.lock, json.dumps(obj), self.pressed)

    def test_scala_limitata_fra_1_e_4(self):
        self.send({"type": "cursor", "scale": 50})
        self.send({"type": "cursor", "scale": -3})
        self.assertEqual(self.backend.calls, [("cursor_scale", 4.0), ("cursor_scale", 1.0)])

    def test_tornare_a_1_non_richiede_ripristino(self):
        self.send({"type": "cursor", "scale": 3})
        self.send({"type": "cursor", "scale": 1})
        self.assertNotIn(server.CURSOR_MARK, self.pressed)

    def test_valori_non_validi_ignorati(self):
        self.assertIsNone(self.send({"type": "cursor", "scale": "tanto"}))
        self.assertIsNone(self.send({"type": "cursor"}))
        self.assertEqual(self.backend.calls, [])

    def test_backend_senza_supporto_risponde_ok_false(self):
        class Minimal:
            pass
        reply = server.handle_message(Minimal(), self.lock, json.dumps({"type": "cursor", "scale": 2}), set())
        self.assertEqual(reply, {"type": "cursor", "ok": False})


# --------------------------------------------------------------------------
# build_setup_config / inject_setup_config
# --------------------------------------------------------------------------

class TestSetupConfigHelpers(unittest.TestCase):
    def test_build_setup_config_con_ip(self):
        cfg = server.build_setup_config("Mac di Mario", "Mac-di-Mario.local", "192.168.1.20", 8765, "AB:CD")
        self.assertEqual(cfg["appUrls"], [
            "https://Mac-di-Mario.local:8765/",
            "https://192.168.1.20:8765/",
        ])
        self.assertEqual(cfg["profileUrl"], "/TeleMac.mobileconfig")

    def test_build_setup_config_senza_ip(self):
        cfg = server.build_setup_config("Mac", "mac.local", None, 8765, "AB:CD")
        self.assertEqual(cfg["appUrls"], ["https://mac.local:8765/"])

    def test_inject_setup_config_sostituisce_letterale_con_virgolette(self):
        html = 'x = "__TELEMAC_SETUP_CONFIG__"; y = 1;'
        out = server.inject_setup_config(html, {"a": 1})
        self.assertNotIn("__TELEMAC_SETUP_CONFIG__", out)
        self.assertIn('x = {"a": 1}; y = 1;', out)

    def test_inject_setup_config_escapa_chiusura_script(self):
        html = 'x = "__TELEMAC_SETUP_CONFIG__";'
        out = server.inject_setup_config(html, {"a": "</script>"})
        self.assertNotIn("</script>", out)
        self.assertIn("<\\/script>", out)


# --------------------------------------------------------------------------
# CLI (main): --forget-devices e "già in esecuzione"
# --------------------------------------------------------------------------

class TestMainCli(unittest.TestCase):
    def setUp(self):
        self.tmp_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_home.cleanup)
        self._old_home = os.environ.get("TELEMAC_HOME")
        os.environ["TELEMAC_HOME"] = self.tmp_home.name
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("TELEMAC_HOME", None)
        else:
            os.environ["TELEMAC_HOME"] = self._old_home

    def test_forget_devices(self):
        store = pairing.DeviceStore(Path(self.tmp_home.name) / "devices.json")
        store.add("iPhone 1")
        store.add("iPhone 2")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = server.main(["--forget-devices"])
        self.assertEqual(code, 0)
        self.assertIn("2", buf.getvalue())
        self.assertEqual(store.count(), 0)

    def test_gia_in_esecuzione_quando_la_porta_e_occupata(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", 0))
        probe.listen(1)
        busy_port = probe.getsockname()[1]
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = server.main([
                    "--port", str(busy_port), "--setup-port", "0",
                    "--dry-run", "--background",
                ])
            self.assertEqual(code, 0)
            self.assertIn("già in esecuzione", buf.getvalue())
        finally:
            probe.close()


if __name__ == "__main__":
    unittest.main()
