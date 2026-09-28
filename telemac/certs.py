"""Certificato TLS autofirmato, generato una volta sola con 'openssl' (già presente su macOS)
e riusato alle volte successive. Serve un contesto sicuro (https) perché Safari su iOS
concede l'accesso al giroscopio solo in pagine servite in modo sicuro.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

STATE_DIR = Path.home() / ".telemac"
CERT_FILE = STATE_DIR / "cert.pem"
KEY_FILE = STATE_DIR / "key.pem"


def ensure_certificate() -> tuple[Path, Path]:
    STATE_DIR.mkdir(exist_ok=True)
    if CERT_FILE.exists() and KEY_FILE.exists():
        return CERT_FILE, KEY_FILE
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
            "-days", "3650", "-nodes",
            "-subj", "/CN=telemac.local",
            "-addext", "subjectAltName=DNS:telemac.local,DNS:localhost",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    KEY_FILE.chmod(0o600)
    return CERT_FILE, KEY_FILE
