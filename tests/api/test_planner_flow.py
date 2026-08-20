"""The whole planning pass, from what it offers to what it changed.

Every piece of this has its own test. This is the one that runs them together,
because the interesting failures live in the seams: an id the board shows but
the store cannot resolve, a completion filed as a success when it missed.
"""

import pytest

from argon import planner
from argon.api import server as srv
from argon.config import ApiConfig, Config
from argon.tasks.local_store import LocalTaskStore

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(srv._rt, "config", Config(api=ApiConfig(token=TOKEN)))
    monkeypatch.setattr(srv._rt, "agent", None)
    monkeypatch.setattr(srv._rt, "whatsapp", None)
    monkeypatch.setattr(srv._rt, "cron", None)
    return srv.app.test_client()


@pytest.fixture
def tasks(tmp_path):
    return LocalTaskStore(tmp_path)


class TestAFullPass:
    def test_done_carry_and_add_all_land(self, client, tasks):
        stale = tasks.add_task("SAT reading study", due="2026-08-10")
        keep = tasks.add_task("Finish Cat 1", due="2026-08-11")

        response = client.post(
            "/v1/planner",
            json={
                "done": [stale["id"]],
                "carry": [keep["id"]],
                "add": [{"title": "Read chapter 3", "subject": "APUSH"}],
                "chem": True,
            },
            headers=AUTH,
        )

        assert response.status_code == 200
        assert response.json["errors"] == [], response.json["errors"]

        titles = {t["title"]: t for t in tasks.get_all()}
        # Completed leaves the pending list entirely.
        assert "SAT reading study" not in titles
        # Carried is still there, moved to today.
        assert titles["Finish Cat 1"]["due"] == response.json["planned_for"]
        # Both the typed item and the Chem checkbox became real work.
        assert "Read chapter 3" in titles
        assert planner.CHEM_TITLE in titles

    def test_chem_is_only_added_when_ticked(self, client, tasks):
        client.post("/v1/planner", json={"chem": False}, headers=AUTH)

        assert all(t["title"] != planner.CHEM_TITLE for t in tasks.get_all())

    def test_a_dead_id_is_reported_not_counted_as_done(self, client, tasks):
        tasks.add_task("Real work")

        response = client.post("/v1/planner", json={"done": ["no-such-task"]}, headers=AUTH)

        # The bug this guards: the tools signal a miss by returning an
        # unsuccessful result rather than raising, so it used to land in
        # "completed" and be reported back as work he had finished.
        assert response.json["completed"] == []
        assert response.json["errors"]
        assert len(tasks.get_all()) == 1

    def test_planning_twice_in_a_day_is_harmless(self, client, tasks):
        keep = tasks.add_task("Finish Cat 1", due="2026-08-11")

        client.post("/v1/planner", json={"carry": [keep["id"]]}, headers=AUTH)
        second = client.post("/v1/planner", json={"carry": [keep["id"]]}, headers=AUTH)

        assert second.json["errors"] == []
        assert len(tasks.get_all()) == 1


class TestOfferedThenApplied:
    def test_what_it_offers_is_what_it_can_act_on(self, client, tasks):
        """Every id the planner shows must be an id the store can resolve.

        This is the seam the migration broke: the board prefers google_task_id
        where a row has one, and the store did not recognise it.
        """
        tasks.add_task("Overdue thing", due="2026-08-01")
        tasks.add_task("Later project", due="2026-12-01")

        offered = client.get("/v1/planner", headers=AUTH).json
        ids = [i["id"] for i in offered["overdue"] + offered["today"] + offered["long_term"]]
        assert ids, "planner offered nothing to act on"

        for task_id in ids:
            assert tasks._resolve(task_id) is not None, f"{task_id} is offered but unresolvable"
