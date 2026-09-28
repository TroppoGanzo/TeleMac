"""Test di telemac/netinfo.py. Giriamo su Linux: i rami 'darwin' (scutil) non
si possono esercitare qui, testiamo la parte non-macOS e i ripieghi."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

import netinfo  # noqa: E402


class TestLocalHostname(unittest.TestCase):
    def test_finisce_con_dot_local(self):
        self.assertTrue(netinfo.local_hostname().endswith(".local"))

    def test_mai_eccezioni_anche_se_gethostname_fallisce(self):
        with mock.patch.object(netinfo.socket, "gethostname", side_effect=OSError("boom")):
            self.assertEqual(netinfo.local_hostname(), "localhost")

    def test_ripulisce_caratteri_non_ammessi(self):
        with mock.patch.object(netinfo.socket, "gethostname", return_value="my_host!!.example"):
            name = netinfo.local_hostname()
        self.assertRegex(name, r"^[A-Za-z0-9-]+\.local$")

    def test_hostname_vuoto_da_localhost(self):
        with mock.patch.object(netinfo.socket, "gethostname", return_value=""):
            self.assertEqual(netinfo.local_hostname(), "localhost")

    def test_non_raddoppia_local_se_gia_presente(self):
        with mock.patch.object(netinfo.socket, "gethostname", return_value="giamacchina.local"):
            name = netinfo.local_hostname()
        self.assertEqual(name.count(".local"), 1)


class TestComputerName(unittest.TestCase):
    def test_non_vuoto(self):
        self.assertTrue(netinfo.computer_name())

    def test_mai_eccezioni(self):
        with mock.patch.object(netinfo.socket, "gethostname", side_effect=OSError("boom")):
            self.assertEqual(netinfo.computer_name(), "Mac")


class TestLanIpv4(unittest.TestCase):
    def test_restituisce_ip_privato_o_none(self):
        ip = netinfo.lan_ipv4()
        if ip is not None:
            import ipaddress
            addr = ipaddress.ip_address(ip)
            self.assertTrue(addr.is_private)
            self.assertFalse(addr.is_loopback)

    def test_none_se_il_socket_fallisce(self):
        class BoomSocket:
            def __init__(self, *a, **k):
                pass

            def connect(self, *a, **k):
                raise OSError("rete assente")

            def close(self):
                pass

        with mock.patch.object(netinfo.socket, "socket", BoomSocket):
            self.assertIsNone(netinfo.lan_ipv4())

    def test_scarta_indirizzi_pubblici(self):
        class FakeSocket:
            def __init__(self, *a, **k):
                pass

            def connect(self, *a, **k):
                pass

            def getsockname(self):
                return ("8.8.4.4", 12345)

            def close(self):
                pass

        with mock.patch.object(netinfo.socket, "socket", FakeSocket):
            self.assertIsNone(netinfo.lan_ipv4())

    def test_scarta_loopback(self):
        class FakeSocket:
            def __init__(self, *a, **k):
                pass

            def connect(self, *a, **k):
                pass

            def getsockname(self):
                return ("127.0.0.1", 12345)

            def close(self):
                pass

        with mock.patch.object(netinfo.socket, "socket", FakeSocket):
            self.assertIsNone(netinfo.lan_ipv4())

    def test_accetta_indirizzo_privato(self):
        class FakeSocket:
            def __init__(self, *a, **k):
                pass

            def connect(self, *a, **k):
                pass

            def getsockname(self):
                return ("192.168.1.42", 12345)

            def close(self):
                pass

        with mock.patch.object(netinfo.socket, "socket", FakeSocket):
            self.assertEqual(netinfo.lan_ipv4(), "192.168.1.42")


if __name__ == "__main__":
    unittest.main()
