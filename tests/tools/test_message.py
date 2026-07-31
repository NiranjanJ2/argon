"""The message tool always sends to the conversation in flight.

Upstream let the model choose ``channel``/``chat_id``; it once passed the literal
string "Niranjan" as a Discord chat id and crashed the send. Argon has one user,
so the destination comes from ``set_context`` and nothing the model says can
move it.
"""

from __future__ import annotations

from argon.core.bus import OutboundMessage
from argon.tools.message import MessageTool


def _tool() -> tuple[MessageTool, list[OutboundMessage]]:
    sent: list[OutboundMessage] = []

    async def send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool()
    tool.set_send_callback(send)
    return tool, sent


async def test_sends_to_the_context_set_by_set_context():
    tool, sent = _tool()
    tool.set_context("discord", "chat-1", "msg-9")

    result = await tool.execute(content="hello")

    assert result == "Sent."
    assert len(sent) == 1
    assert (sent[0].channel, sent[0].chat_id) == ("discord", "chat-1")
    assert sent[0].content == "hello"
    assert sent[0].metadata == {"message_id": "msg-9"}


async def test_ignores_any_destination_the_model_tries_to_pass():
    tool, sent = _tool()
    tool.set_context("discord", "chat-1")

    result = await tool.execute(
        content="hello",
        channel="email",
        chat_id="Niranjan",
        to="someone@example.com",
    )

    assert result == "Sent."
    assert (sent[0].channel, sent[0].chat_id) == ("discord", "chat-1")


async def test_context_changes_redirect_later_sends():
    tool, sent = _tool()
    tool.set_context("discord", "chat-1")
    await tool.execute(content="a")
    tool.set_context("whatsapp", "chat-2")
    await tool.execute(content="b")

    assert [(m.channel, m.chat_id) for m in sent] == [
        ("discord", "chat-1"),
        ("whatsapp", "chat-2"),
    ]


async def test_errors_when_no_context_is_set():
    tool, sent = _tool()

    result = await tool.execute(content="hello")

    assert result.startswith("Error")
    assert "no active conversation" in result
    assert sent == []


async def test_errors_when_the_context_is_only_half_set():
    tool, sent = _tool()
    tool.set_context("discord", "")

    assert (await tool.execute(content="hello")).startswith("Error")
    assert sent == []


async def test_errors_when_no_send_callback_is_wired():
    tool = MessageTool()
    tool.set_context("discord", "chat-1")

    result = await tool.execute(content="hello")

    assert result == "Error: message sending not configured"


async def test_send_failure_is_reported_not_raised():
    async def boom(msg: OutboundMessage) -> None:
        raise RuntimeError("discord 500")

    tool = MessageTool()
    tool.set_context("discord", "chat-1")
    tool.set_send_callback(boom)

    result = await tool.execute(content="hello")

    assert result == "Error sending message: discord 500"
    assert tool._sent_in_turn is False


async def test_media_is_attached_and_counted():
    tool, sent = _tool()
    tool.set_context("discord", "chat-1")

    result = await tool.execute(content="here", media=["/tmp/a.png", "/tmp/b.png"])

    assert result == "Sent with 2 attachment(s)."
    assert sent[0].media == ["/tmp/a.png", "/tmp/b.png"]


async def test_internal_reasoning_is_stripped_before_delivery():
    tool, sent = _tool()
    tool.set_context("discord", "chat-1")

    await tool.execute(content="<think>he is asleep</think>Morning!")

    assert sent[0].content == "Morning!"


async def test_a_send_marks_the_turn_and_start_turn_resets_it():
    tool, _ = _tool()
    tool.set_context("discord", "chat-1")
    assert tool._sent_in_turn is False

    await tool.execute(content="hello")
    assert tool._sent_in_turn is True

    tool.start_turn()
    assert tool._sent_in_turn is False


def test_the_schema_offers_the_model_no_destination():
    props = MessageTool().parameters["properties"]
    assert set(props) == {"content", "media"}
