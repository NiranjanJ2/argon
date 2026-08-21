"""The planning screen's HTTP surface."""

import pytest

from argon import planner
from argon.api import server as srv
from argon.config import ApiConfig, Config

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(srv._rt, "config", Config(api=ApiConfig(token=TOKEN)))
    monkeypatch.setattr(srv._rt, "agent", None)
    monkeypatch.setattr(srv._rt, "whatsapp", None)
    return srv.app.test_client()


class TestGate:
    def test_no_token_plans_nothing(self, client):
        assert client.post("/v1/planner", json={}, headers={}).status_code == 401
        assert client.get("/v1/planner", headers={}).status_code == 401


class TestMarkingTheDayPlanned:
    def test_an_empty_submission_still_counts_as_planned(self, client):
        # "I looked and nothing needs moving" is an answer. Without recording
        # it, the screen reopens on the next launch and becomes a nag.
        assert planner.last_planned() is None

        response = client.post("/v1/planner", json={}, headers=AUTH)

        assert response.status_code == 200
        assert response.json["planned_for"] == planner.last_planned()
        assert planner.last_planned() is not None

    def test_planning_closes_the_screen_for_the_rest_of_the_day(self, client):
        from datetime import datetime

        client.post("/v1/planner", json={}, headers=AUTH)
        later = datetime.fromisoformat(planner.last_planned()).replace(hour=20)

        assert planner.is_due(later) is False

    def test_a_bad_task_id_does_not_lose_the_whole_submission(self, client):
        response = client.post(
            "/v1/planner", json={"done": ["not-a-real-task"]}, headers=AUTH
        )

        # The day is still recorded as planned and the failure is reported,
        # rather than a 500 that leaves him with the screen open forever.
        assert response.status_code == 200
        assert response.json["planned_for"]
        assert response.json["errors"]


class TestSchedulingFailureDoesNotLoseThePlan:
    def test_a_broken_scheduler_still_marks_the_day(self, client, monkeypatch):
        """The bug: the request 500'd between saving the time and marking the
        day, so the wizard reopened every launch with the time already set."""
        from argon import planner

        class ExplodingCron:
            def list_jobs(self, include_disabled: bool = False):
                raise RuntimeError("no running event loop")

        monkeypatch.setattr(srv._rt, "cron", ExplodingCron())

        response = client.post("/v1/planner", json={"start_at": "18:00"}, headers=AUTH)

        assert response.status_code == 200
        assert response.json["planned_for"]
        assert response.json["errors"], "the failure should be reported, not hidden"
        # The time he chose is still his, even though the alarm could not be set.
        assert planner.start_time() == "18:00"
