"""The Gree wire format, checked without an air conditioner in the room."""

import base64

import pytest

from argon.ac import gree


class TestCrypto:
    def test_ecb_round_trips(self):
        assert gree.decrypt(gree.encrypt({"t": "scan"})) == {"t": "scan"}

    def test_gcm_round_trips(self):
        pack, tag = gree.encrypt_gcm({"t": "bind"}, gree.GENERIC_KEY_GCM)
        assert gree.decrypt_gcm(pack, tag, gree.GENERIC_KEY_GCM) == {"t": "bind"}

    def test_gcm_rejects_a_tampered_body(self):
        # The auth tag is the point of GCM: a flipped byte must fail loudly
        # rather than decrypt into nonsense that gets parsed as a command.
        pack, tag = gree.encrypt_gcm({"t": "bind"}, gree.GENERIC_KEY_GCM)
        raw = bytearray(base64.b64decode(pack))
        raw[0] ^= 0xFF
        with pytest.raises(Exception):
            gree.decrypt_gcm(base64.b64encode(bytes(raw)).decode(), tag, gree.GENERIC_KEY_GCM)

    def test_the_two_ciphers_are_not_interchangeable(self):
        # Why an ECB bind against V2 firmware times out instead of erroring:
        # the unit cannot read the packet, so it says nothing at all.
        pack, _ = gree.encrypt_gcm({"t": "bind"}, gree.GENERIC_KEY_GCM)
        with pytest.raises(Exception):
            gree.decrypt(pack, gree.GENERIC_KEY)

    def test_padding_survives_a_block_aligned_payload(self):
        payload = {"t": "x" * 16}
        assert gree.decrypt(gree.encrypt(payload)) == payload


class TestStatusDecoding:
    def _unit(self, monkeypatch, values):
        unit = gree.Unit(mac="aa", host="127.0.0.1", bound=True)
        cols = list(values)
        monkeypatch.setattr(
            unit, "_send", lambda *a, **k: {"cols": cols, "dat": [values[c] for c in cols]}
        )
        return unit

    def test_it_names_what_the_numbers_mean(self, monkeypatch):
        unit = self._unit(monkeypatch, {"Pow": 1, "Mod": 1, "SetTem": 22, "TemSen": 67})

        status = unit.status()

        assert status["on"] is True
        assert status["mode"] == "cool"
        assert status["target_c"] == 22
        # The unit reports room temperature offset by 40.
        assert status["room_c"] == 27

    def test_a_missing_sensor_reads_as_unknown_not_as_minus_forty(self, monkeypatch):
        unit = self._unit(monkeypatch, {"Pow": 0, "TemSen": 0})

        assert unit.status()["room_c"] is None

    def test_an_unrecognised_mode_does_not_crash(self, monkeypatch):
        unit = self._unit(monkeypatch, {"Mod": 99})

        assert unit.status()["mode"] == "unknown"


class TestCommands:
    def test_set_sends_parallel_option_and_value_lists(self, monkeypatch):
        unit = gree.Unit(mac="aa", host="127.0.0.1", bound=True)
        sent = {}
        monkeypatch.setattr(unit, "_send", lambda payload, **k: sent.update(payload) or {"r": 200})

        unit.set(Pow=1, SetTem=24)

        assert sent["t"] == "cmd"
        assert dict(zip(sent["opt"], sent["p"])) == {"Pow": 1, "SetTem": 24}

    def test_power_maps_to_the_right_column(self, monkeypatch):
        unit = gree.Unit(mac="aa", host="127.0.0.1", bound=True)
        sent = {}
        monkeypatch.setattr(unit, "_send", lambda payload, **k: sent.update(payload) or {"r": 200})

        unit.power(False)

        assert dict(zip(sent["opt"], sent["p"])) == {"Pow": 0}

    def test_setting_nothing_is_refused(self):
        with pytest.raises(ValueError):
            gree.Unit(mac="aa", host="127.0.0.1", bound=True).set()
