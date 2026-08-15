"""The message tool always sends to the conversation in flight.

Upstream let the model choose ``channel``/``chat_id``; it once passed the literal
string "Niranjan" as a Discord chat id and crashed the send. Argon has one user,
so the destination is never the model's decision.

It was, until now, a pair of fields on a tool shared by every session, rewritten
before each batch of tool calls. Interactive turns run concurrently on purpose,
so two overlapping turns raced on those fields: the later one's ``set_context``
won and the earlier one's reply went to the wrong conversation. The destination
now comes from :mod:`argon.core.turn`, which asyncio copies per task.
"""

from __future__ import annotations

import asyncio

from argon.core import turn
from argon.core.bus import OutboundMessage
from argon.tools.message import MessageTool


def _ctx(channel: str = "discord", chat_id: str = "chat-1", message_id: str | None = None):
    return turn.TurnContext(
        channel=channel, chat_id=chat_id, session_key=f"{channel}:{chat_id}",
        message_id=message_id,
    )


def _tool() -> tuple[MessageTool, list[OutboundMessage]]:
    sent: list[OutboundMessage] = []

    async def send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool()
    tool.set_send_callback(send)
    return tool, sent


async def test_sends_to_the_turn_in_flight():
    tool, sent = _tool()

    with turn.use(_ctx("discord", "chat-1", "msg-9")):
        result = await tool.execute(content="hello")

    assert result == "Sent."
    assert len(sent) == 1
    assert (sent[0].channel, sent[0].chat_id) == ("discord", "chat-1")
    assert sent[0].content == "hello"
    assert sent[0].metadata == {"message_id": "msg-9"}


async def test_ignores_any_destination_the_model_tries_to_pass():
    tool, sent = _tool()

    with turn.use(_ctx("discord", "chat-1")):
        result = await tool.execute(
            content="hello",
            channel="email",
            chat_id="Niranjan",
            to="someone@example.com",
        )

    assert result == "Sent."
    assert (sent[0].channel, sent[0].chat_id) == ("discord", "chat-1")


async def test_a_concurrent_ios_turn_cannot_steal_a_discord_turn_s_destination():
    """The bug this whole mechanism exists for.

    Two turns in flight at once — a Discord message and an iOS/webhook request —
    shared one ``MessageTool``. Whichever called ``set_context`` last owned the
    destination for both, so an answer meant for Discord was delivered to the
    phone bridge and the Discord user got silence.
    """
    tool, sent = _tool()
    started = asyncio.Event()

    async def discord_turn():
        with turn.use(_ctx("discord", "discord-chat")):
            started.set()
            await asyncio.sleep(0.02)   # the iOS turn runs (and used to clobber us)
            await tool.execute(content="for discord")

    async def ios_turn():
        await started.wait()
        with turn.use(_ctx("whatsapp", "ios-chat")):
            await tool.execute(content="for ios")

    await asyncio.gather(discord_turn(), ios_turn())

    delivered = {m.content: (m.channel, m.chat_id) for m in sent}
    assert delivered["for discord"] == ("discord", "discord-chat")
    assert delivered["for ios"] == ("whatsapp", "ios-chat")


async def test_one_turn_cannot_mark_another_turn_as_already_replied():
    """``_sent_in_turn`` was shared state too.

    A send in one turn set it for every turn, so the loop concluded another
    turn had already delivered and dropped its real reply on the floor.
    """
    tool, _sent = _tool()
    discord = _ctx("discord", "discord-chat")
    ios = _ctx("whatsapp", "ios-chat")

    with turn.use(discord):
        await tool.execute(content="for discord")

    assert discord.said == ["for discord"]
    assert ios.said == []


async def test_errors_when_there_is_no_turn():
    tool, sent = _tool()

    result = await tool.execute(content="hello")

    assert result.startswith("Error")
    assert "no active conversation" in result
    assert sent == []


async def test_errors_when_the_turn_has_no_chat_id():
    tool, sent = _tool()

    with turn.use(_ctx("discord", "")):
        assert (await tool.execute(content="hello")).startswith("Error")
    assert sent == []


async def test_errors_when_no_send_callback_is_wired():
    tool = MessageTool()

    with turn.use(_ctx()):
        result = await tool.execute(content="hello")

    assert result == "Error: message sending not configured"


async def test_send_failure_is_reported_not_raised():
    async def boom(msg: OutboundMessage) -> None:
        raise RuntimeError("discord 500")

    tool = MessageTool()
    tool.set_send_callback(boom)

    ctx = _ctx()
    with turn.use(ctx):
        result = await tool.execute(content="hello")

    assert result == "Error sending message: discord 500"
    assert ctx.said == []


async def test_media_is_attached_and_counted():
    tool, sent = _tool()

    with turn.use(_ctx()):
        result = await tool.execute(content="here", media=["/tmp/a.png", "/tmp/b.png"])

    assert result == "Sent with 2 attachment(s)."
    assert sent[0].media == ["/tmp/a.png", "/tmp/b.png"]


async def test_internal_reasoning_is_stripped_before_delivery():
    tool, sent = _tool()

    with turn.use(_ctx()):
        await tool.execute(content="<think>he is asleep</think>Morning!")

    assert sent[0].content == "Morning!"


def test_the_schema_offers_the_model_no_destination():
    props = MessageTool().parameters["properties"]
    assert set(props) == {"content", "media"}


class TestWhatWasActuallySent:
    """The ledger needs the text, not a placeholder.

    ``on_check_in`` recorded "(sent)" whenever the model delivered through this
    tool, so the next check-in was shown "(sent)" as its own history and could
    not tell it had already asked. Nine near-identical "what's your plan?"
    messages in one day followed from that single string.
    """

    def test_the_delivered_text_is_kept(self):
        sent = []

        async def capture(msg):
            sent.append(msg)

        tool = MessageTool(capture)
        ctx = _ctx()

        async def run():
            with turn.use(ctx):
                await tool.execute(content="What's your plan for today?")

        asyncio.run(run())

        assert ctx.said == ["What's your plan for today?"]
        assert sent[0].content == "What's your plan for today?"

    def test_a_failed_send_records_nothing(self):
        async def boom(_msg):
            raise RuntimeError("discord down")

        tool = MessageTool(boom)
        ctx = _ctx()

        async def run():
            with turn.use(ctx):
                await tool.execute(content="never arrived")

        asyncio.run(run())

        assert ctx.said == []
