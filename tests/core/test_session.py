"""Session truncation must never hand the provider an illegal message list.

Providers reject a ``tool`` message whose ``tool_call_id`` was not declared by a
preceding assistant ``tool_calls``. Both ``get_history`` (read path) and
``retain_recent_legal_suffix`` (persist path) cut history, so both have to land
on a legal boundary. This is also the code that bounds a session file which had
grown to 14MB.
"""

from __future__ import annotations

from argon.core.session import Session, SessionManager


def user(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant(text: str = "", call_id: str | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": text}
    if call_id:
        msg["tool_calls"] = [
            {"id": call_id, "type": "function", "function": {"name": "t", "arguments": "{}"}}
        ]
    return msg


def tool(call_id: str, text: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": "t", "content": text}


def turn(prompt: str, call_id: str, reply: str) -> list[dict]:
    """One full user -> tool-call -> tool-result -> answer exchange."""
    return [user(prompt), assistant("", call_id), tool(call_id), assistant(reply)]


def assert_legal(messages: list[dict]) -> None:
    """No tool result may reference a call that was not declared before it."""
    declared: set[str] = set()
    for msg in messages:
        if msg["role"] == "assistant":
            declared.update(tc["id"] for tc in msg.get("tool_calls", []))
        elif msg["role"] == "tool":
            assert msg["tool_call_id"] in declared, f"orphan tool result {msg['tool_call_id']}"


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------


def test_history_drops_a_leading_orphan_tool_result():
    session = Session(key="discord:1")
    session.messages = [tool("gone"), assistant("", "a"), tool("a"), assistant("done")]

    history = session.get_history()

    assert [m["role"] for m in history] == ["assistant", "tool", "assistant"]
    assert_legal(history)


def test_history_drops_every_leading_orphan_tool_result():
    session = Session(key="discord:1")
    session.messages = [tool("x"), tool("y"), assistant("", "a"), tool("a")]

    history = session.get_history()

    assert [m["role"] for m in history] == ["assistant", "tool"]
    assert_legal(history)


def test_history_starts_at_a_user_message_when_one_is_available():
    session = Session(key="discord:1")
    session.messages = [assistant("stray"), *turn("hi", "a", "hello")]

    history = session.get_history()

    assert history[0] == {"role": "user", "content": "hi"}


def test_history_truncated_to_max_messages_stays_legal():
    session = Session(key="discord:1")
    session.messages = [*turn("one", "a", "1"), *turn("two", "b", "2")]

    # A naive last-2 slice starts on turn two's tool result, whose assistant
    # call has just been cut away.
    history = session.get_history(max_messages=2)

    assert_legal(history)
    assert [m["role"] for m in history] == ["assistant"]


def test_history_preserves_tool_call_fields():
    session = Session(key="discord:1")
    session.messages = turn("hi", "a", "done")

    history = session.get_history()

    assert history[1]["tool_calls"][0]["id"] == "a"
    assert history[2]["tool_call_id"] == "a"
    assert history[2]["name"] == "t"


def test_history_drops_bookkeeping_fields():
    session = Session(key="discord:1")
    session.add_message("user", "hi")

    entry = session.get_history()[0]

    assert "timestamp" not in entry
    assert entry == {"role": "user", "content": "hi"}


def test_history_skips_consolidated_messages():
    session = Session(key="discord:1")
    session.messages = [*turn("one", "a", "1"), *turn("two", "b", "2")]
    session.last_consolidated = 4

    history = session.get_history()

    assert history[0]["content"] == "two"
    assert len(history) == 4


# ---------------------------------------------------------------------------
# retain_recent_legal_suffix
# ---------------------------------------------------------------------------


def test_retain_is_a_no_op_below_the_cap():
    session = Session(key="discord:1")
    session.messages = turn("one", "a", "1")

    session.retain_recent_legal_suffix(10)

    assert len(session.messages) == 4


def test_retain_clears_everything_on_a_non_positive_cap():
    session = Session(key="discord:1")
    session.messages = turn("one", "a", "1")
    session.last_consolidated = 2

    session.retain_recent_legal_suffix(0)

    assert session.messages == []
    assert session.last_consolidated == 0


def test_retain_does_not_split_a_turn():
    session = Session(key="discord:1")
    session.messages = [*turn("one", "a", "1"), *turn("two", "b", "2")]

    # A cut at exactly 3 would land on `assistant(tool_calls=b)`'s tool result.
    session.retain_recent_legal_suffix(3)

    assert_legal(session.messages)
    assert session.messages[0] == user("two")
    assert len(session.messages) == 4


def test_retain_keeps_the_most_recent_turns():
    session = Session(key="discord:1")
    session.messages = [
        *turn("one", "a", "1"),
        *turn("two", "b", "2"),
        *turn("three", "c", "3"),
    ]

    session.retain_recent_legal_suffix(4)

    assert_legal(session.messages)
    assert session.messages[0] == user("three")


def test_retain_drops_an_orphan_tool_result_left_at_the_front():
    session = Session(key="discord:1")
    # A tool result whose assistant call is older than anything retained.
    session.messages = [
        assistant("", "old"),
        tool("old"),
        tool("older-still"),
        assistant("tail"),
    ]

    session.retain_recent_legal_suffix(2)

    assert_legal(session.messages)
    assert session.messages == [assistant("tail")]


def test_retain_rewinds_the_consolidation_marker_by_what_it_dropped():
    session = Session(key="discord:1")
    session.messages = [*turn("one", "a", "1"), *turn("two", "b", "2")]
    session.last_consolidated = 6

    session.retain_recent_legal_suffix(3)

    # Four messages fell off the front, so the marker moves back four.
    assert session.last_consolidated == 2


def test_retain_never_drives_the_marker_negative():
    session = Session(key="discord:1")
    session.messages = [*turn("one", "a", "1"), *turn("two", "b", "2")]
    session.last_consolidated = 1

    session.retain_recent_legal_suffix(3)

    assert session.last_consolidated == 0


def test_retain_bounds_an_autonomous_run_with_no_user_messages():
    session = Session(key="cron:daily")
    session.messages = [user("start")]
    for i in range(200):
        session.messages += [assistant("", f"c{i}"), tool(f"c{i}")]

    session.retain_recent_legal_suffix(60)

    assert len(session.messages) <= 60


def test_retained_history_is_still_legal_after_a_save_and_reload(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("discord:42")
    session.messages = [*turn("one", "a", "1"), *turn("two", "b", "2")]
    session.retain_recent_legal_suffix(3)
    manager.save(session)
    manager.invalidate("discord:42")

    reloaded = manager.get_or_create("discord:42")

    assert reloaded.messages == session.messages
    assert_legal(reloaded.get_history())
