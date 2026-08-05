"""Where unprompted messages go.

Check-ins, cron jobs and heartbeats have no inbound message to reply to, so
they need a remembered destination. That used to be derived by scanning session
files for the newest non-CLI key — which meant the delivery address was a
side effect of a cache. Archiving one stale session file on 2026-08-03 deleted
the only record of the Discord DM, ``pick_target()`` fell back to ``cli``, and
``notify()`` dropped every check-in on the floor for two days while still
logging "Check-in spoke" and recording it in the ledger.

The address is now written down on its own, the moment Niranjan says something
on a real channel. Session files can be archived, trimmed or rotated freely.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

#: Channels a message can actually be pushed to. An allowlist, not a denylist:
#: ``cli``, ``ios``, ``webhook``, ``cron`` and ``heartbeat`` all arrive as
#: inbound turns, and any of them recorded as the target would silently
#: displace the one address Argon can reach Niranjan at.
DELIVERABLE = {"discord", "whatsapp"}

#: Kept for callers that reason about the other direction.
UNREACHABLE = {"cli", "system", "heartbeat", "ios", "webhook", "cron"}


def _path(workspace: Path) -> Path:
    return workspace / "last_target.json"


def remember(workspace: Path, channel: str, chat_id: str) -> None:
    """Record where Niranjan last spoke from. Never raises."""
    if channel not in DELIVERABLE or not chat_id:
        return
    path = _path(workspace)
    try:
        if json.loads(path.read_text(encoding="utf-8")) == {
            "channel": channel, "chat_id": chat_id
        }:
            return  # unchanged; skip the write on every single message
    except (OSError, json.JSONDecodeError):
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"channel": channel, "chat_id": chat_id}), encoding="utf-8"
        )
        logger.info("Delivery target is now {}:{}", channel, chat_id)
    except OSError as exc:
        logger.warning("Could not record the delivery target: {}", exc)


def recall(workspace: Path, enabled: set[str]) -> tuple[str, str] | None:
    """The remembered destination, if its channel is still switched on."""
    try:
        data = json.loads(_path(workspace).read_text(encoding="utf-8"))
        channel, chat_id = data["channel"], data["chat_id"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if channel in enabled and chat_id:
        return channel, chat_id
    return None
