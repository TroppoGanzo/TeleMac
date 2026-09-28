#!/usr/bin/env python3
"""Avvio automatico di TeleMac al login (solo macOS), tramite un LaunchAgent.

Uso: python3 telemac/autostart.py install|uninstall|status

Le funzioni "pure" (costruzione del plist e dei percorsi) accettano `home=`
ed `executable=` per essere testabili senza toccare la vera home dell'utente
e senza chiamare `launchctl` (che qui non esiste nemmeno, non essendo macOS).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

LABEL = "io.github.troppoganzo.telemac"
REPO_ROOT = Path(__file__).resolve().parent.parent


def is_macos() -> bool:
    return sys.platform == "darwin"


def app_support_dir(home: Path) -> Path:
    """Cartella dove viene copiata l'app.

    I LaunchAgent girano in un contesto che non può leggere Desktop,
    Documenti o Download (privacy di macOS): per questo copiamo il codice in
    "Application Support" invece di eseguirlo da dove si trova il repository.
    """
    return Path(home) / "Library" / "Application Support" / "TeleMac" / "app"


def launch_agents_dir(home: Path) -> Path:
    return Path(home) / "Library" / "LaunchAgents"


def plist_path(home: Path) -> Path:
    return launch_agents_dir(home) / f"{LABEL}.plist"


def log_path(home: Path) -> Path:
    return Path(home) / "Library" / "Logs" / "TeleMac.log"


def build_plist(executable: str, app_dir: Path, log_file: Path) -> dict:
    """Costruisce il dizionario del LaunchAgent (nessun accesso al disco)."""
    server_py = str(Path(app_dir) / "telemac" / "server.py")
    return {
        "Label": LABEL,
        "ProgramArguments": [str(executable), server_py, "--background"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }


def write_plist(path: Path, plist: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist, f)


def copy_app(repo_root: Path, home: Path) -> Path:
    """Copia telemac/ e web/ nella cartella dati dell'app, escludendo __pycache__."""
    dest = app_support_dir(home)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("telemac", "web"):
        src = Path(repo_root) / name
        dst = dest / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=ignore)
    return dest


def _run_launchctl(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(home: Optional[Path] = None, executable: Optional[str] = None, repo_root: Optional[Path] = None) -> int:
    if not is_macos():
        print("L'avvio automatico usa i LaunchAgent e funziona solo su macOS.")
        return 1

    home = Path(home) if home else Path.home()
    executable = executable or sys.executable
    repo_root = Path(repo_root) if repo_root else REPO_ROOT

    app_dir = copy_app(repo_root, home)
    plist = build_plist(executable, app_dir, log_path(home))
    path = plist_path(home)
    write_plist(path, plist)

    uid = os.getuid()
    _run_launchctl("bootout", f"gui/{uid}/{LABEL}")  # ignora l'errore: forse non era installato
    result = _run_launchctl("bootstrap", f"gui/{uid}", str(path))

    print(f"Copiata l'app in: {app_dir}")
    print(f"Scritto il LaunchAgent: {path}")
    if result.returncode == 0:
        print("Avvio automatico installato: TeleMac partirà da solo a ogni accesso.")
    else:
        print("Il LaunchAgent è stato scritto, ma launchctl ha segnalato un problema:")
        print(f"  {result.stderr.strip()}")
    print(f"Log del server in: {log_path(home)}")
    print()
    print('Al primo avvio macOS chiederà il permesso di Accessibilità per "Python":')
    print("Impostazioni di Sistema → Privacy e sicurezza → Accessibilità → attivalo.")
    return 0


def uninstall(home: Optional[Path] = None) -> int:
    if not is_macos():
        print("L'avvio automatico usa i LaunchAgent e funziona solo su macOS.")
        return 1

    home = Path(home) if home else Path.home()
    uid = os.getuid()
    _run_launchctl("bootout", f"gui/{uid}/{LABEL}")  # ignora l'errore

    path = plist_path(home)
    if path.exists():
        path.unlink()
        print(f"Rimosso il LaunchAgent: {path}")
    else:
        print("L'avvio automatico non era installato (nessun LaunchAgent trovato).")
    print(f"La copia dell'app resta in {app_support_dir(home)}: non viene cancellata.")
    return 0


def status(home: Optional[Path] = None) -> int:
    if not is_macos():
        print("L'avvio automatico usa i LaunchAgent e funziona solo su macOS.")
        return 1

    home = Path(home) if home else Path.home()
    path = plist_path(home)
    if not path.exists():
        print("Avvio automatico: non installato.")
        return 0

    uid = os.getuid()
    result = _run_launchctl("print", f"gui/{uid}/{LABEL}")
    if result.returncode == 0:
        print(f"Avvio automatico: installato e attivo ({path}).")
    else:
        print(f"Avvio automatico: il LaunchAgent esiste ({path}) ma launchctl non lo vede in esecuzione.")
    return 0


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    azioni = {"install": install, "uninstall": uninstall, "status": status}
    if len(argv) != 1 or argv[0] not in azioni:
        print("Uso: python3 telemac/autostart.py install|uninstall|status")
        return 2
    return azioni[argv[0]]()


if __name__ == "__main__":
    sys.exit(main())
