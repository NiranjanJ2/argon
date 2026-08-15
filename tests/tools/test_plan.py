import json

from argon.productivity.plan import DayPlan
from argon.tools.plan import GetDayPlanTool, SetDayPlanTool


def test_set_plan_schema_has_no_decline_state(tmp_path):
    tool = SetDayPlanTool(DayPlan(tmp_path))

    assert "planning" not in tool.parameters["properties"]
    assert "stop asking" not in tool.description.lower()
    assert "check in" not in tool.description.lower()


async def test_an_explicit_empty_plan_clears_existing_blocks(tmp_path):
    plan = DayPlan(tmp_path)
    plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])

    result = await SetDayPlanTool(plan).execute(blocks=[])

    assert result == "Plan cleared."
    assert plan.blocks() == []


async def test_no_plan_input_is_neutral_and_does_not_mutate(tmp_path):
    plan = DayPlan(tmp_path)
    tool = SetDayPlanTool(plan)

    result = await tool.execute()

    assert result == "No explicit plan supplied; nothing changed."
    assert plan.blocks() == []


async def test_get_plan_returns_only_blocks(tmp_path):
    plan = DayPlan(tmp_path)
    stored = plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])

    result = json.loads(await GetDayPlanTool(plan).execute())

    assert result == {
        "blocks": [{
            "id": stored[0].id, "start": "14:00", "end": None,
            "what": "SAT prep", "status": "pending",
        }],
    }
