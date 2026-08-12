"""Filesystem layout.

Everything mutable lives under a single root, ``~/.argon`` (override with
``ARGON_HOME``).  The git checkout holds only code, so it stays disposable::

    ~/.argon/
      config.json
      SOUL.md AGENTS.md HEARTBEAT.md   persona + periodic tasks (user-editable)
      memory/     MEMORY.md, HISTORY.md
      sessions/   <channel>_<chat>.jsonl
      daily/      YYYY-MM-DD.md, state.json
      habits/     habits.json
      schedule/   overrides.json
      google/     client_secrets.json, <account>/token.json
      cron/       jobs.json
      screentime/ usage reports from the iOS app
      skills/     user-authored skills
      media/      inbound attachments
"""

from __future__ import annotations

import os
from pathlib import Path


def argon_home() -> Path:
    """Root of all mutable state. Honours ``ARGON_HOME`` for test isolation."""
    return Path(os.environ.get("ARGON_HOME") or Path.home() / ".argon").expanduser()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_config_path() -> Path:
    return argon_home() / "config.json"


def get_runtime_subdir(name: str) -> Path:
    return ensure_dir(argon_home() / name)


def get_media_dir(channel: str | None = None) -> Path:
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_store() -> Path:
    return get_runtime_subdir("cron") / "jobs.json"


def get_bridge_auth_dir() -> Path:
    """WhatsApp bridge session store — survives repo wipes."""
    return get_runtime_subdir("whatsapp")
