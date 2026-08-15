"""HTTP surface: auth must fail closed, and the webhook must stay on-host.

This server is bound to 0.0.0.0 on the home LAN, so ``/v1/*`` is the only thing
between the LAN and a full agent turn. Nothing here touches the network — the
agent bridge is a plain callable and the WhatsApp sink is a list.
"""

from __future__ import annotations

import json

import pytest

from argon.api import server as srv
from argon.config import ApiConfig, Config

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
V1_ROUTES = [
    ("post", "/v1/chat"),
    ("post", "/v1/webhook/shortcut"),
    ("get", "/v1/status"),
    ("get", "/v1/tasks"),
    ("post", "/v1/tasks"),
    ("patch", "/v1/tasks/task-1"),
    ("post", "/v1/screentime"),
    ("get", "/v1/screentime"),
    ("get", "/v1/ios/mode"),
    ("post", "/v1/ios/state"),
    ("post", "/v1/ios/register"),
    ("post", "/v1/ios/override"),
]


def _client(tmp_path, monkeypatch, *, token: str = TOKEN):
    """A test client with an isolated ARGON_HOME and no agent wired."""
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(srv._rt, "config", Config(api=ApiConfig(token=token)))
    monkeypatch.setattr(srv._rt, "agent", None)
    monkeypatch.setattr(srv._rt, "whatsapp", None)
    # The task cache is module state; without this it leaks between tests.
    monkeypatch.setattr(srv, "_tasks_cache", None)
    return srv.app.test_client()


def _call(client, method: str, path: str, **kw):
    return getattr(client, method)(path, **kw)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", V1_ROUTES)
def test_v1_rejects_a_request_with_no_token(tmp_path, monkeypatch, method, path):
    response = _call(_client(tmp_path, monkeypatch), method, path, json={})
    assert response.status_code == 401
    assert response.get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize("method,path", V1_ROUTES)
def test_v1_fails_closed_when_the_configured_token_is_empty(
    tmp_path, monkeypatch, method, path
):
    """An unset api.token must authorise nothing, including an empty bearer."""
    client = _client(tmp_path, monkeypatch, token="")
    assert _call(client, method, path, json={}, headers={"Authorization": "Bearer "}).status_code == 401
    assert _call(client, method, path, json={}, headers={"Authorization": "Bearer x"}).status_code == 401
    assert _call(client, method, path, json={}).status_code == 401


def test_v1_rejects_a_wrong_token(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/v1/chat", json={"message": "hi"}, headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


def test_v1_rejects_a_correct_token_under_the_wrong_scheme(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/v1/chat", json={"message": "hi"}, headers={"Authorization": f"Basic {TOKEN}"}
    )
    assert response.status_code == 401


def test_v1_rejects_a_token_prefix(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/v1/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {TOKEN[:-1]}"}
    )
    assert response.status_code == 401


def test_v1_rejects_when_no_config_is_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(srv._rt, "config", None)
    response = srv.app.test_client().post("/v1/chat", json={"message": "hi"}, headers=AUTH)
    assert response.status_code == 401


def test_health_is_unauthenticated(tmp_path, monkeypatch):
    response = _client(tmp_path, monkeypatch).get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


# ---------------------------------------------------------------------------
# /whatsapp/incoming
# ---------------------------------------------------------------------------


def test_whatsapp_webhook_rejects_a_non_loopback_caller(tmp_path, monkeypatch):
    received: list[dict] = []
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "whatsapp", received.append)

    response = client.post(
        "/whatsapp/incoming",
        json={"from": "attacker", "body": "hi"},
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "forbidden"}
    assert received == []


def test_whatsapp_webhook_accepts_loopback(tmp_path, monkeypatch):
    received: list[dict] = []
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "whatsapp", received.append)

    response = client.post(
        "/whatsapp/incoming",
        json={"from": "1555", "body": "hi"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 200
    assert received == [{"from": "1555", "body": "hi"}]


def test_whatsapp_webhook_survives_a_handler_that_raises(tmp_path, monkeypatch):
    def boom(payload):
        raise RuntimeError("bridge exploded")

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "whatsapp", boom)

    response = client.post("/whatsapp/incoming", json={"body": "hi"})

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /v1/chat
# ---------------------------------------------------------------------------


def test_chat_runs_one_turn_on_the_ios_session(tmp_path, monkeypatch):
    seen: list[tuple[str, str]] = []

    def agent(message: str, session_key: str, timeout: float) -> str:
        seen.append((message, session_key))
        return "pong"

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "agent", agent)

    response = client.post("/v1/chat", json={"message": "ping"}, headers=AUTH)

    assert response.status_code == 200
    assert response.get_json() == {"reply": "pong", "session": srv.IOS_SESSION}
    assert seen == [("ping", srv.IOS_SESSION)]


def test_chat_requires_a_message(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "agent", lambda *a: "unreachable")
    response = client.post("/v1/chat", json={"message": "   "}, headers=AUTH)
    assert response.status_code == 400


def test_chat_reports_503_when_no_agent_is_wired(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/v1/chat", json={"message": "ping"}, headers=AUTH)
    assert response.status_code == 503


def test_chat_maps_a_bridge_timeout_to_504(tmp_path, monkeypatch):
    def agent(message, session_key, timeout):
        raise TimeoutError

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "agent", agent)
    assert client.post("/v1/chat", json={"message": "ping"}, headers=AUTH).status_code == 504


def test_chat_does_not_leak_an_agent_traceback(tmp_path, monkeypatch):
    def agent(message, session_key, timeout):
        raise RuntimeError("NIM api key sk-secret is invalid")

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "agent", agent)

    response = client.post("/v1/chat", json={"message": "ping"}, headers=AUTH)

    assert response.status_code == 502
    assert "sk-secret" not in response.get_data(as_text=True)


def test_webhook_rejects_a_name_outside_the_allowed_charset(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "agent", lambda *a: "ok")
    assert client.post("/v1/webhook/Shortcut", json={"message": "x"}, headers=AUTH).status_code == 400
    assert client.post("/v1/webhook/-lead", json={"message": "x"}, headers=AUTH).status_code == 400


def test_webhook_runs_on_its_own_session(tmp_path, monkeypatch):
    seen: list[str] = []
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv._rt, "agent", lambda m, k, t: seen.append(k) or "ok")

    response = client.post("/v1/webhook/leaving-school", json={"input": "home"}, headers=AUTH)

    assert response.status_code == 200
    assert seen == ["webhook:leaving-school"]


# ---------------------------------------------------------------------------
# /v1/tasks
# ---------------------------------------------------------------------------


class _FakeTaskStore:
    def __init__(self):
        self.tasks = [
            {
                "id": "task-1",
                "title": "Ship dashboard",
                "done": False,
                "priority": "high",
                "source": "manual",
                "subject": None,
                "notes": None,
                "due": "2026-08-02T00:00:00.000Z",
                "classroom_id": None,
                "time_estimate_min": 25,
                "time_actual_min": None,
                "started_at": None,
            }
        ]

    def get_all(self):
        return self.tasks


class _FakeTaskState:
    def get(self):
        return {"mode": "working", "current_task": "Ship dashboard"}

    def get_session(self):
        return {"task_id": "task-1", "title": "Ship dashboard", "elapsed_min": 7}

    def get_work_session_duration_minutes(self):
        return None

    def get_lock_in_duration_minutes(self):
        return 7


def _task_client(tmp_path, monkeypatch):
    store = _FakeTaskStore()
    state = _FakeTaskState()
    monkeypatch.setattr(srv, "_task_dependencies", lambda: (store, state, object(), object()))
    return _client(tmp_path, monkeypatch), store, state


class _CountingTaskStore(_FakeTaskStore):
    """Counts Google reads, and can be told to start failing them."""

    def __init__(self):
        super().__init__()
        self.reads = 0
        self.broken = False

    def get_all(self):
        self.reads += 1
        if self.broken:
            raise RuntimeError("google work account needs re-authentication")
        return self.tasks

    def add_task(self, title, **kwargs):
        task = {"id": "task-{}".format(len(self.tasks) + 1), "title": title}
        self.tasks.append(task)
        return task


class _FakeLog:
    def append(self, *args, **kwargs):
        pass


def _counting_client(tmp_path, monkeypatch):
    store, state = _CountingTaskStore(), _FakeTaskState()
    monkeypatch.setattr(
        srv, "_task_dependencies", lambda: (store, state, _FakeLog(), object())
    )
    return _client(tmp_path, monkeypatch), store


def test_polling_the_dashboard_does_not_re_read_google_every_time(tmp_path, monkeypatch):
    """The widgets poll every few seconds; Google is rate-limited."""
    client, store = _counting_client(tmp_path, monkeypatch)

    first = client.get("/v1/tasks", headers=AUTH).get_json()
    second = client.get("/v1/tasks", headers=AUTH).get_json()

    assert store.reads == 1
    assert first["cached"] is False and second["cached"] is True
    assert second["tasks"] == first["tasks"]


def test_live_work_state_is_not_frozen_by_the_task_cache(tmp_path, monkeypatch):
    """A cache hit must still report the current minute counter."""
    client, store = _counting_client(tmp_path, monkeypatch)
    client.get("/v1/tasks", headers=AUTH)

    body = client.get("/v1/tasks", headers=AUTH).get_json()

    assert body["cached"] is True
    assert body["state"]["lock_in_minutes"] == 7


def test_fresh_forces_a_google_read(tmp_path, monkeypatch):
    client, store = _counting_client(tmp_path, monkeypatch)
    client.get("/v1/tasks", headers=AUTH)
    client.get("/v1/tasks?fresh=1", headers=AUTH)
    assert store.reads == 2


def test_adding_a_task_bypasses_the_cache(tmp_path, monkeypatch):
    """A dashboard still showing the task you just added looks broken."""
    client, store = _counting_client(tmp_path, monkeypatch)
    client.get("/v1/tasks", headers=AUTH)

    response = client.post("/v1/tasks", headers=AUTH, json={"title": "new"})

    assert response.status_code == 200
    assert response.get_json()["cached"] is False
    assert store.reads == 2


def test_a_failed_refresh_serves_the_last_good_list(tmp_path, monkeypatch):
    """Blanking out is indistinguishable from being offline."""
    client, store = _counting_client(tmp_path, monkeypatch)
    client.get("/v1/tasks", headers=AUTH)
    store.broken = True

    response = client.get("/v1/tasks?fresh=1", headers=AUTH)

    assert response.status_code == 200
    body = response.get_json()
    assert body["tasks"][0]["id"] == "task-1"
    assert "re-authentication" in body["error"]


def test_a_failure_with_nothing_cached_is_a_renderable_503(tmp_path, monkeypatch):
    client, store = _counting_client(tmp_path, monkeypatch)
    store.broken = True

    response = client.get("/v1/tasks", headers=AUTH)

    assert response.status_code == 503
    assert response.get_json()["tasks"] == []


def test_tasks_get_returns_the_shared_dashboard_shape(tmp_path, monkeypatch):
    client, _, _ = _task_client(tmp_path, monkeypatch)

    response = client.get("/v1/tasks", headers=AUTH)

    assert response.status_code == 200
    assert response.get_json()["tasks"][0]["id"] == "task-1"
    assert response.get_json()["state"] == {
        "mode": "working",
        "current_task": "Ship dashboard",
        "work_session_minutes": 0,
        "lock_in_minutes": 7,
    }


def test_tasks_add_runs_the_agent_tool_and_returns_the_dashboard(tmp_path, monkeypatch):
    seen = []

    async def execute(self, **kwargs):
        seen.append(kwargs)
        return "Added: Read paper"

    monkeypatch.setattr("argon.tools.tasks.AddTaskTool.execute", execute)
    client, _, _ = _task_client(tmp_path, monkeypatch)

    response = client.post(
        "/v1/tasks",
        headers=AUTH,
        json={
            "title": "Read paper",
            "priority": "medium",
            "due": "2026-08-03",
            "time_estimate_min": 30,
        },
    )

    assert response.status_code == 200
    assert seen[0]["title"] == "Read paper"
    assert seen[0]["time_estimate_min"] == 30


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": "x", "priority": "urgent"},
        {"title": "x", "due": "tomorrow"},
        {"title": "x", "time_estimate_min": True},
    ],
)
def test_tasks_add_rejects_malformed_mutations(tmp_path, monkeypatch, body):
    client, _, _ = _task_client(tmp_path, monkeypatch)
    assert client.post("/v1/tasks", headers=AUTH, json=body).status_code == 400


def test_tasks_patch_runs_the_same_start_tool_the_agent_uses(tmp_path, monkeypatch):
    seen = []

    async def execute(self, **kwargs):
        seen.append(kwargs)
        return "Started: Ship dashboard"

    monkeypatch.setattr("argon.tools.tasks.StartTaskTool.execute", execute)
    client, _, _ = _task_client(tmp_path, monkeypatch)

    response = client.patch("/v1/tasks/task-1", headers=AUTH, json={"action": "start"})

    assert response.status_code == 200
    assert seen == [{"task_id": "task-1"}]


def test_tasks_patch_maps_a_missing_task_to_404(tmp_path, monkeypatch):
    async def execute(self, **kwargs):
        return "No pending task matching 'missing'."

    monkeypatch.setattr("argon.tools.tasks.CompleteTaskTool.execute", execute)
    client, _, _ = _task_client(tmp_path, monkeypatch)

    response = client.patch("/v1/tasks/missing", headers=AUTH, json={"action": "complete"})

    assert response.status_code == 404


def test_stopping_a_stale_dashboard_task_does_not_stop_the_running_one(tmp_path, monkeypatch):
    class State(_FakeTaskState):
        def __init__(self):
            self.ended = False

        def end_session_if_task(self, _task_id):
            return None

    store, state = _FakeTaskStore(), State()
    monkeypatch.setattr(srv, "_task_dependencies", lambda: (store, state, _FakeLog(), object()))
    client = _client(tmp_path, monkeypatch)

    response = client.patch("/v1/tasks/another-task", headers=AUTH, json={"action": "stop"})

    assert response.status_code == 409
    assert state.ended is False


def test_stopping_uses_one_atomic_compare_and_end_operation(tmp_path, monkeypatch):
    class State(_FakeTaskState):
        def __init__(self):
            self.compared = []

        def get_session(self):
            raise AssertionError("endpoint must not check then end in separate calls")

        def end_session(self):
            raise AssertionError("endpoint must compare and end atomically")

        def end_session_if_task(self, task_id):
            self.compared.append(task_id)
            return None

    store, state = _FakeTaskStore(), State()
    monkeypatch.setattr(srv, "_task_dependencies", lambda: (store, state, _FakeLog(), object()))
    client = _client(tmp_path, monkeypatch)

    response = client.patch("/v1/tasks/task-1", headers=AUTH, json={"action": "stop"})

    assert response.status_code == 409
    assert state.compared == ["task-1"]


# ---------------------------------------------------------------------------
# /v1/screentime
# ---------------------------------------------------------------------------


def test_screentime_post_round_trips_through_read_screentime(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    payload = {"apps": [{"name": "Safari", "minutes": 42}], "total": 42}

    response = client.post("/v1/screentime", json=payload, headers=AUTH)

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True

    records = srv.read_screentime(body["date"])
    assert len(records) == 1
    assert records[0]["payload"] == payload
    assert records[0]["received_at"]

    # And the GET endpoint sees the same record.
    history = client.get(f"/v1/screentime?date={body['date']}", headers=AUTH)
    assert history.get_json()["count"] == 1
    assert history.get_json()["records"][0]["payload"] == payload


def test_screentime_appends_rather_than_overwrites(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/v1/screentime", json={"n": 1}, headers=AUTH)
    day = client.post("/v1/screentime", json={"n": 2}, headers=AUTH).get_json()["date"]

    records = srv.read_screentime(day)

    assert [r["payload"]["n"] for r in records] == [1, 2]


def test_screentime_writes_under_argon_home(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    day = client.post("/v1/screentime", json={"n": 1}, headers=AUTH).get_json()["date"]
    assert (tmp_path / "screentime" / f"{day}.jsonl").exists()


def test_screentime_rejects_a_non_object_body(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/v1/screentime", json=[1, 2, 3], headers=AUTH)
    assert response.status_code == 400


def test_screentime_history_rejects_a_malformed_date(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/v1/screentime?date=../../etc/passwd", headers=AUTH)
    assert response.status_code == 400


def test_read_screentime_tolerates_a_torn_line(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    day = "2026-07-30"
    path = tmp_path / "screentime"
    path.mkdir()
    (path / f"{day}.jsonl").write_text(
        json.dumps({"payload": {"n": 1}}) + "\n" + '{"payload": {"n":',
        encoding="utf-8",
    )

    records = srv.read_screentime(day)

    assert [r["payload"]["n"] for r in records] == [1]


def test_read_screentime_rejects_a_malformed_day(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        srv.read_screentime("2026-7-30")


def test_read_screentime_of_an_unreported_day_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    assert srv.read_screentime("2020-01-01") == []


# ---------------------------------------------------------------------------
# /v1/status
# ---------------------------------------------------------------------------


def test_status_returns_the_widget_payload(tmp_path, monkeypatch):
    """Same assembly the agent's get_status tool uses — catches wiring breaks."""
    client = _client(tmp_path, monkeypatch)

    body = client.get("/v1/status", headers=AUTH).get_json()

    assert body["mode"] == "idle"
    assert "school_period" in body


def test_status_plan_payload_contains_only_explicit_blocks(tmp_path, monkeypatch):
    from argon.productivity.plan import DayPlan

    client = _client(tmp_path, monkeypatch)
    stored = DayPlan(tmp_path).set_blocks([{"start": "2pm", "what": "SAT prep"}])
    body = client.get("/v1/status", headers=AUTH).get_json()

    assert body["plan"] == {
        "blocks": [{
            "id": stored[0].id, "start": "14:00", "end": None,
            "what": "SAT prep", "status": "pending",
        }],
    }







# ---------------------------------------------------------------------------
# iOS app contract
#
# The app's Swift structs are non-optional for everything but `since` and
# `expires_at`. A missing key fails the whole /v1/status decode and the app
# silently reports "Offline", so these assert the shape, not just the values.
# ---------------------------------------------------------------------------

DESIRED_KEYS = {"mode", "version", "since", "expires_at", "allow_early_end", "reason"}
ACTUAL_KEYS = {"mode", "version", "shielded", "last_seen"}


def test_status_carries_the_ios_block_on_a_fresh_install(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.get("/v1/status", headers=AUTH).get_json()

    assert DESIRED_KEYS <= body["ios"]["desired"].keys()
    assert ACTUAL_KEYS <= body["ios"]["actual"].keys()
    # Non-optional on the Swift side — nulls here break the decode.
    assert body["ios"]["desired"]["reason"] == ""
    assert body["ios"]["desired"]["allow_early_end"] is True
    assert body["ios"]["actual"]["shielded"] is False


def test_status_still_carries_the_keys_the_app_reads(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.get("/v1/status", headers=AUTH).get_json()
    assert {"mode", "current_task", "work_session_minutes", "lock_in_minutes"} <= body.keys()


def test_a_published_mode_reaches_status(tmp_path, monkeypatch):
    from argon.ios import mode as ios_mode

    client = _client(tmp_path, monkeypatch)
    ios_mode.set_mode("lock_in", duration_min=60, allow_early_end=False, reason="pset")

    desired = client.get("/v1/status", headers=AUTH).get_json()["ios"]["desired"]
    assert desired["mode"] == "lock_in"
    assert desired["allow_early_end"] is False
    assert desired["reason"] == "pset"
    # Swift parses 0 or 3 fractional digits; Python's default 6 decodes to nil
    # and the phone would never release the shield on its own.
    assert "." not in desired["expires_at"]


def test_ios_mode_endpoint_matches_the_status_block(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    standalone = client.get("/v1/ios/mode", headers=AUTH).get_json()
    embedded = client.get("/v1/status", headers=AUTH).get_json()["ios"]["desired"]
    assert standalone == embedded


def test_the_phone_can_report_what_it_applied(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/v1/ios/state",
        headers=AUTH,
        json={"mode": "lock_in", "version": 3, "shielded": True,
              "applied_at": "2026-07-31T10:00:00-07:00", "battery": 0.62},
    )
    assert response.status_code == 200

    actual = client.get("/v1/status", headers=AUTH).get_json()["ios"]["actual"]
    assert actual["mode"] == "lock_in"
    assert actual["shielded"] is True
    assert actual["last_seen"] is not None


def test_an_empty_state_report_is_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/v1/ios/state", headers=AUTH, json={}).status_code == 400


def test_device_registration_stores_the_token(tmp_path, monkeypatch):
    from argon.ios import mode as ios_mode

    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/v1/ios/register",
        headers=AUTH,
        json={"device_token": "deadbeef", "environment": "sandbox", "app_version": "1.0"},
    )
    assert response.status_code == 200
    assert ios_mode._read("device.json", {})["device_token"] == "deadbeef"


def test_registration_without_a_token_is_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/v1/ios/register", headers=AUTH, json={}).status_code == 400


def test_the_override_endpoint_releases_and_holds(tmp_path, monkeypatch):
    from argon.ios import mode as ios_mode

    client = _client(tmp_path, monkeypatch)
    ios_mode.set_mode("lock_in", duration_min=600, allow_early_end=False, reason="pset")

    response = client.post("/v1/ios/override", headers=AUTH, json={"minutes": 60})

    assert response.status_code == 200
    assert response.get_json()["active"] is True
    assert client.get("/v1/ios/mode", headers=AUTH).get_json()["mode"] == "off"
    with pytest.raises(ios_mode.OverrideActive):
        ios_mode.set_mode("lock_in", duration_min=30, reason="nope")


def test_the_override_endpoint_can_clear(tmp_path, monkeypatch):
    from argon.ios import mode as ios_mode

    client = _client(tmp_path, monkeypatch)
    client.post("/v1/ios/override", headers=AUTH, json={"minutes": 60})
    client.post("/v1/ios/override", headers=AUTH, json={"clear": True})
    assert ios_mode.override_status()[0] is False


def test_an_override_with_no_minutes_uses_the_configured_default(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/v1/ios/override", headers=AUTH, json={}).status_code == 200


# ---------------------------------------------------------------------------
# The plan is what decides when Argon speaks, so it has to be tickable from the
# desk. Without this the readout says one thing, the gate believes another, and
# he gets asked how a block went that he closed an hour ago.
# ---------------------------------------------------------------------------


def _plan_client(tmp_path, monkeypatch):
    from argon.productivity.plan import DayPlan

    client = _client(tmp_path, monkeypatch)
    plan = DayPlan(tmp_path)
    stored = plan.set_blocks([
        {"start": "5pm", "end": "6pm", "what": "SAT prep"},
        {"start": "7pm", "what": "UCLA work"},
    ])
    return client, plan, stored


def test_a_block_can_be_marked_done_from_the_desktop(tmp_path, monkeypatch):
    client, plan, stored = _plan_client(tmp_path, monkeypatch)

    reply = client.patch(f"/v1/plan/{stored[0].id}", json={"status": "done"},
                         headers={"Authorization": "Bearer s3cret-token"})

    assert reply.status_code == 200
    assert [b.status for b in plan.blocks()] == ["done", "pending"]


def test_an_unknown_block_is_a_404(tmp_path, monkeypatch):
    client, _, _ = _plan_client(tmp_path, monkeypatch)
    reply = client.patch("/v1/plan/nosuchid", json={"status": "done"},
                         headers={"Authorization": "Bearer s3cret-token"})
    assert reply.status_code == 404


def test_a_bad_status_is_refused(tmp_path, monkeypatch):
    client, plan, stored = _plan_client(tmp_path, monkeypatch)
    reply = client.patch(f"/v1/plan/{stored[0].id}", json={"status": "finished-ish"},
                         headers={"Authorization": "Bearer s3cret-token"})
    assert reply.status_code == 400
    assert [b.status for b in plan.blocks()] == ["pending", "pending"]


def test_it_needs_the_token(tmp_path, monkeypatch):
    client, _, stored = _plan_client(tmp_path, monkeypatch)
    assert client.patch(
        f"/v1/plan/{stored[0].id}", json={"status": "done"}
    ).status_code == 401
