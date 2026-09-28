"""Test che non toccano un vero Mac: usano il backend 'a secco' e verificano la logica pura."""

import io
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

from backends import DryRunBackend
from icon import make_icon_png
from keys import EXTRA_FLAGS, KEYCODES, MEDIA_KEYS, MODIFIERS, MOUSE_BUTTONS
from qr import render_terminal
from server import handle_message


class RecordingBackend(DryRunBackend):
    def __init__(self):
        super().__init__(out=io.StringIO())
        self.calls = []

    def move(self, dx, dy):
        self.calls.append(("move", dx, dy))

    def scroll(self, dx, dy):
        self.calls.append(("scroll", dx, dy))

    def click(self, button):
        self.calls.append(("click", button))

    def button(self, button, down):
        self.calls.append(("button", button, down))

    def key(self, name, mods):
        self.calls.append(("key", name, tuple(mods)))

    def text(self, s):
        self.calls.append(("text", s))

    def media(self, name):
        self.calls.append(("media", name))

    def display_sleep(self):
        self.calls.append(("sleep",))


class TestKeyTables(unittest.TestCase):
    def test_extra_flags_only_reference_known_keys(self):
        for name in EXTRA_FLAGS:
            self.assertIn(name, KEYCODES, f"{name} in EXTRA_FLAGS ma non in KEYCODES")

    def test_modifiers_have_distinct_flags(self):
        flags = [flag for _, flag in MODIFIERS.values()]
        self.assertEqual(len(flags), len(set(flags)))

    def test_letters_and_digits_present(self):
        for ch in "abcdefghijklmnopqrstuvwxyz0123456789":
            self.assertIn(ch, KEYCODES)


class TestQr(unittest.TestCase):
    def test_render_terminal_is_square_ish_and_nonempty(self):
        art = render_terminal("https://example.com/?t=abc123")
        lines = art.splitlines()
        self.assertGreater(len(lines), 5)
        self.assertTrue(all(len(l) == len(lines[0]) for l in lines))


class TestIcon(unittest.TestCase):
    def test_png_signature(self):
        data = make_icon_png(64)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(data), 100)


class TestMessageHandling(unittest.TestCase):
    def setUp(self):
        self.backend = RecordingBackend()
        self.lock = threading.Lock()

    def send(self, obj):
        handle_message(self.backend, self.lock, json.dumps(obj))

    def test_move(self):
        self.send({"type": "move", "dx": 1.5, "dy": -2})
        self.assertEqual(self.backend.calls, [("move", 1.5, -2.0)])

    def test_click_rejects_unknown_button(self):
        self.send({"type": "click", "button": "middle"})
        self.assertEqual(self.backend.calls, [])

    def test_click_accepts_known_button(self):
        self.send({"type": "click", "button": "left"})
        self.assertEqual(self.backend.calls, [("click", "left")])

    def test_key_drops_unknown_modifier(self):
        self.send({"type": "key", "name": "a", "mods": ["cmd", "hyper"]})
        self.assertEqual(self.backend.calls, [("key", "a", ("cmd",))])

    def test_key_rejects_unknown_name(self):
        self.send({"type": "key", "name": "doesnotexist", "mods": []})
        self.assertEqual(self.backend.calls, [])

    def test_media_rejects_unknown_name(self):
        self.send({"type": "media", "name": "teleport"})
        self.assertEqual(self.backend.calls, [])

    def test_text_is_truncated(self):
        self.send({"type": "text", "text": "a" * 5000})
        self.assertEqual(self.backend.calls, [("text", "a" * 2000)])

    def test_malformed_json_is_ignored(self):
        handle_message(self.backend, self.lock, "{not json")
        self.assertEqual(self.backend.calls, [])

    def test_missing_field_is_ignored(self):
        self.send({"type": "move", "dx": 1.0})  # manca dy
        self.assertEqual(self.backend.calls, [])

    def test_sleep(self):
        self.send({"type": "sleep"})
        self.assertEqual(self.backend.calls, [("sleep",)])


if __name__ == "__main__":
    unittest.main()
