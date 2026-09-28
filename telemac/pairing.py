"""Abbinamento con codice a 6 cifre (come Apple TV) e archivio dei dispositivi
abbinati (token permanenti salvati sul Mac).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple


class PairingManager:
    """Gestisce il codice di abbinamento corrente: un solo codice alla volta.

    Una volta che il codice "termina" (successo, scadenza scoperta o blocco
    per troppi tentativi sbagliati) resta in quello stato finché non si chiama
    di nuovo `start()`, cosa che genera un codice nuovo.

    Le richieste arrivano da thread diversi: tutto passa da un lucchetto, se no
    tentativi in parallelo scavalcherebbero il limite di `max_attempts`. Ogni
    codice bloccato raddoppia l'attesa prima del successivo (fino a
    `max_backoff`), così provare tutti i codici a forza richiederebbe giorni.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 120,
        max_attempts: int = 5,
        min_interval: float = 3.0,
        on_code: Optional[Callable[[str, float], None]] = None,
        on_done: Optional[Callable[[], None]] = None,
        max_backoff: float = 300.0,
    ):
        self._lock = threading.Lock()
        self._clock = clock
        self._ttl = ttl
        self._max_attempts = max_attempts
        self._min_interval = min_interval
        self._on_code = on_code
        self._on_done = on_done
        self._max_backoff = max_backoff
        self._lockouts = 0  # codici bloccati di fila (si azzera a ogni successo)

        self._code: Optional[str] = None
        self._expires_at = 0.0
        self._attempts_left = 0
        # None = codice attivo (o nessun codice mai creato); "locked"/"expired"
        # = il codice esiste ancora ma non è più utilizzabile.
        self._terminal: Optional[str] = None
        self._last_call: Optional[float] = None

    def _current_interval(self) -> float:
        return min(self._max_backoff, self._min_interval * (2 ** self._lockouts))

    def start(self) -> Tuple[bool, dict]:
        with self._lock:
            return self._start()

    def _start(self) -> Tuple[bool, dict]:
        now = self._clock()
        # Il limite si applica a OGNI chiamata (anche per rimostrare un codice
        # già valido): altrimenti toccare due volte di fretta il pulsante sul
        # telefono aprirebbe due finestre di dialogo sul Mac.
        if self._last_call is not None and (now - self._last_call) < self._current_interval():
            return False, {"error": "too_fast"}
        self._last_call = now

        active = self._code is not None and self._terminal is None and now < self._expires_at
        if active:
            seconds_left = max(0, int(round(self._expires_at - now)))
            self._fire_on_code(self._code, seconds_left)
            return True, {"expires_in": seconds_left}

        self._code = "{:06d}".format(secrets.randbelow(10 ** 6))
        self._expires_at = now + self._ttl
        self._attempts_left = self._max_attempts
        self._terminal = None
        self._fire_on_code(self._code, self._ttl)
        return True, {"expires_in": self._ttl}

    def finish(self, code: str) -> Tuple[bool, Optional[str], Optional[int]]:
        with self._lock:
            return self._finish(code)

    def _finish(self, code: str) -> Tuple[bool, Optional[str], Optional[int]]:
        if self._code is None:
            return False, "no_code", None
        if self._terminal == "locked":
            return False, "locked", None
        if self._terminal == "expired":
            return False, "expired", None

        now = self._clock()
        if now >= self._expires_at:
            self._terminal = "expired"
            self._fire_on_done()
            return False, "expired", None

        # Solo 6 cifre ASCII: compare_digest solleva eccezioni con testo non ASCII.
        candidate = code if isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code) else "x"
        if secrets.compare_digest(candidate, self._code):
            self._code = None
            self._expires_at = 0.0
            self._attempts_left = 0
            self._terminal = None
            self._lockouts = 0
            self._fire_on_done()
            return True, None, None

        self._attempts_left -= 1
        if self._attempts_left <= 0:
            self._terminal = "locked"
            self._lockouts += 1
            self._fire_on_done()
            return False, "locked", 0
        return False, "wrong_code", self._attempts_left

    def _fire_on_code(self, code: str, seconds_left: float) -> None:
        if self._on_code is None:
            return
        try:
            self._on_code(code, seconds_left)
        except Exception:
            pass

    def _fire_on_done(self) -> None:
        if self._on_done is None:
            return
        try:
            self._on_done()
        except Exception:
            pass


class DeviceStore:
    """Elenco dei dispositivi abbinati: salviamo solo l'hash del token, mai il
    token in chiaro. File JSON con scrittura atomica e permessi 600."""

    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()  # add() da thread diversi non deve perdere dispositivi

    def _load(self) -> list:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, devices: list) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), prefix=".tmp-devices-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(devices, f)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, str(self._path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def add(self, name: str) -> str:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            devices = self._load()
            devices.append({
                "name": name,
                "token_sha256": digest,
                "added_at": datetime.now(timezone.utc).isoformat(),
            })
            self._save(devices)
        return token

    def verify(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for device in self._load():
            stored = device.get("token_sha256", "")
            if isinstance(stored, str) and secrets.compare_digest(digest, stored):
                return device
        return None

    def clear(self) -> None:
        with self._lock:
            self._save([])

    def count(self) -> int:
        return len(self._load())

    def devices(self) -> list:
        """Nome e data di abbinamento di ogni dispositivo (mai l'hash del token)."""
        out = []
        for device in self._load():
            if isinstance(device, dict):
                out.append({
                    "name": str(device.get("name", "iPhone")),
                    "addedAt": str(device.get("added_at", "")),
                })
        return out
