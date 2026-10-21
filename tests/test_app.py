"""Prova l'app vera (TeleMac.app compilata) in modalità --headless --dry-run.

Il Mac non si muove: i comandi finiscono su stdout come "  [prova] ...".
Solo libreria standard di Python, gira su macOS:

    tools/build_mac_app.sh build            # prima, per avere l'app
    python3 -m unittest tests.test_app -v   # TELEMAC_APP=percorso se non è build/TeleMac.app
"""

import base64
import hashlib
import json
import os
import plistlib
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = Path(os.environ.get("TELEMAC_APP", ROOT / "build" / "TeleMac.app"))
BINARY = APP / "Contents" / "MacOS" / "TeleMac"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


class Server:
    """Un'istanza di TeleMac --headless con la sua cartella di stato."""

    def __init__(self, home=None, port=0, setup_port=0):
        self.home = Path(home or tempfile.mkdtemp(prefix="telemac-test-"))
        self.lines = []
        self.proc = subprocess.Popen(
            [str(BINARY), "--headless", "--dry-run", "--port", str(port), "--setup-port", str(setup_port),
             "--home", str(self.home)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.events = []
        self._cv = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            with self._cv:
                self.lines.append(line.rstrip("\n"))
                if line.startswith("@telemac "):
                    self.events.append(json.loads(line[len("@telemac "):]))
                self._cv.notify_all()

    def wait_event(self, name, timeout=20, after=0):
        deadline = time.time() + timeout
        with self._cv:
            while True:
                for ev in self.events[after:]:
                    if ev.get("event") == name:
                        return ev
                left = deadline - time.time()
                if left <= 0:
                    raise AssertionError(f"evento {name!r} non arrivato; righe: {self.lines}")
                self._cv.wait(left)

    def wait_line(self, text, timeout=5):
        deadline = time.time() + timeout
        with self._cv:
            while True:
                for line in self.lines:
                    if text in line:
                        return line
                left = deadline - time.time()
                if left <= 0:
                    raise AssertionError(f"riga {text!r} non arrivata; righe: {self.lines}")
                self._cv.wait(left)

    def count_codes(self):
        with self._cv:
            return len([e for e in self.events if "code" in e and e.get("event") == "pair_code"])

    def next_code(self, seen, timeout=5):
        """Il codice di abbinamento (solo in modalità prova compare su stdout)."""
        deadline = time.time() + timeout
        with self._cv:
            while True:
                codes = [e["code"] for e in self.events if "code" in e and e.get("event") == "pair_code"]
                if len(codes) > seen:
                    return codes[-1]
                left = deadline - time.time()
                if left <= 0:
                    raise AssertionError(f"codice non arrivato; righe: {self.lines}")
                self._cv.wait(left)

    def prova_lines(self):
        with self._cv:
            return [l.strip() for l in self.lines if l.strip().startswith("[prova]")]

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def http(url, method="GET", body=None, https=True):
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, context=CTX if https else None, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class WS:
    """Client WebSocket minimo (messaggi testo)."""

    def __init__(self, port, token):
        raw = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.sock = CTX.wrap_socket(raw, server_hostname="localhost")
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET /ws?token={token} HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        head = self._read_until(b"\r\n\r\n")
        self.status_line = head.split(b"\r\n")[0].decode()
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.accept_ok = accept in head.decode()

    def _read_until(self, marker):
        while marker not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buf += chunk
        head, _, self.buf = self.buf.partition(marker)
        return head

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError()
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        """(opcode, payload) del prossimo frame."""
        b0, b1 = self._need(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._need(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._need(8))[0]
        return b0 & 0x0F, self._need(n)

    def recv_json(self):
        op, payload = self.recv()
        assert op == 1, op
        return json.loads(payload)

    def send(self, obj, opcode=1, raw=None):
        payload = raw if raw is not None else json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        else:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


@unittest.skipUnless(BINARY.exists(), f"app non trovata: {BINARY} (lancia tools/build_mac_app.sh build)")
class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        ready = cls.server.wait_event("ready")
        cls.port = ready["appPort"]
        cls.setup_port = ready["setupPort"]
        cls.base = f"https://127.0.0.1:{cls.port}"
        cls.setup = f"http://127.0.0.1:{cls.setup_port}"
        cls._pair_calls = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.server.home, ignore_errors=True)

    # ---- pagine ----

    def test_ping(self):
        status, headers, body = http(self.base + "/api/ping")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], 2)
        self.assertIsNone(data["accessibility"])  # in modalità prova non serve
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    def test_pagina_dell_iphone_e_file_statici(self):
        status, headers, body = http(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"TeleMac", body)
        self.assertEqual(headers["Cache-Control"], "no-cache")
        status, headers, _ = http(self.base + "/app.js")
        self.assertEqual((status, headers["Content-Type"].split(";")[0]), (200, "text/javascript"))
        status, headers, body = http(self.base + "/icon-192.png")
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_pagine_riservate_e_percorsi_strani(self):
        for path in ("/panel.html", "/setup.html", "/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd",
                     "/.DS_Store", "/nonesiste", "/api/pair/start"):
            status, _, _ = http(self.base + path)
            self.assertEqual(status, 404, path)

    def test_pagina_di_configurazione(self):
        # "/" sulla porta HTTP apre il telecomando (trackpad), la configurazione sta su /setup.
        status, _, body = http(self.setup + "/", https=False)
        self.assertEqual(status, 200)
        self.assertIn(b'id="clickpad"', body)
        status, _, body = http(self.setup + "/setup", https=False)
        html = body.decode()
        self.assertEqual(status, 200)
        self.assertNotIn("__TELEMAC_SETUP_CONFIG__", html)
        self.assertIn('"profileUrl"', html)
        self.assertIn(f":{self.port}/", html)

    def test_profilo_e_certificato_della_ca(self):
        status, headers, body = http(self.setup + "/TeleMac.mobileconfig", https=False)
        self.assertEqual((status, headers["Content-Type"]), (200, "application/x-apple-aspen-config"))
        profile = plistlib.loads(body)
        self.assertEqual(profile["PayloadType"], "Configuration")
        der = profile["PayloadContent"][0]["PayloadContent"]
        status, _, cer = http(self.setup + "/TeleMac-CA.cer", https=False)
        self.assertEqual(cer, der)
        # Il certificato del server è firmato dalla CA ed è valido per localhost/IP privati.
        ca_pem = (self.server.home / "ca.pem").read_text()
        out = subprocess.run(
            ["/usr/bin/openssl", "verify", "-CAfile", str(self.server.home / "ca.pem"),
             str(self.server.home / "server.pem")], capture_output=True, text=True)
        self.assertIn("OK", out.stdout, out.stdout + out.stderr)
        self.assertIn("BEGIN CERTIFICATE", ca_pem)

    # ---- abbinamento e comandi ----

    def _pair(self, device="iPhone di prova"):
        """Abbina un dispositivo e restituisce il token. Aspetta il limite fra due richieste."""
        type(self)._pair_calls += 1
        deadline = time.time() + 30
        while True:
            seen = self.server.count_codes()
            status, _, body = http(self.base + "/api/pair/start", "POST", b"{}")
            if status == 200:
                break
            self.assertEqual(json.loads(body)["error"], "too_fast")
            self.assertLess(time.time(), deadline)
            time.sleep(1)
        code = self.server.next_code(seen)
        status, _, body = http(self.base + "/api/pair/finish", "POST",
                               json.dumps({"code": code, "device": device}).encode())
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        return data["token"]

    def test_abbinamento_e_websocket(self):
        # codice sbagliato: 403 e tentativi che calano
        http(self.base + "/api/pair/start", "POST", b"{}")
        status, _, body = http(self.base + "/api/pair/finish", "POST", b'{"code":"abcdef"}')
        self.assertEqual(status, 403)
        data = json.loads(body)
        self.assertEqual((data["error"], data["attempts_left"]), ("wrong_code", 4))
        # corpo malformato
        status, _, body = http(self.base + "/api/pair/finish", "POST", b"non json")
        self.assertEqual((status, json.loads(body)["error"]), (400, "bad_request"))
        # due richieste di fila: la seconda è troppo veloce
        status, _, body = http(self.base + "/api/pair/start", "POST", b"{}")
        self.assertEqual((status, json.loads(body)["error"]), (429, "too_fast"))

        time.sleep(3.2)
        token = self._pair()
        self.assertGreaterEqual(len(token), 40)
        devices = json.loads((self.server.home / "devices.json").read_text())
        self.assertEqual(devices[-1]["name"], "iPhone di prova")
        self.assertNotIn(token, json.dumps(devices))  # solo l'impronta, mai il token
        self.assertEqual(oct((self.server.home / "devices.json").stat().st_mode & 0o777), "0o600")

        # token sbagliato: auth fallita e chiusura 4001
        ws = WS(self.port, "token-sbagliato")
        self.assertIn("101", ws.status_line)
        self.assertTrue(ws.accept_ok)
        self.assertEqual(ws.recv_json(), {"type": "auth", "ok": False})
        op, payload = ws.recv()
        self.assertEqual((op, struct.unpack(">H", payload[:2])[0]), (8, 4001))
        ws.close()

        # token giusto: hello, poi i comandi
        ws = WS(self.port, token)
        hello = ws.recv_json()
        self.assertEqual(hello["type"], "hello")
        self.assertIn("name", hello)
        ws.send({"type": "ping"})
        self.assertEqual(ws.recv_json(), {"type": "pong"})
        ws.send({"type": "click", "button": "left"})
        ws.send({"type": "key", "name": "left", "mods": ["cmd", "bogus"]})
        ws.send({"type": "text", "text": "ciao àè"})
        ws.send({"type": "media", "name": "volup"})
        ws.send({"type": "scroll", "dx": 0, "dy": 3.5})
        ws.send({"type": "button", "button": "right", "down": True})
        ws.send({"type": "cursor", "scale": 2})
        reply = ws.recv_json()
        self.assertEqual((reply["type"], reply["ok"]), ("cursor", True))
        # messaggi sbagliati: ignorati, la connessione resta viva
        ws.send(None, raw=b"non json")
        ws.send({"type": "key", "name": "tasto-che-non-esiste"})
        ws.send({"type": "move", "dx": "nan", "dy": 1})
        ws.send({"type": "click", "button": "middle"})
        ws.send({"type": "ping"})
        self.assertEqual(ws.recv_json(), {"type": "pong"})
        self.server.wait_line("key cmd+left")
        lines = self.server.prova_lines()
        self.assertIn("[prova] click left", lines)
        self.assertIn("[prova] key cmd+left", lines)
        self.assertIn('[prova] text "ciao àè"', lines)
        self.assertIn("[prova] media volup", lines)
        self.assertIn("[prova] scroll 0.0 3.5", lines)
        self.assertIn("[prova] button right down", lines)
        self.assertIn("[prova] cursor_scale 2.0", lines)
        self.assertNotIn("[prova] key tasto-che-non-esiste", lines)
        self.assertFalse([l for l in lines if "middle" in l])

        # chiusura con tasto premuto e cursore grande: viene rilasciato tutto
        ws.close()
        self.server.wait_line("button right up")
        self.server.wait_line("cursor_restore")

    def test_movimento_fluido(self):
        token = self._pair("iPhone fluido")
        ws = WS(self.port, token)
        ws.recv_json()
        before = len(self.server.prova_lines())
        for _ in range(10):
            ws.send({"type": "move", "dx": 20, "dy": -10})
            time.sleep(0.01)
        ws.send({"type": "click", "button": "left"})  # prima del clic il residuo va applicato tutto
        ws.send({"type": "ping"})
        ws.recv_json()
        self.server.wait_line("click left", timeout=5)
        moves = [l.split() for l in self.server.prova_lines()[before:] if l.startswith("[prova] move")]
        self.assertGreater(len(moves), 1, "i movimenti devono arrivare a piccoli passi, non in blocco")
        # quelli prima del clic sommano (quasi) tutto
        click_index = [i for i, l in enumerate(self.server.prova_lines()[before:]) if "click left" in l][0]
        before_click = [l.split() for l in self.server.prova_lines()[before:][:click_index] if l.startswith("[prova] move")]
        self.assertAlmostEqual(sum(float(m[2]) for m in before_click), 200.0, delta=1.0)
        self.assertAlmostEqual(sum(float(m[3]) for m in before_click), -100.0, delta=1.0)
        ws.close()

    def test_richiesta_ws_senza_upgrade(self):
        status, _, body = http(self.base + "/ws")
        self.assertEqual(status, 400)


class RestartAndPorts(unittest.TestCase):
    @unittest.skipUnless(BINARY.exists(), "app non compilata")
    def test_dispositivi_e_certificati_restano_dopo_il_riavvio(self):
        home = Path(tempfile.mkdtemp(prefix="telemac-test-"))
        try:
            s1 = Server(home)
            port = s1.wait_event("ready")["appPort"]
            http(f"https://127.0.0.1:{port}/api/pair/start", "POST", b"{}")
            code = s1.next_code(0)
            _, _, body = http(f"https://127.0.0.1:{port}/api/pair/finish", "POST",
                              json.dumps({"code": code}).encode())
            token = json.loads(body)["token"]
            ca_before = (home / "ca.pem").read_bytes()
            cert_before = (home / "server.pem").read_bytes()
            s1.stop()

            s2 = Server(home)
            port2 = s2.wait_event("ready")["appPort"]
            self.assertEqual((home / "ca.pem").read_bytes(), ca_before)       # la CA non si rifà mai
            self.assertEqual((home / "server.pem").read_bytes(), cert_before)  # il certificato nemmeno
            ws = WS(port2, token)
            self.assertEqual(ws.recv_json()["type"], "hello")                  # il token vale ancora
            ws.close()
            s2.stop()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    @unittest.skipUnless(BINARY.exists(), "app non compilata")
    def test_porta_occupata(self):
        first = Server()
        try:
            port = first.wait_event("ready")["appPort"]
            second = Server(port=port)
            try:
                err = second.wait_event("error")
                self.assertEqual((err["code"], err["port"]), ("port_in_use", port))
                self.assertEqual(second.proc.wait(10), 3)
            finally:
                second.stop()
                shutil.rmtree(second.home, ignore_errors=True)
        finally:
            first.stop()
            shutil.rmtree(first.home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
