"""The app's conversation, and the badge that points at it."""

import pytest

from argon.api import server as srv
from argon.config import ApiConfig, Config
from argon.core.session import SessionManager
from argon.ios import unread

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(srv._rt, "config", Config(api=ApiConfig(token=TOKEN)))
    monkeypatch.setattr(srv._rt, "agent", None)
    monkeypatch.setattr(srv._rt, "whatsapp", None)
    return srv.app.test_client()


def _say(tmp_path, role, text):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("ios")
    session.add_message(role, text)
    sessions.save(session)


class TestHistory:
    def test_both_sides_of_the_conversation_come_back(self, client, tmp_path):
        _say(tmp_path, "assistant", "Have you started APUSH?")
        _say(tmp_path, "user", "starting now")

        messages = client.get("/v1/messages", headers=AUTH).json["messages"]

        assert [m["role"] for m in messages] == ["assistant", "user"]
        assert messages[0]["text"] == "Have you started APUSH?"

    def test_tool_traffic_is_not_conversation(self, client, tmp_path):
        _say(tmp_path, "assistant", "Working on it")
        _say(tmp_path, "tool", "Screen Time block released.")

        messages = client.get("/v1/messages", headers=AUTH).json["messages"]

        assert all(m["role"] != "tool" for m in messages)

    def test_empty_messages_are_dropped(self, client, tmp_path):
        _say(tmp_path, "assistant", "   ")

        assert client.get("/v1/messages", headers=AUTH).json["messages"] == []

    def test_no_token_reads_nothing(self, client):
        assert client.get("/v1/messages").status_code == 401


class TestBadge:
    def test_it_counts_up_and_clears(self, client):
        unread.bump()
        unread.bump()
        assert client.get("/v1/messages", headers=AUTH).json["unread"] == 2

        cleared = client.post("/v1/ios/read", headers=AUTH).json

        assert cleared["cleared"] == 2
        assert client.get("/v1/messages", headers=AUTH).json["unread"] == 0

    def test_clearing_twice_is_harmless(self, client):
        unread.bump()
        client.post("/v1/ios/read", headers=AUTH)

        assert client.post("/v1/ios/read", headers=AUTH).json["cleared"] == 0
