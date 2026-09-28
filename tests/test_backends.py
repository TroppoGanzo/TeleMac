"""Test di telemac/backends.py: solo la parte 'a secco' + accessibility_status
(che su Linux deve sempre restituire None, senza mai importare macinput)."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

from backends import DryRunBackend, accessibility_status, create_backend  # noqa: E402


class TestDryRunBackend(unittest.TestCase):
    def test_non_solleva_per_nessun_comando(self):
        out = io.StringIO()
        b = DryRunBackend(out=out)
        b.move(1, 2)
        b.scroll(3, 4)
        b.click("left")
        b.button("right", True)
        b.key("a", ["cmd"])
        b.text("ciao")
        b.media("play")
        b.display_sleep()
        self.assertIn("move", out.getvalue())


class TestCreateBackend(unittest.TestCase):
    def test_dry_run_restituisce_dry_run_backend(self):
        self.assertIsInstance(create_backend(dry_run=True), DryRunBackend)

    def test_su_linux_senza_dry_run_restituisce_comunque_dry_run(self):
        # sys.platform non è 'darwin' qui: create_backend non deve mai
        # importare macinput (che richiede librerie macOS assenti su Linux).
        self.assertIsInstance(create_backend(dry_run=False), DryRunBackend)


class TestAccessibilityStatus(unittest.TestCase):
    def test_none_su_non_macos(self):
        self.assertIsNone(accessibility_status(prompt=False))
        self.assertIsNone(accessibility_status(prompt=True))

    def test_non_importa_macinput_su_linux(self):
        self.assertNotIn("macinput", sys.modules)
        accessibility_status(prompt=False)
        self.assertNotIn("macinput", sys.modules)


if __name__ == "__main__":
    unittest.main()
