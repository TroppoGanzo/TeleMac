"""Test di telemac/pairing.py: PairingManager (codice a 6 cifre) e DeviceStore
(token permanenti). Usiamo un orologio finto per controllare scadenza/attese."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemac"))

import pairing  # noqa: E402
from pairing import DeviceStore, PairingManager  # noqa: E402


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestPairingManagerStart(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.codes = []
        self.done_calls = 0
        self.mgr = PairingManager(
            clock=self.clock, ttl=120, max_attempts=5, min_interval=3.0,
            on_code=lambda code, secs: self.codes.append((code, secs)),
            on_done=lambda: setattr(self, "done_calls", self.done_calls + 1),
        )

    def test_crea_un_codice_di_sei_cifre(self):
        ok, info = self.mgr.start()
        self.assertTrue(ok)
        self.assertEqual(info["expires_in"], 120)
        self.assertEqual(len(self.codes), 1)
        code, secs = self.codes[0]
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertEqual(secs, 120)

    def test_too_fast_quando_si_richiede_un_codice_nuovo_troppo_presto(self):
        self.mgr.start()
        code = self.codes[-1][0]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.mgr.finish(wrong)  # blocca il codice (locked) allo stesso istante della creazione
        self.clock.advance(1)  # meno del min_interval (3s) dall'ultima creazione di un codice
        ok, info = self.mgr.start()
        self.assertFalse(ok)
        self.assertEqual(info["error"], "too_fast")

    def test_richiesta_dopo_min_interval_crea_codice_nuovo(self):
        ok1, info1 = self.mgr.start()
        first_code = self.codes[0][0]
        self.clock.advance(200)  # scade e supera anche il min_interval
        self.mgr.finish("000000")
        self.clock.advance(5)
        ok2, info2 = self.mgr.start()
        self.assertTrue(ok2)
        self.assertEqual(len(self.codes), 2)

    def test_ricrea_non_un_nuovo_codice_se_quello_attuale_e_ancora_valido(self):
        self.mgr.start()
        first_code = self.codes[0][0]
        self.clock.advance(5)  # oltre il min_interval, ma entro il ttl
        ok, info = self.mgr.start()
        self.assertTrue(ok)
        self.assertEqual(len(self.codes), 2)  # on_code richiamato di nuovo...
        self.assertEqual(self.codes[1][0], first_code)  # ...ma con lo STESSO codice
        self.assertLess(info["expires_in"], 120)  # tempo residuo, non 120 di nuovo

    def test_richiamo_immediato_e_too_fast_anche_con_codice_valido(self):
        # Il limite di frequenza vale per ogni chiamata, anche per un semplice
        # "rimostra il codice": altrimenti un doppio tocco sul telefono
        # aprirebbe due finestre di dialogo sul Mac.
        self.mgr.start()
        self.clock.advance(0.1)  # molto meno del min_interval di 3s
        ok, info = self.mgr.start()
        self.assertFalse(ok)
        self.assertEqual(info["error"], "too_fast")


class TestPairingManagerFinish(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.codes = []
        self.done_calls = 0
        self.mgr = PairingManager(
            clock=self.clock, ttl=120, max_attempts=5, min_interval=3.0,
            on_code=lambda code, secs: self.codes.append((code, secs)),
            on_done=lambda: setattr(self, "done_calls", self.done_calls + 1),
        )

    def _current_code(self):
        return self.codes[-1][0]

    def test_no_code_se_non_e_mai_stato_avviato(self):
        ok, error, left = self.mgr.finish("123456")
        self.assertFalse(ok)
        self.assertEqual(error, "no_code")
        self.assertIsNone(left)

    def test_codice_giusto_da_successo_e_consuma(self):
        self.mgr.start()
        code = self._current_code()
        ok, error, left = self.mgr.finish(code)
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(self.done_calls, 1)
        # il codice è consumato: un altro finish ora dà no_code
        ok2, error2, left2 = self.mgr.finish(code)
        self.assertFalse(ok2)
        self.assertEqual(error2, "no_code")

    def test_codice_sbagliato_decrementa_i_tentativi(self):
        self.mgr.start()
        # essendo casuale, il codice giusto potrebbe per sfortuna combaciare:
        # rendiamo il test deterministico forzando un codice sicuramente errato
        code = self._current_code()
        wrong = "000000" if code != "000000" else "111111"
        ok, error, left = self.mgr.finish(wrong)
        self.assertFalse(ok)
        self.assertEqual(error, "wrong_code")
        self.assertEqual(left, 4)

    def test_blocco_dopo_cinque_tentativi_sbagliati(self):
        self.mgr.start()
        code = self._current_code()
        wrong = "000000" if code != "000000" else "111111"
        for i in range(4):
            ok, error, left = self.mgr.finish(wrong)
            self.assertEqual(error, "wrong_code")
        ok, error, left = self.mgr.finish(wrong)
        self.assertFalse(ok)
        self.assertEqual(error, "locked")
        self.assertEqual(self.done_calls, 1)

    def test_dopo_il_blocco_i_tentativi_successivi_restano_locked(self):
        self.mgr.start()
        code = self._current_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.mgr.finish(wrong)
        ok, error, left = self.mgr.finish(code)  # anche col codice giusto: ormai è bloccato
        self.assertFalse(ok)
        self.assertEqual(error, "locked")

    def test_scadenza(self):
        self.mgr.start()
        code = self._current_code()
        self.clock.advance(121)
        ok, error, left = self.mgr.finish(code)
        self.assertFalse(ok)
        self.assertEqual(error, "expired")
        self.assertEqual(self.done_calls, 1)

    def test_dopo_la_scadenza_i_tentativi_successivi_restano_expired(self):
        self.mgr.start()
        code = self._current_code()
        self.clock.advance(121)
        self.mgr.finish(code)
        ok, error, left = self.mgr.finish(code)
        self.assertFalse(ok)
        self.assertEqual(error, "expired")
        self.assertEqual(self.done_calls, 1)  # non richiamato una seconda volta

    def test_confronto_sicuro_anche_con_tipi_strani(self):
        self.mgr.start()
        # non deve sollevare anche se il client manda qualcosa di strano
        ok, error, left = self.mgr.finish("")
        self.assertFalse(ok)


class TestOnCodeOnDoneNonSollevano(unittest.TestCase):
    def test_eccezioni_nei_callback_non_si_propagano(self):
        clock = FakeClock()

        def boom_code(code, secs):
            raise RuntimeError("boom")

        def boom_done():
            raise RuntimeError("boom")

        mgr = PairingManager(clock=clock, on_code=boom_code, on_done=boom_done)
        ok, info = mgr.start()  # non deve sollevare nonostante on_code fallisca
        self.assertTrue(ok)


class TestDeviceStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = DeviceStore(Path(self.tmp.name) / "devices.json")

    def test_add_e_verify(self):
        token = self.store.add("iPhone di Prova")
        device = self.store.verify(token)
        self.assertIsNotNone(device)
        self.assertEqual(device["name"], "iPhone di Prova")

    def test_token_non_salvato_in_chiaro(self):
        token = self.store.add("iPhone")
        raw = (Path(self.tmp.name) / "devices.json").read_text(encoding="utf-8")
        self.assertNotIn(token, raw)

    def test_verify_token_sbagliato(self):
        self.store.add("iPhone")
        self.assertIsNone(self.store.verify("token-inventato"))

    def test_verify_none_o_vuoto(self):
        self.assertIsNone(self.store.verify(None))
        self.assertIsNone(self.store.verify(""))

    def test_count_e_clear(self):
        self.store.add("A")
        self.store.add("B")
        self.assertEqual(self.store.count(), 2)
        self.store.clear()
        self.assertEqual(self.store.count(), 0)

    def test_permessi_del_file(self):
        self.store.add("iPhone")
        path = Path(self.tmp.name) / "devices.json"
        mode = path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_piu_dispositivi_coesistono(self):
        t1 = self.store.add("iPhone 1")
        t2 = self.store.add("iPhone 2")
        self.assertIsNotNone(self.store.verify(t1))
        self.assertIsNotNone(self.store.verify(t2))
        self.assertNotEqual(t1, t2)

    def test_add_in_parallelo_non_perde_dispositivi(self):
        threads = [threading.Thread(target=self.store.add, args=(f"iPhone {i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.store.count(), 20)


class TestPairingManagerForzaBruta(unittest.TestCase):
    def test_tentativi_in_parallelo_non_superano_il_limite(self):
        mgr = PairingManager(max_attempts=5)
        mgr.start()
        real_code = mgr._code
        compared = []
        original = pairing.secrets.compare_digest

        def slow_compare(a, b):
            compared.append(a)
            time.sleep(0.005)  # allarga la finestra in cui due thread potrebbero incrociarsi
            return original(a, b)

        pairing.secrets.compare_digest = slow_compare
        try:
            guesses = [f"{n:06d}" for n in range(100) if f"{n:06d}" != real_code]
            threads = [threading.Thread(target=mgr.finish, args=(g,)) for g in guesses]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            pairing.secrets.compare_digest = original
        self.assertEqual(len(compared), 5)

    def test_ogni_blocco_raddoppia_l_attesa(self):
        clock = FakeClock()
        mgr = PairingManager(clock=clock, min_interval=3.0, max_attempts=1, max_backoff=300.0)

        def lock_current_code():
            ok, _ = mgr.start()
            self.assertTrue(ok)
            wrong = "000000" if mgr._code != "000000" else "111111"
            self.assertEqual(mgr.finish(wrong)[1], "locked")

        lock_current_code()                 # 1 blocco -> attesa 6 s
        clock.advance(5)
        self.assertEqual(mgr.start(), (False, {"error": "too_fast"}))
        clock.advance(1.1)
        lock_current_code()                 # 2 blocchi -> attesa 12 s
        clock.advance(11)
        self.assertEqual(mgr.start(), (False, {"error": "too_fast"}))
        clock.advance(1.1)
        self.assertTrue(mgr.start()[0])

    def test_attesa_massima_e_azzeramento_dopo_successo(self):
        clock = FakeClock()
        mgr = PairingManager(clock=clock, min_interval=3.0, max_attempts=1, max_backoff=20.0)
        for _ in range(6):
            clock.advance(1000)
            mgr.start()
            mgr.finish("000000" if mgr._code != "000000" else "111111")
        clock.advance(20.1)                 # l'attesa non supera max_backoff
        self.assertTrue(mgr.start()[0])
        self.assertTrue(mgr.finish(mgr._code)[0])
        clock.advance(3.1)                  # dopo un successo si torna a min_interval
        self.assertTrue(mgr.start()[0])

    def test_codici_non_ascii_o_malformati_contano_come_sbagliati(self):
        mgr = PairingManager(max_attempts=5)
        mgr.start()
        for bad in ("１２３４５６", "12345", "1234567", "abcdef"):
            ok, error, _ = mgr.finish(bad)
            self.assertFalse(ok)
            self.assertIn(error, ("wrong_code", "locked"))


if __name__ == "__main__":
    unittest.main()
