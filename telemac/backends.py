"""Backend che eseguono i comandi: quello vero per macOS e uno "a secco" per prove."""

from __future__ import annotations

import sys
from typing import Optional


class DryRunBackend:
    """Non tocca niente: stampa soltanto i comandi ricevuti. Utile per provare l'interfaccia."""

    def __init__(self, out=None):
        self.out = out or sys.stdout

    def _log(self, *parts):
        print("  [prova]", *parts, file=self.out, flush=True)

    def move(self, dx, dy):
        self._log("move", round(dx, 1), round(dy, 1))

    def scroll(self, dx, dy):
        self._log("scroll", round(dx, 1), round(dy, 1))

    def click(self, button):
        self._log("click", button)

    def button(self, button, down):
        self._log("button", button, "down" if down else "up")

    def key(self, name, mods):
        self._log("key", "+".join(list(mods) + [name]))

    def text(self, s):
        self._log("text", repr(s))

    def media(self, name):
        self._log("media", name)

    def display_sleep(self):
        self._log("display_sleep")

    def cursor_scale(self, scale):
        self._log("cursor_scale", scale)
        return True

    def cursor_restore(self):
        self._log("cursor_restore")


def create_backend(dry_run=False):
    if dry_run or sys.platform != "darwin":
        return DryRunBackend()
    import macinput

    macinput.hide_from_dock()
    return macinput.MacBackend()


def accessibility_status(prompt: bool = False) -> Optional[bool]:
    """Stato del permesso di Accessibilità, o None se non ha senso chiederlo
    (non siamo su macOS). Importa 'macinput' solo qui dentro: negli altri
    moduli e nei test (che girano su Linux) non deve mai essere importato."""
    if sys.platform != "darwin":
        return None
    try:
        import macinput
    except Exception:
        return None
    try:
        return bool(macinput.is_trusted(prompt))
    except Exception:
        return None
