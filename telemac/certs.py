"""CA locale + certificato server compatibili con iOS, generati con 'openssl'
(su macOS è LibreSSL: usiamo solo le opzioni che capisce anche lui).

La CA si crea una volta sola e non si rigenera mai (altrimenti l'utente
dovrebbe reinstallare il profilo sull'iPhone ogni volta). Il certificato del
server invece si rigenera quando manca, sta per scadere o non copre più tutti
gli host/IP richiesti (es. il Mac ha cambiato indirizzo IP).
"""

from __future__ import annotations

import ipaddress
import json
import os
import plistlib
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, NamedTuple, Optional

# Spazio dei nomi arbitrario (ma fisso) usato per derivare gli UUID del profilo
# in modo stabile dall'impronta della CA: stesso Mac, stesso profilo, sempre.
_UUID_NAMESPACE = uuid.UUID("2f6f4b2a-8a3e-4b7a-9d3a-6a3f0b8e2c4d")

CA_CERT_NAME = "ca.pem"
CA_KEY_NAME = "ca.key"
SERVER_CERT_NAME = "server.pem"
SERVER_KEY_NAME = "server.key"
META_NAME = "server.json"

CA_DAYS = 3650
SERVER_DAYS = 397
RENEW_MARGIN_DAYS = 30

# Reti private a cui la CA è limitata (name constraints) e uniche IP che
# accettiamo nel subjectAltName del certificato server.
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
]

_NAME_CONSTRAINTS = (
    "permitted;DNS:local, permitted;DNS:localhost, "
    "permitted;IP:10.0.0.0/255.0.0.0, permitted;IP:172.16.0.0/255.240.0.0, "
    "permitted;IP:192.168.0.0/255.255.0.0, permitted;IP:169.254.0.0/255.255.0.0, "
    "permitted;IP:100.64.0.0/255.192.0.0, permitted;IP:127.0.0.0/255.0.0.0"
)


class CertPaths(NamedTuple):
    ca_cert: Path
    ca_key: Path
    server_cert: Path
    server_key: Path


def _paths(state_dir: Path) -> CertPaths:
    return CertPaths(
        ca_cert=state_dir / CA_CERT_NAME,
        ca_key=state_dir / CA_KEY_NAME,
        server_cert=state_dir / SERVER_CERT_NAME,
        server_key=state_dir / SERVER_KEY_NAME,
    )


def _openssl_bin() -> str:
    return os.environ.get("TELEMAC_OPENSSL", "openssl")


def _run_openssl(args: List[str]) -> bytes:
    proc = subprocess.run(
        [_openssl_bin()] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "openssl " + " ".join(args) + " fallito: "
            + proc.stderr.decode("utf-8", "replace").strip()
        )
    return proc.stdout


def _is_allowed_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _PRIVATE_NETS)


def _dedup(items) -> List[str]:
    seen: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _generate_key(path: Path) -> None:
    _run_openssl(["genrsa", "-out", str(path), "2048"])
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _random_serial_hex() -> str:
    raw = bytearray(secrets.token_bytes(16))
    raw[0] &= 0x7F  # bit più alto a 0: alcuni openssl trattano un MSB=1 come negativo
    if raw[0] == 0:
        raw[0] = 0x01
    return "0x" + bytes(raw).hex()


def _write_json_atomic(path: Path, obj) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_meta(state_dir: Path) -> Optional[dict]:
    p = state_dir / META_NAME
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _ca_config(cn_host: str) -> str:
    return (
        "[req]\n"
        "distinguished_name = dn\n"
        "prompt = no\n"
        "[dn]\n"
        f"CN = TeleMac CA ({cn_host})\n"
        "O = TeleMac\n"
        "[v3_ca]\n"
        "basicConstraints = critical, CA:TRUE, pathlen:0\n"
        "keyUsage = critical, keyCertSign, cRLSign\n"
        "subjectKeyIdentifier = hash\n"
        f"nameConstraints = {_NAME_CONSTRAINTS}\n"
    )


def _csr_config(cn: str) -> str:
    return (
        "[req]\n"
        "distinguished_name = dn\n"
        "prompt = no\n"
        "[dn]\n"
        f"CN = {cn}\n"
    )


def _leaf_config(hostnames: List[str], ips: List[str]) -> str:
    lines = [
        "[v3_leaf]",
        "basicConstraints = critical, CA:FALSE",
        "keyUsage = critical, digitalSignature, keyEncipherment",
        "extendedKeyUsage = serverAuth",
        "subjectAltName = @alt_names",
        "authorityKeyIdentifier = keyid",
        "",
        "[alt_names]",
    ]
    for i, host in enumerate(hostnames, 1):
        lines.append(f"DNS.{i} = {host}")
    for i, ip in enumerate(ips, 1):
        lines.append(f"IP.{i} = {ip}")
    lines.append("")
    return "\n".join(lines)


def _create_ca(state_dir: Path, hostnames: List[str]) -> None:
    paths = _paths(state_dir)
    first_host = hostnames[0] if hostnames else "TeleMac"
    _generate_key(paths.ca_key)
    with tempfile.TemporaryDirectory() as tmp:
        cnf = Path(tmp) / "ca.cnf"
        cnf.write_text(_ca_config(first_host), encoding="utf-8")
        _run_openssl([
            "req", "-new", "-x509",
            "-key", str(paths.ca_key),
            "-config", str(cnf),
            "-extensions", "v3_ca",
            "-days", str(CA_DAYS),
            "-sha256",
            "-out", str(paths.ca_cert),
        ])


def _create_server_cert(state_dir: Path, hostnames: List[str], ips: List[str]) -> None:
    paths = _paths(state_dir)
    hostnames = hostnames or ["localhost"]
    cn = hostnames[0]
    _generate_key(paths.server_key)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        csr_cnf = tmp_path / "csr.cnf"
        csr_cnf.write_text(_csr_config(cn), encoding="utf-8")
        csr_path = tmp_path / "server.csr"
        _run_openssl([
            "req", "-new",
            "-key", str(paths.server_key),
            "-config", str(csr_cnf),
            "-out", str(csr_path),
        ])
        leaf_cnf = tmp_path / "leaf.cnf"
        leaf_cnf.write_text(_leaf_config(hostnames, ips), encoding="utf-8")
        _run_openssl([
            "x509", "-req",
            "-in", str(csr_path),
            "-CA", str(paths.ca_cert),
            "-CAkey", str(paths.ca_key),
            "-set_serial", _random_serial_hex(),
            "-days", str(SERVER_DAYS),
            "-sha256",
            "-extfile", str(leaf_cnf),
            "-extensions", "v3_leaf",
            "-out", str(paths.server_cert),
        ])
    not_after = time.time() + SERVER_DAYS * 86400
    _write_json_atomic(state_dir / META_NAME, {
        "hostnames": hostnames,
        "ips": ips,
        "not_after": not_after,
    })


def ensure_certificates(state_dir: Path, hostnames: List[str], ips: List[str]) -> CertPaths:
    """Crea (se serve) la CA e il certificato server, e ne restituisce i percorsi."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(state_dir)

    wanted_hosts = _dedup(hostnames)
    wanted_ips = _dedup(ip for ip in ips if _is_allowed_ip(ip))

    if not paths.ca_cert.exists() or not paths.ca_key.exists():
        _create_ca(state_dir, wanted_hosts or ["TeleMac"])

    meta = _load_meta(state_dir)
    needs_new = not paths.server_cert.exists() or not paths.server_key.exists() or meta is None
    if not needs_new:
        not_after = meta.get("not_after", 0)
        if not isinstance(not_after, (int, float)) or not_after < time.time() + RENEW_MARGIN_DAYS * 86400:
            needs_new = True
        meta_hosts = set(meta.get("hostnames") or [])
        meta_ips = set(meta.get("ips") or [])
        if not (set(wanted_hosts) <= meta_hosts and set(wanted_ips) <= meta_ips):
            needs_new = True

    if needs_new:
        _create_server_cert(state_dir, wanted_hosts, wanted_ips)

    return paths


def ca_fingerprint(state_dir: Path) -> str:
    """Impronta SHA-256 della CA, in maiuscolo, a coppie separate da ':'."""
    state_dir = Path(state_dir)
    out = _run_openssl([
        "x509", "-in", str(_paths(state_dir).ca_cert), "-noout", "-fingerprint", "-sha256",
    ])
    text = out.decode("ascii", "replace").strip()
    _, _, value = text.partition("=")
    return value.strip().upper()


def ca_der(state_dir: Path) -> bytes:
    """Certificato della CA in formato DER (quello che si scarica come .cer)."""
    state_dir = Path(state_dir)
    return _run_openssl(["x509", "-in", str(_paths(state_dir).ca_cert), "-outform", "der"])


def mobileconfig(state_dir: Path, computer_name: str) -> bytes:
    """Profilo .mobileconfig (non firmato) che installa solo la CA come radice fidata."""
    state_dir = Path(state_dir)
    fp = ca_fingerprint(state_dir)
    der = ca_der(state_dir)
    profile_uuid = str(uuid.uuid5(_UUID_NAMESPACE, fp + "|profile"))
    ca_uuid = str(uuid.uuid5(_UUID_NAMESPACE, fp + "|ca"))
    root = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": "io.github.troppoganzo.telemac.profile",
        "PayloadUUID": profile_uuid,
        "PayloadDisplayName": "TeleMac – " + computer_name,
        "PayloadDescription": (
            "Permette all'iPhone di collegarsi in modo sicuro a TeleMac sul tuo Mac. "
            "Vale solo per i dispositivi della rete di casa."
        ),
        "PayloadOrganization": "TeleMac",
        "PayloadContent": [
            {
                "PayloadType": "com.apple.security.root",
                "PayloadVersion": 1,
                "PayloadIdentifier": "io.github.troppoganzo.telemac.ca",
                "PayloadUUID": ca_uuid,
                "PayloadCertificateFileName": "TeleMac-CA.cer",
                "PayloadDisplayName": "TeleMac CA",
                "PayloadContent": der,
            }
        ],
    }
    return plistlib.dumps(root)
