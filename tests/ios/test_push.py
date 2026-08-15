"""Reaching the phone, and knowing whether it worked.

Push fails silently by nature: nothing buzzes, and without Apple's answer there
is no difference between "delivered" and "swallowed". These pin the parts that
decide whether Argon may claim it told him something.
"""

from __future__ import annotations

import json

import pytest

from argon.config import Config
from argon.ios import push as push_mod
from argon.ios.push import APNsClient, device_token


def _config(**apns):
    base = {"enabled": True, "teamId": "TEAM123456", "keyId": "KEY1234567",
            "bundleId": "com.niranjanj.argon"}
    return Config(google={"enabled": False}, ios={"apns": {**base, **apns}})


def _write_key(tmp_path):
    """A real P-256 key, because the JWT is genuinely signed."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    path = tmp_path / "apns" / "AuthKey_KEY1234567.p8"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pem)
    return path


def _register(tmp_path, token="abc123", environment="production"):
    path = tmp_path / "ios" / "device.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"device_token": token, "environment": environment}))
    return path


class TestWhetherItCanSendAtAll:
    def test_push_disabled_is_not_configured(self, tmp_path):
        assert APNsClient(_config(enabled=False)).configured is False

    def test_a_missing_key_file_is_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        assert APNsClient(_config()).configured is False

    def test_key_plus_settings_is_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        _write_key(tmp_path)
        assert APNsClient(_config()).configured is True

    async def test_sending_without_configuration_says_so_rather_than_raising(self, tmp_path):
        result = await APNsClient(_config(enabled=False)).send("t", "b")
        assert result.ok is False and result.reason == "not configured"

    async def test_sending_with_no_registered_phone_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        _write_key(tmp_path)
        result = await APNsClient(_config()).send("t", "b")
        assert result.ok is False and "no device token" in result.reason


class TestTheEnvironmentTravelsWithTheToken:
    """A sandbox token pushed at production is rejected as BadDeviceToken.

    An Xcode build registers against sandbox and a TestFlight build against
    production, so assuming either one silently breaks half the installs.
    """

    def test_a_testflight_token_reads_as_production(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        _register(tmp_path, environment="production")
        assert device_token() == ("abc123", "production")

    def test_an_xcode_token_reads_as_sandbox(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        _register(tmp_path, environment="sandbox")
        assert device_token() == ("abc123", "sandbox")

    def test_no_registration_is_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        assert device_token() == (None, "production")


class TestTalkingToApple:
    def _client(self, tmp_path, monkeypatch, environment="production"):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        _write_key(tmp_path)
        _register(tmp_path, environment=environment)
        return APNsClient(_config())

    def _stub(self, monkeypatch, status, body=None, capture=None):
        class _Response:
            status_code = status

            def json(self):
                if body is None:
                    raise ValueError("no body")
                return body

            @property
            def text(self):
                return json.dumps(body or {})

        class _Client:
            def __init__(self, **kwargs):
                if capture is not None:
                    capture["client_kwargs"] = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def post(self, url, headers=None, content=None):
                if capture is not None:
                    capture["url"] = url
                    capture["headers"] = headers
                    capture["payload"] = json.loads(content)
                return _Response()

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _Client)

    async def test_a_200_is_a_delivered_notification(self, tmp_path, monkeypatch):
        capture = {}
        client = self._client(tmp_path, monkeypatch)
        self._stub(monkeypatch, 200, capture=capture)

        result = await client.send("Argon", "Chemistry is due tonight", category="ARGON_TASK")

        assert result.ok is True and result.status == 200
        assert capture["url"] == "https://api.push.apple.com/3/device/abc123"
        assert capture["headers"]["apns-topic"] == "com.niranjanj.argon"
        assert capture["headers"]["authorization"].startswith("bearer ")
        assert capture["client_kwargs"]["http2"] is True, "APNs requires HTTP/2"
        assert capture["payload"]["aps"]["alert"] == {
            "title": "Argon", "body": "Chemistry is due tonight"
        }
        assert capture["payload"]["aps"]["category"] == "ARGON_TASK"

    async def test_a_sandbox_token_goes_to_the_sandbox_host(self, tmp_path, monkeypatch):
        capture = {}
        client = self._client(tmp_path, monkeypatch, environment="sandbox")
        self._stub(monkeypatch, 200, capture=capture)

        await client.send("t", "b")

        assert capture["url"].startswith("https://api.sandbox.push.apple.com")

    async def test_apples_reason_is_reported_not_swallowed(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        self._stub(monkeypatch, 400, {"reason": "TopicDisallowed"})

        result = await client.send("t", "b")

        assert result.ok is False
        assert result.status == 400 and result.reason == "TopicDisallowed"

    async def test_a_dead_token_is_discarded_so_we_stop_pushing_into_nothing(
        self, tmp_path, monkeypatch
    ):
        client = self._client(tmp_path, monkeypatch)
        self._stub(monkeypatch, 410, {"reason": "Unregistered"})

        result = await client.send("t", "b")

        assert result.ok is False and result.reason == "Unregistered"
        assert device_token()[0] is None, "the app must re-register before we push again"
        stored = json.loads((tmp_path / "ios" / "device.json").read_text())
        assert stored["unregistered_reason"] == "Unregistered"

    async def test_a_transient_rejection_keeps_the_token(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        self._stub(monkeypatch, 429, {"reason": "TooManyRequests"})

        await client.send("t", "b")

        assert device_token()[0] == "abc123", "a rate limit is not a dead device"

    async def test_a_dead_network_is_reported_not_raised(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)

        class _Boom:
            def __init__(self, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def post(self, *_a, **_kw):
                raise OSError("network is down")

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _Boom)

        result = await client.send("t", "b")
        assert result.ok is False and "network is down" in result.reason


class TestTheAssertion:
    def test_the_bearer_token_is_reused_not_minted_per_push(self, tmp_path, monkeypatch):
        """Apple rate-limits regenerating it faster than every twenty minutes."""
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        _write_key(tmp_path)
        client = APNsClient(_config())

        first = client._bearer()
        second = client._bearer()

        assert first == second

    def test_it_is_refreshed_once_it_ages_out(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        _write_key(tmp_path)
        client = APNsClient(_config())
        first = client._bearer()

        client._token_minted -= push_mod.TOKEN_REFRESH_SECONDS + 1
        assert client._bearer() != first

    def test_it_carries_the_key_id_and_team(self, tmp_path, monkeypatch):
        import jwt

        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        _write_key(tmp_path)
        token = APNsClient(_config())._bearer()

        assert jwt.get_unverified_header(token)["kid"] == "KEY1234567"
        assert jwt.decode(token, options={"verify_signature": False})["iss"] == "TEAM123456"

    def test_a_missing_key_is_an_explicit_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        client = APNsClient(_config())
        with pytest.raises(push_mod.APNsUnavailable):
            client._bearer()
