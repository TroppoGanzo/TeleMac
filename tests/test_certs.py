"""Test di telemac/certs.py: CA locale + certificato server via 'openssl'.

Si saltano se 'openssl' non è disponibile nell'ambiente di test.
"""

from __future__ import annotations

import datetime
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

import certs  # noqa: E402

OPENSSL = shutil.which("openssl")


def _openssl(*args):
    return subprocess.run(
        [OPENSSL] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout


@unittest.skipUnless(OPENSSL, "openssl non disponibile in questo ambiente")
class TestEnsureCertificates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def test_crea_tutti_i_file_attesi(self):
        paths = certs.ensure_certificates(self.state, ["mac-di-prova.local"], ["192.168.1.20"])
        for p in (paths.ca_cert, paths.ca_key, paths.server_cert, paths.server_key):
            self.assertTrue(p.is_file(), f"manca {p}")
        self.assertTrue((self.state / "server.json").is_file())

    def test_permessi_delle_chiavi(self):
        paths = certs.ensure_certificates(self.state, ["mac.local"], [])
        self.assertEqual(paths.ca_key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(paths.server_key.stat().st_mode & 0o777, 0o600)

    def test_il_server_verifica_contro_la_ca(self):
        paths = certs.ensure_certificates(self.state, ["mac.local"], ["10.0.0.5"])
        out = subprocess.run(
            [OPENSSL, "verify", "-CAfile", str(paths.ca_cert), "-purpose", "sslserver", str(paths.server_cert)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(out.returncode, 0, out.stderr.decode())
        self.assertIn(b"OK", out.stdout)

    def test_validita_server_entro_398_giorni(self):
        paths = certs.ensure_certificates(self.state, ["mac.local"], [])
        start_text = _openssl("x509", "-in", str(paths.server_cert), "-noout", "-startdate").decode()
        end_text = _openssl("x509", "-in", str(paths.server_cert), "-noout", "-enddate").decode()
        start = _parse_openssl_date(start_text.split("=", 1)[1])
        end = _parse_openssl_date(end_text.split("=", 1)[1])
        self.assertLessEqual((end - start).days, 398)

    def test_validita_ca_e_di_dieci_anni(self):
        paths = certs.ensure_certificates(self.state, ["mac.local"], [])
        start_text = _openssl("x509", "-in", str(paths.ca_cert), "-noout", "-startdate").decode()
        end_text = _openssl("x509", "-in", str(paths.ca_cert), "-noout", "-enddate").decode()
        start = _parse_openssl_date(start_text.split("=", 1)[1])
        end = _parse_openssl_date(end_text.split("=", 1)[1])
        self.assertGreaterEqual((end - start).days, 3649)

    def test_san_contiene_hostname_e_ip(self):
        paths = certs.ensure_certificates(self.state, ["mac-di-prova.local", "altro-nome.local"], ["192.168.1.20"])
        text = _openssl("x509", "-in", str(paths.server_cert), "-noout", "-text").decode()
        self.assertIn("DNS:mac-di-prova.local", text)
        self.assertIn("DNS:altro-nome.local", text)
        self.assertIn("IP Address:192.168.1.20", text)

    def test_scarta_ip_pubblici(self):
        paths = certs.ensure_certificates(self.state, ["mac.local"], ["8.8.8.8", "192.168.1.5"])
        text = _openssl("x509", "-in", str(paths.server_cert), "-noout", "-text").decode()
        self.assertNotIn("8.8.8.8", text)
        self.assertIn("IP Address:192.168.1.5", text)

    def test_ca_non_e_critica_su_name_constraints(self):
        paths = certs.ensure_certificates(self.state, ["mac.local"], [])
        text = _openssl("x509", "-in", str(paths.ca_cert), "-noout", "-text").decode()
        self.assertIn("X509v3 Name Constraints", text)
        # la riga della name constraints non deve avere "critical"
        idx = text.index("X509v3 Name Constraints")
        snippet = text[idx: idx + 80]
        self.assertNotIn("critical", snippet)

    def test_basic_constraints_ca_true_critico(self):
        paths = certs.ensure_certificates(self.state, ["mac.local"], [])
        text = _openssl("x509", "-in", str(paths.ca_cert), "-noout", "-text").decode()
        idx = text.index("X509v3 Basic Constraints")
        snippet = text[idx: idx + 80]
        self.assertIn("critical", snippet)
        self.assertIn("CA:TRUE", snippet)

    def test_rigenera_il_server_se_cambia_lip_ma_non_la_ca(self):
        paths1 = certs.ensure_certificates(self.state, ["mac.local"], ["192.168.1.10"])
        ca_bytes_before = paths1.ca_cert.read_bytes()
        server_bytes_before = paths1.server_cert.read_bytes()

        paths2 = certs.ensure_certificates(self.state, ["mac.local"], ["192.168.1.99"])
        ca_bytes_after = paths2.ca_cert.read_bytes()
        server_bytes_after = paths2.server_cert.read_bytes()

        self.assertEqual(ca_bytes_before, ca_bytes_after, "la CA non deve mai rigenerarsi")
        self.assertNotEqual(server_bytes_before, server_bytes_after, "il server deve rigenerarsi con un IP nuovo")

        text = _openssl("x509", "-in", str(paths2.server_cert), "-noout", "-text").decode()
        self.assertIn("IP Address:192.168.1.99", text)
        self.assertNotIn("192.168.1.10", text)

    def test_non_rigenera_se_richiesto_un_sottoinsieme(self):
        certs.ensure_certificates(self.state, ["mac.local", "altro.local"], ["192.168.1.10"])
        server_before = (self.state / "server.pem").read_bytes()
        # richiediamo solo un sottoinsieme di quanto già presente: non deve rigenerare
        certs.ensure_certificates(self.state, ["mac.local"], [])
        server_after = (self.state / "server.pem").read_bytes()
        self.assertEqual(server_before, server_after)

    def test_ca_rifiuta_un_leaf_per_dominio_non_permesso(self):
        # La CA è già stata creata da ensure_certificates.
        certs.ensure_certificates(self.state, ["mac.local"], [])
        ca_cert = self.state / "ca.pem"
        ca_key = self.state / "ca.key"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            evil_key = tmp_path / "evil.key"
            _openssl("genrsa", "-out", str(evil_key), "2048")

            csr_cnf = tmp_path / "csr.cnf"
            csr_cnf.write_text(
                "[req]\ndistinguished_name = dn\nprompt = no\n[dn]\nCN = example.com\n", encoding="utf-8",
            )
            csr = tmp_path / "evil.csr"
            _openssl("req", "-new", "-key", str(evil_key), "-config", str(csr_cnf), "-out", str(csr))

            leaf_cnf = tmp_path / "leaf.cnf"
            leaf_cnf.write_text(
                "[v3_leaf]\n"
                "basicConstraints = critical, CA:FALSE\n"
                "keyUsage = critical, digitalSignature, keyEncipherment\n"
                "extendedKeyUsage = serverAuth\n"
                "subjectAltName = @alt_names\n"
                "authorityKeyIdentifier = keyid\n"
                "\n[alt_names]\nDNS.1 = example.com\n",
                encoding="utf-8",
            )
            evil_cert = tmp_path / "evil.pem"
            _openssl(
                "x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
                "-set_serial", "0x1234", "-days", "365", "-sha256",
                "-extfile", str(leaf_cnf), "-extensions", "v3_leaf", "-out", str(evil_cert),
            )

            result = subprocess.run(
                [OPENSSL, "verify", "-CAfile", str(ca_cert), str(evil_cert)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0, "le name constraints dovrebbero bocciare example.com")


def _parse_openssl_date(text: str) -> datetime.datetime:
    text = text.strip()
    # Formato tipico: "Jan  1 00:00:00 2030 GMT"
    return datetime.datetime.strptime(text, "%b %d %H:%M:%S %Y %Z")


@unittest.skipUnless(OPENSSL, "openssl non disponibile in questo ambiente")
class TestFingerprintEDer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        certs.ensure_certificates(self.state, ["mac.local"], [])

    def test_fingerprint_formato(self):
        fp = certs.ca_fingerprint(self.state)
        self.assertRegex(fp, r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")

    def test_der_e_binario_valido(self):
        der = certs.ca_der(self.state)
        # un certificato DER inizia con la sequenza ASN.1 SEQUENCE (0x30)
        self.assertEqual(der[0], 0x30)
        self.assertGreater(len(der), 200)


@unittest.skipUnless(OPENSSL, "openssl non disponibile in questo ambiente")
class TestMobileconfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        certs.ensure_certificates(self.state, ["mac.local"], [])

    def test_si_rilegge_con_plistlib_e_contiene_il_der_della_ca(self):
        data = certs.mobileconfig(self.state, "Mac di Prova")
        parsed = plistlib.loads(data)
        self.assertEqual(parsed["PayloadType"], "Configuration")
        self.assertIn("Mac di Prova", parsed["PayloadDisplayName"])
        payloads = parsed["PayloadContent"]
        self.assertEqual(len(payloads), 1)
        ca_payload = payloads[0]
        self.assertEqual(ca_payload["PayloadType"], "com.apple.security.root")
        self.assertEqual(ca_payload["PayloadCertificateFileName"], "TeleMac-CA.cer")
        self.assertEqual(bytes(ca_payload["PayloadContent"]), certs.ca_der(self.state))

    def test_uuid_stabili_per_la_stessa_ca(self):
        data1 = certs.mobileconfig(self.state, "Mac")
        data2 = certs.mobileconfig(self.state, "Mac")
        p1 = plistlib.loads(data1)
        p2 = plistlib.loads(data2)
        self.assertEqual(p1["PayloadUUID"], p2["PayloadUUID"])
        self.assertEqual(p1["PayloadContent"][0]["PayloadUUID"], p2["PayloadContent"][0]["PayloadUUID"])


if __name__ == "__main__":
    unittest.main()
