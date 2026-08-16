"""Setting a focus mode from a switch on the phone.

Deliberately not routed through ``set_focus_mode``: that tool refuses a
night-time block until he confirms in a later message, because the model asking
for one is a guess and a bad guess once locked the phone at 1:47 AM. A tap is
not a guess, so demanding a second confirmation would be asking him to agree
with himself — and until this existed there was no way to turn weekend mode on
except asking Argon and hoping.
"""

import pytest

from argon.api import server as srv
from argon.config import ApiConfig, Config
from argon.ios import mode as ios_mode

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(srv._rt, "config", Config(api=ApiConfig(token=TOKEN)))
    monkeypatch.setattr(srv._rt, "agent", None)
    monkeypatch.setattr(srv._rt, "whatsapp", None)
    return srv.app.test_client()


class TestSettingAMode:
    def test_weekend_arrives_with_its_allowance(self, client):
        response = client.post("/v1/ios/mode", json={"mode": "weekend"}, headers=AUTH)

        assert response.status_code == 200
        assert response.json["mode"] == "weekend"
        assert response.json["allowance"] == {"minutes": 15, "per_hours": 1}

    def test_the_allowance_can_be_chosen(self, client):
        response = client.post(
            "/v1/ios/mode",
            json={"mode": "weekend", "allowance_min": 30, "allowance_per_hours": 24},
            headers=AUTH,
        )

        assert response.json["allowance"] == {"minutes": 30, "per_hours": 24}

    def test_turning_it_off_again(self, client):
        client.post("/v1/ios/mode", json={"mode": "weekend"}, headers=AUTH)
        response = client.post("/v1/ios/mode", json={"mode": "off"}, headers=AUTH)

        assert response.json["mode"] == "off"
        assert response.json["allowance"] is None

    def test_an_unknown_mode_is_refused(self, client):
        assert client.post("/v1/ios/mode", json={"mode": "banana"}, headers=AUTH).status_code == 400

    def test_no_token_sets_nothing(self, client):
        assert client.post("/v1/ios/mode", json={"mode": "weekend"}).status_code == 401


class TestOwnership:
    def test_a_mode_he_set_survives_finishing_a_task(self, client):
        from argon.tools.tasks import release_task_focus

        client.post("/v1/ios/mode", json={"mode": "weekend"}, headers=AUTH)
        release_task_focus("some-task")

        assert ios_mode.get_mode()["mode"] == "weekend"

    def test_the_emergency_override_still_wins(self, client):
        ios_mode.engage_override(minutes=30)

        response = client.post("/v1/ios/mode", json={"mode": "weekend"}, headers=AUTH)

        assert response.status_code == 409
        assert ios_mode.get_mode()["mode"] == "off"
