"""A tap is the one unambiguous thing Niranjan can say.

"yeah in a bit" has to be interpreted, and interpretation is where Argon
decided he had started work he had not started. A button carries a verb and a
task id chosen by code, so the state change is exactly what he pressed — no
model is involved in the mutation at all.
"""

from __future__ import annotations

import pytest

from argon.services.reminder import OCCASIONS, ReminderService


async def _silent(_p):
    return ""


class TestTheButtonsOffered:
    def _service(self, tmp_path):
        return ReminderService(tmp_path, "America/Los_Angeles", _silent)

    def test_the_follow_up_offers_no_buttons(self, tmp_path):
        """It is about starting at all, so there is no task id to attach.

        The buttons existed for the per-item nudge — "Starting now / Done / Not
        tonight" against one assignment. That question is gone: thirteen of them
        went out over eight days and none was answered. The phone lock is what
        does the forcing now.
        """
        service = self._service(tmp_path)

        assert service._actions_for(OCCASIONS["start"]) is None

    def test_an_imminent_event_gets_no_buttons(self, tmp_path):
        service = self._service(tmp_path)

        assert service._actions_for(OCCASIONS["upcoming"]) is None


class TestPressingOne:
    """The handler runs the real domain operation, and only that."""

    def _runtime(self, tmp_path, monkeypatch):
        from argon import runtime
        from argon.config import Config
        from argon.tools.registry import ToolRegistry

        class Loop:
            instances: list = []

            def __init__(self, _c, _b, _p, *, model=None, **_kw):
                self.model = model
                self.tools = ToolRegistry()
                Loop.instances.append(self)

            async def process_direct(self, *_a, **_kw):
                raise AssertionError("a button press must never spend a model turn")

        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        monkeypatch.setattr(runtime, "AgentLoop", Loop)
        monkeypatch.setattr(runtime, "build_provider", lambda *_a, **_kw: object())
        monkeypatch.setattr(runtime, "get_cron_store", lambda: tmp_path / "cron.json")
        return runtime.build_runtime(Config(google={"enabled": False}))

    async def test_starting_now_starts_the_session_on_that_exact_task(
        self, tmp_path, monkeypatch
    ):
        from argon.productivity.state import DailyState

        monkeypatch.setattr(
            "argon.tasks.local_store.LocalTaskStore.start_task",
            lambda self, task_id: {"id": task_id, "title": "Chemistry reading"},
        )
        rt = self._runtime(tmp_path, monkeypatch)

        result = await rt.on_button(
            {"action": "start", "task_id": "t1", "title": "Chemistry reading"}
        )

        assert "Started Chemistry reading" in result
        session = DailyState(tmp_path).get_session()
        assert session["task_id"] == "t1"
        assert session["title"] == "Chemistry reading"

    async def test_done_completes_it_and_ends_the_session(self, tmp_path, monkeypatch):
        from argon.productivity.state import DailyState

        completed = {}

        def complete(self, task_id, *, actual_min=None):
            completed["id"], completed["min"] = task_id, actual_min
            return {"id": task_id, "title": "Chemistry reading"}

        monkeypatch.setattr(
            "argon.tasks.local_store.LocalTaskStore.complete_task", complete
        )
        rt = self._runtime(tmp_path, monkeypatch)
        DailyState(tmp_path).start_session(task_id="t1", title="Chemistry reading")

        result = await rt.on_button(
            {"action": "complete", "task_id": "t1", "title": "Chemistry reading"}
        )

        assert "off the list" in result
        assert completed["id"] == "t1"
        assert DailyState(tmp_path).get_session() is None

    async def test_a_task_that_vanished_is_reported_not_faked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "argon.tasks.local_store.LocalTaskStore.start_task",
            lambda self, task_id: None,
        )
        rt = self._runtime(tmp_path, monkeypatch)

        result = await rt.on_button(
            {"action": "start", "task_id": "gone", "title": "Chemistry reading"}
        )

        assert "Couldn't find" in result
        from argon.productivity.state import DailyState

        assert DailyState(tmp_path).get_session() is None, (
            "a failed lookup must not start a phantom session"
        )

    async def test_not_tonight_changes_no_state(self, tmp_path, monkeypatch):
        from argon.productivity.state import DailyState

        rt = self._runtime(tmp_path, monkeypatch)

        result = await rt.on_button(
            {"action": "defer", "task_id": "t1", "title": "Chemistry reading"}
        )

        assert "leaving" in result.lower()
        assert DailyState(tmp_path).get_session() is None

    async def test_an_unknown_verb_is_refused(self, tmp_path, monkeypatch):
        rt = self._runtime(tmp_path, monkeypatch)
        assert "Don't know how" in await rt.on_button({"action": "nuke", "task_id": "t1"})


@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["util"]).find_spec("discord") is None,
    reason="discord.py not installed in this environment",
)
class TestTheViewItself:
    def test_a_stranger_cannot_press_them(self):
        """The buttons hang in a channel; the handler still checks who pressed."""
        import inspect

        from argon.channels import discord as discord_channel

        source = inspect.getsource(discord_channel._ArgonActionButton.callback)
        assert "is_allowed" in source
        assert "not allowed" in source

    def test_pressing_retires_the_buttons(self):
        import inspect

        from argon.channels import discord as discord_channel

        source = inspect.getsource(discord_channel._ArgonActionButton.callback)
        assert "disabled = True" in source, "a second press must not start it twice"
