from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_productivity_skill_uses_the_registered_focus_tool():
    skill = (ROOT / "argon/skills/productivity/SKILL.md").read_text()

    assert "set_focus_mode" in skill
    assert "send_phone_notification" not in skill


def test_persona_does_not_turn_questions_into_mutations_or_follow_up_nags():
    agents = (ROOT / "argon/prompts/AGENTS.md").read_text()
    soul = (ROOT / "argon/prompts/SOUL.md").read_text()
    soul_words = " ".join(soul.split())

    assert "A question is not permission to change a plan" in agents
    assert "Do not append a question just to keep the conversation moving" in soul_words
    assert "only when he explicitly asks for a reminder" in agents


def test_persona_is_a_secretary_not_a_productivity_coach():
    agents = (ROOT / "argon/prompts/AGENTS.md").read_text()
    soul = (ROOT / "argon/prompts/SOUL.md").read_text()
    productivity = (ROOT / "argon/skills/productivity/SKILL.md").read_text()
    soul_words = " ".join(soul.lower().split())
    agent_words = " ".join(agents.lower().split())

    assert "secretary" in soul_words
    assert "not a productivity coach" in soul_words
    assert "operational facts" in agent_words
    assert "incidental conversation" in agent_words
    assert "hardest thing first" not in soul_words
    assert "slacking" not in soul_words
    assert "pick the starting point" not in productivity.lower()
    assert "anything unfinished to `due='tomorrow'`" not in productivity


def test_threads_are_only_for_ongoing_operational_matters():
    from argon.core.journal import _PROMPT

    agents = (ROOT / "argon/prompts/AGENTS.md").read_text()
    combined = " ".join((agents + _PROMPT).lower().split())

    assert "ongoing operational matter" in combined
    assert "incidental person" in combined
    assert "start one the first time he mentions it" not in combined


def test_track_tool_does_not_invite_a_thread_for_every_mention(tmp_path):
    from argon.tools.threads import TrackThreadTool

    description = " ".join(TrackThreadTool(tmp_path).description.lower().split())

    assert "ongoing operational matter" in description
    assert "incidental" in description
    assert "first time he mentions" not in description
