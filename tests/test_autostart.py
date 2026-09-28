"""Test di telemac/autostart.py.

Qui giriamo su Linux: `launchctl` non esiste. Testiamo solo le funzioni pure
(percorsi, costruzione del plist) passando `home=`/`executable=`/`repo_root=`,
e verifichiamo che il comando reale, su un sistema non-macOS, si fermi subito
con un messaggio chiaro (exit 1) senza toccare il disco.
"""

from __future__ import annotations

import io
import plistlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

import autostart  # noqa: E402


def _silent(fn, *args, **kwargs):
    """Chiama fn sopprimendo lo stdout, ritorna (risultato, testo_stampato)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


class TestPercorsi(unittest.TestCase):
    def test_app_support_dir(self):
        home = Path("/tmp/fake-home")
        self.assertEqual(
            autostart.app_support_dir(home),
            home / "Library" / "Application Support" / "TeleMac" / "app",
        )

    def test_launch_agents_dir(self):
        home = Path("/tmp/fake-home")
        self.assertEqual(autostart.launch_agents_dir(home), home / "Library" / "LaunchAgents")

    def test_plist_path_usa_la_label(self):
        home = Path("/tmp/fake-home")
        path = autostart.plist_path(home)
        self.assertEqual(path.parent, autostart.launch_agents_dir(home))
        self.assertEqual(path.name, f"{autostart.LABEL}.plist")

    def test_log_path(self):
        home = Path("/tmp/fake-home")
        self.assertEqual(autostart.log_path(home), home / "Library" / "Logs" / "TeleMac.log")


class TestBuildPlist(unittest.TestCase):
    def test_contenuto_plist(self):
        app_dir = Path("/tmp/fake-home/Library/Application Support/TeleMac/app")
        log_file = Path("/tmp/fake-home/Library/Logs/TeleMac.log")
        plist = autostart.build_plist("/usr/bin/python3", app_dir, log_file)

        self.assertEqual(plist["Label"], "io.github.troppoganzo.telemac")
        self.assertEqual(
            plist["ProgramArguments"],
            ["/usr/bin/python3", str(app_dir / "telemac" / "server.py"), "--background"],
        )
        self.assertIs(plist["RunAtLoad"], True)
        self.assertIs(plist["KeepAlive"], True)
        self.assertEqual(plist["ProcessType"], "Interactive")
        self.assertEqual(plist["StandardOutPath"], str(log_file))
        self.assertEqual(plist["StandardErrorPath"], str(log_file))
        self.assertEqual(plist["EnvironmentVariables"], {"PYTHONUNBUFFERED": "1"})

    def test_label_esatta(self):
        # La label deve combaciare con gli identificatori usati altrove nel
        # progetto (es. certs.py / mobileconfig): io.github.troppoganzo.telemac
        self.assertEqual(autostart.LABEL, "io.github.troppoganzo.telemac")


class TestWritePlist(unittest.TestCase):
    def test_scrive_un_plist_valido(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = autostart.plist_path(home)
            plist = autostart.build_plist(sys.executable, autostart.app_support_dir(home), autostart.log_path(home))
            autostart.write_plist(path, plist)

            self.assertTrue(path.is_file())
            with open(path, "rb") as f:
                reletto = plistlib.load(f)
            self.assertEqual(reletto, plist)


class TestCopyApp(unittest.TestCase):
    def _crea_repo_finto(self, root: Path):
        telemac = root / "telemac"
        web = root / "web"
        telemac.mkdir()
        web.mkdir()
        (telemac / "server.py").write_text("# finto server\n")
        (telemac / "__pycache__").mkdir()
        (telemac / "__pycache__" / "server.cpython-39.pyc").write_bytes(b"\x00\x01")
        (web / "index.html").write_text("<html></html>")

    def test_copia_ignorando_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home = Path(tmp) / "home"
            repo_root.mkdir()
            (Path(tmp) / "home").mkdir()
            self._crea_repo_finto(repo_root)

            dest = autostart.copy_app(repo_root, home)

            self.assertEqual(dest, autostart.app_support_dir(home))
            self.assertTrue((dest / "telemac" / "server.py").is_file())
            self.assertTrue((dest / "web" / "index.html").is_file())
            self.assertFalse((dest / "telemac" / "__pycache__").exists())

    def test_copia_di_nuovo_sovrascrive(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home = Path(tmp) / "home"
            repo_root.mkdir()
            home.mkdir()
            self._crea_repo_finto(repo_root)

            autostart.copy_app(repo_root, home)
            (repo_root / "telemac" / "server.py").write_text("# versione nuova\n")
            dest = autostart.copy_app(repo_root, home)

            self.assertEqual((dest / "telemac" / "server.py").read_text(), "# versione nuova\n")


class TestNonMacos(unittest.TestCase):
    """Qui siamo su Linux: install/uninstall/status devono uscire subito."""

    def test_install_su_non_macos(self):
        with mock.patch.object(autostart.sys, "platform", "linux"):
            result, out = _silent(autostart.install)
        self.assertEqual(result, 1)
        self.assertIn("macOS", out)

    def test_uninstall_su_non_macos(self):
        with mock.patch.object(autostart.sys, "platform", "linux"):
            result, out = _silent(autostart.uninstall)
        self.assertEqual(result, 1)
        self.assertIn("macOS", out)

    def test_status_su_non_macos(self):
        with mock.patch.object(autostart.sys, "platform", "linux"):
            result, out = _silent(autostart.status)
        self.assertEqual(result, 1)
        self.assertIn("macOS", out)

    def test_non_tocca_la_home_reale(self):
        # Nessuna delle tre funzioni deve creare nulla se non è macOS: lo
        # verifichiamo passando una home temporanea e controllando che resti
        # vuota (il controllo piattaforma deve avvenire PRIMA di ogni I/O).
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf), mock.patch.object(autostart.sys, "platform", "linux"):
                autostart.install(home=home)
                autostart.uninstall(home=home)
                autostart.status(home=home)
            self.assertEqual(list(home.iterdir()), [])

    def test_main_uso_senza_argomenti(self):
        result, out = _silent(autostart.main, [])
        self.assertEqual(result, 2)
        self.assertIn("Uso:", out)

    def test_main_argomento_sconosciuto(self):
        result, out = _silent(autostart.main, ["pippo"])
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
