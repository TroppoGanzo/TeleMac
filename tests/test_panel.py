"""Test del pannello della finestra per Mac (telemac/panel.py) e della
modalità --app del server."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

import panel  # noqa: E402
import pairing  # noqa: E402
import server  # noqa: E402

TOKEN = "gettone-di-prova"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class PanelHarness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.web = base / "web"
        self.web.mkdir()
        (self.web / "panel.html").write_text("<html>pannello telemac</html>", encoding="utf-8")
        (self.web / "icon-192.png").write_bytes(b"\x89PNG finta")
        (self.web / "segreto.txt").write_text("non deve uscire", encoding="utf-8")
        self.clock = FakeClock()
        self.events = []
        self.shared = server.SharedState(emit=self.events.append, clock=self.clock)
        self.store = pairing.DeviceStore(base / "devices.json")
        self.qr_cache = panel.QrCache()

        def status():
            return panel.build_panel_status(
                shared=self.shared, device_store=self.store, computer_name="Mac di Edoardo",
                hostname="mac-di-edoardo.local", ip="192.168.1.20", app_port=8765, setup_port=8766,
                ca_fp="AA:BB", profile_path=base / "TeleMac.mobileconfig", qr_cache=self.qr_cache,
                clock=self.clock,
            )

        self.status = status
        handler = panel.make_panel_handler(
            token=TOKEN, status_provider=status, device_store=self.store, web_dir=self.web,
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def request(self, path, *, token=None, method="GET", host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        if token:
            req.add_header(panel.TOKEN_HEADER, token)
        if host:
            req.add_header("Host", host)
        if method == "POST":
            req.data = b""
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()


class TestPannelloHttp(unittest.TestCase):
    def setUp(self):
        self.h = PanelHarness()
        self.addCleanup(self.h.close)

    def test_pagina_solo_con_gettone(self):
        self.assertEqual(self.h.request("/")[0], 403)
        self.assertEqual(self.h.request("/?token=sbagliato")[0], 403)
        code, body = self.h.request(f"/?token={TOKEN}")
        self.assertEqual(code, 200)
        self.assertIn(b"pannello telemac", body)

    def test_stato_solo_con_gettone_nell_header(self):
        self.assertEqual(self.h.request("/api/status")[0], 403)
        self.assertEqual(self.h.request(f"/api/status?token={TOKEN}")[0], 403)
        code, body = self.h.request("/api/status", token=TOKEN)
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["computerName"], "Mac di Edoardo")
        self.assertEqual(data["setupUrl"], "http://192.168.1.20:8766/")
        self.assertTrue(data["qrSvg"].startswith("<svg"))

    def test_host_estraneo_rifiutato(self):
        # Difesa dal DNS rebinding: una pagina web che punta un suo dominio a 127.0.0.1.
        code, _ = self.h.request(f"/?token={TOKEN}", host=f"attacco.example:{self.h.port}")
        self.assertEqual(code, 403)
        code, _ = self.h.request("/api/status", token=TOKEN, host="attacco.example")
        self.assertEqual(code, 403)
        code, _ = self.h.request("/api/status", token=TOKEN, host=f"localhost:{self.h.port}")
        self.assertEqual(code, 200)

    def test_solo_i_file_statici_previsti(self):
        self.assertEqual(self.h.request("/icon-192.png")[0], 200)
        self.assertEqual(self.h.request("/segreto.txt", token=TOKEN)[0], 404)
        self.assertEqual(self.h.request("/../panel.py", token=TOKEN)[0], 404)

    def test_scollega_tutti(self):
        self.h.store.add("iPhone di Edo")
        self.h.store.add("iPad")
        self.assertEqual(self.h.request("/api/forget-devices", method="POST")[0], 403)
        self.assertEqual(self.h.store.count(), 2)
        code, body = self.h.request("/api/forget-devices", method="POST", token=TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["removed"], 2)
        self.assertEqual(self.h.store.count(), 0)


class TestStatoPannello(unittest.TestCase):
    def setUp(self):
        self.h = PanelHarness()
        self.addCleanup(self.h.close)

    def test_codice_visibile_finche_non_scade(self):
        self.h.shared.show_code("123456", 120)
        st = self.h.status()
        self.assertEqual(st["pairing"], {"code": "123456", "expiresIn": 120})
        self.h.clock.now += 119.5
        self.assertEqual(self.h.status()["pairing"]["expiresIn"], 1)
        self.h.clock.now += 1
        self.assertIsNone(self.h.status()["pairing"])

    def test_codice_nascosto_a_fine_abbinamento(self):
        self.h.shared.show_code("123456", 120)
        self.h.shared.hide_code()
        self.assertIsNone(self.h.status()["pairing"])
        self.assertEqual([e["event"] for e in self.h.events], ["pair_code", "pair_done"])

    def test_abbinato_mostrato_per_qualche_secondo(self):
        self.h.shared.paired("iPhone di Edo")
        self.assertEqual(self.h.status()["paired"], {"device": "iPhone di Edo"})
        self.assertEqual(self.h.events[-1], {"event": "paired", "device": "iPhone di Edo"})
        self.h.clock.now += panel.PAIRED_BANNER_SECONDS + 1
        self.assertIsNone(self.h.status()["paired"])

    def test_dispositivi_senza_hash(self):
        self.h.store.add("iPhone di Edo")
        devices = self.h.status()["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["name"], "iPhone di Edo")
        self.assertNotIn("token_sha256", json.dumps(devices))

    def test_senza_pagina_di_configurazione_niente_qr(self):
        st = panel.build_panel_status(
            shared=self.h.shared, device_store=self.h.store, computer_name="Mac", hostname="mac.local",
            ip=None, app_port=8765, setup_port=None, ca_fp="AA", profile_path=Path("/x"),
            qr_cache=panel.QrCache(),
        )
        self.assertIsNone(st["setupUrl"])
        self.assertIsNone(st["qrSvg"])


class TestModalitaApp(unittest.TestCase):
    """server.main(["--app"]): eventi su stdout e pannello su 127.0.0.1."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old = {k: os.environ.get(k) for k in ("TELEMAC_HOME", "TELEMAC_PANEL_TOKEN")}
        os.environ["TELEMAC_HOME"] = self.tmp.name
        os.environ["TELEMAC_PANEL_TOKEN"] = TOKEN
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_emitter_una_riga_json_per_evento(self):
        buf = io.StringIO()
        emit = server._make_emitter(buf)
        emit({"event": "paired", "device": "iPhone di Edo"})
        line = buf.getvalue()
        self.assertTrue(line.startswith(server.EVENT_PREFIX))
        self.assertEqual(json.loads(line[len(server.EVENT_PREFIX):]), {"event": "paired", "device": "iPhone di Edo"})

    def test_avvio_annuncia_il_pannello_e_si_ferma_a_fine_stdin(self):
        read_fd, write_fd = os.pipe()
        old_stdin = sys.stdin
        sys.stdin = os.fdopen(read_fd, "r")
        self.addCleanup(setattr, sys, "stdin", old_stdin)
        out = io.StringIO()
        result = {}

        def run():
            with redirect_stdout(out):
                result["code"] = server.main(["--app", "--dry-run", "--port", "0", "--setup-port", "0"])

        t = threading.Thread(target=run, daemon=True)
        t.start()
        ready = None
        for _ in range(100):
            for line in out.getvalue().splitlines():
                if line.startswith(server.EVENT_PREFIX):
                    event = json.loads(line[len(server.EVENT_PREFIX):])
                    if event.get("event") == "ready":
                        ready = event
            if ready:
                break
            t.join(0.1)
        self.assertIsNotNone(ready, out.getvalue())
        self.assertTrue(ready["panel"].startswith("http://127.0.0.1:"))
        self.assertIn("token=" + TOKEN, ready["panel"])
        req = urllib.request.Request(ready["panel"].split("?")[0] + "api/status")
        req.add_header(panel.TOKEN_HEADER, TOKEN)
        with urllib.request.urlopen(req, timeout=5) as res:
            self.assertTrue(json.loads(res.read())["dryRun"])

        os.close(write_fd)  # l'app si è chiusa: stdin finisce
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(result["code"], 0)
        sys.stdin.close()

    def test_porta_occupata_segnalata_all_app(self):
        import socket
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", 0))
        probe.listen(1)
        self.addCleanup(probe.close)
        out = io.StringIO()
        with redirect_stdout(out):
            code = server.main(["--app", "--dry-run", "--port", str(probe.getsockname()[1]), "--setup-port", "0"])
        self.assertEqual(code, server.EXIT_PORT_IN_USE)
        self.assertIn('"port_in_use"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
