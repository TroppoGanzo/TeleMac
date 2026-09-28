"""Informazioni sulla rete e sul Mac: nome host, nome del computer, IP della LAN.

Tutte le funzioni sono pensate per non sollevare mai eccezioni: se qualcosa va
storto (comando assente, rete non disponibile, ...) restituiscono un valore di
ripiego ragionevole invece di far cadere il server.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
from typing import Optional

_VALID_CHARS = re.compile(r"[^A-Za-z0-9-]")


def _scutil_get(key: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["scutil", "--get", key],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.decode("utf-8", "replace").strip()
    return value or None


def _clean_label(name: str) -> str:
    return _VALID_CHARS.sub("", name)


def local_hostname() -> str:
    """Nome ".local" usato per il certificato e per il banner (es. Mac-di-Mario.local)."""
    try:
        if sys.platform == "darwin":
            name = _scutil_get("LocalHostName")
            if name:
                cleaned = _clean_label(name)
                if cleaned:
                    return cleaned if cleaned.endswith(".local") else cleaned + ".local"
        name = socket.gethostname() or ""
        cleaned = _clean_label(name)
        if not cleaned:
            return "localhost"
        return cleaned if cleaned.endswith(".local") else cleaned + ".local"
    except Exception:
        return "localhost"


def computer_name() -> str:
    """Nome "umano" del Mac (es. "Mac di Mario"), quello che vede l'utente."""
    try:
        if sys.platform == "darwin":
            name = _scutil_get("ComputerName")
            if name:
                return name
        name = socket.gethostname()
        return name or "Mac"
    except Exception:
        return "Mac"


def lan_ipv4() -> Optional[str]:
    """IP della rete di casa, o None se non riusciamo a determinarne uno privato.

    Trucco classico: apriamo un socket UDP "verso" 8.8.8.8 (nessun pacchetto
    viene davvero inviato) solo per far scegliere al sistema operativo
    l'interfaccia di uscita, e ne leggiamo l'indirizzo locale.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_loopback:
        return None
    if not addr.is_private:
        return None
    return ip
