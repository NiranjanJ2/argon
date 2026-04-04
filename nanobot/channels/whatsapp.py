"""WhatsApp channel — bridges whatsapp-web.js subprocess to the nanobot MessageBus.

Setup (one-time):
  1. cd whatsapp_bridge && npm install
  2. nanobot gateway  (bridge auto-starts; scan QR code with phone)
  3. Session is saved — subsequent starts reconnect silently.

Config keys (under channels.whatsapp in config.json):
  enabled       bool    false
  phoneNumber   str     ""       Your phone number, digits only, with country code.
                                 e.g. "16265551234"  (US +1 626-555-1234)
  bridgePort    int     3996     Port the Node.js bridge listens on.
  bridgeDir     str     ""       Override path to whatsapp_bridge/ directory.
                                 Defaults to auto-detected repo location.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Base

# ── Module-level state for Flask→asyncio bridge ───────────────────────────────
# Flask runs in a daemon thread; we hand messages to the async channel via these.
# Wrapped in a list so _enqueue_from_flask always sees the current value.
_wa_state: list = [None, None]  # [loop, queue]


def _enqueue_from_flask(payload: dict) -> None:
    """Called by the Flask webhook route (sync thread) to push a message into the async channel."""
    loop, queue = _wa_state
    if loop is None or queue is None:
        logger.warning("WhatsApp: received message but channel not running yet — dropped.")
        return
    loop.call_soon_threadsafe(queue.put_nowait, payload)


# ── Config ────────────────────────────────────────────────────────────────────

class WhatsAppConfig(Base):
    """WhatsApp channel configuration."""

    enabled: bool = False
    phone_number: str = Field(default="", alias="phoneNumber")
    bridge_port: int = Field(default=3996, alias="bridgePort")
    bridge_dir: str = Field(default="", alias="bridgeDir")

    # allow_from mirrors the base-class pattern; defaults to phone_number when set.
    allow_from: list[str] = Field(default_factory=list, alias="allowFrom")

    model_config = {"populate_by_name": True}

    def effective_allow_from(self) -> list[str]:
        if self.allow_from:
            return self.allow_from
        if self.phone_number:
            return [self.phone_number]
        return []


# ── Channel ───────────────────────────────────────────────────────────────────

class WhatsAppChannel(BaseChannel):
    """WhatsApp channel via whatsapp-web.js bridge subprocess."""

    name = "whatsapp"
    display_name = "WhatsApp"

    # How long to wait for bridge to start before giving up (seconds)
    _BRIDGE_STARTUP_TIMEOUT = 30
    # Seconds between bridge health checks / restart attempts
    _BRIDGE_RESTART_DELAY = 5

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "phoneNumber": "",
            "bridgePort": 3996,
            "bridgeDir": "",
            "allowFrom": [],
        }

    def __init__(self, config: Any, bus: MessageBus) -> None:
        if isinstance(config, dict):
            config = WhatsAppConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self._bridge_proc: subprocess.Popen | None = None
        self._http: httpx.AsyncClient | None = None

    # ── Public interface ──────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self.config.phone_number and not self.config.allow_from:
            logger.error("WhatsApp: phoneNumber not set — channel disabled.")
            return

        # Register ourselves with the Flask app so the webhook route can reach us.
        try:
            from nanobot.dashboard.app import register_whatsapp_handler
            register_whatsapp_handler(_enqueue_from_flask)
        except Exception as e:
            logger.warning("WhatsApp: could not register Flask webhook: {}", e)

        # Set up the asyncio bridge for the Flask thread.
        _wa_state[0] = asyncio.get_running_loop()
        _wa_state[1] = asyncio.Queue()

        self._http = httpx.AsyncClient(timeout=10)
        self._running = True

        bridge_dir = self._resolve_bridge_dir()
        if bridge_dir is None:
            logger.error(
                "WhatsApp: whatsapp_bridge/ not found. "
                "Run 'cd whatsapp_bridge && npm install' first."
            )
            return

        # Run bridge + message-consumer concurrently.
        await asyncio.gather(
            self._run_bridge(bridge_dir),
            self._consume_queue(_wa_state[1]),
        )

    async def stop(self) -> None:
        self._running = False
        self._kill_bridge()
        if self._http:
            await self._http.aclose()
            self._http = None

    async def send(self, msg: OutboundMessage) -> None:
        if self._http is None:
            logger.warning("WhatsApp: not running — cannot send.")
            return

        # Resolve destination: use chat_id from the message (the sender's @c.us id)
        to = msg.chat_id
        if not to:
            logger.warning("WhatsApp: outbound message has no chat_id — dropped.")
            return

        content = msg.content or ""
        if not content:
            return

        port = self.config.bridge_port
        try:
            resp = await self._http.post(
                f"http://127.0.0.1:{port}/send",
                json={"to": to, "body": content},
            )
            if resp.status_code != 200:
                logger.warning("WhatsApp bridge /send returned {}: {}", resp.status_code, resp.text)
        except Exception as e:
            logger.error("WhatsApp send failed: {}", e)

    # ── allow_from override (strips @c.us suffix) ─────────────────────────────

    def is_allowed(self, sender_id: str) -> bool:
        allow_list = self.config.effective_allow_from()
        if not allow_list:
            logger.warning("WhatsApp: allowFrom/phoneNumber not set — all access denied.")
            return False
        if "*" in allow_list:
            return True
        # Normalize: strip @c.us / @g.us so users can put bare digits in config
        bare = sender_id.split("@")[0]
        return bare in allow_list or sender_id in allow_list

    # ── Internal: bridge subprocess ───────────────────────────────────────────

    async def _run_bridge(self, bridge_dir: Path) -> None:
        """Start the Node.js bridge and restart it if it dies."""
        while self._running:
            logger.info("WhatsApp: starting bridge subprocess in {}", bridge_dir)
            self._bridge_proc = await asyncio.get_running_loop().run_in_executor(
                None, self._launch_bridge, bridge_dir
            )
            if self._bridge_proc is None:
                logger.error("WhatsApp: bridge failed to launch — retrying in {}s.", self._BRIDGE_RESTART_DELAY)
                await asyncio.sleep(self._BRIDGE_RESTART_DELAY)
                continue

            # Wait for bridge to become healthy
            if not await self._wait_for_bridge():
                logger.warning("WhatsApp: bridge didn't become healthy — restarting.")
                self._kill_bridge()
                await asyncio.sleep(self._BRIDGE_RESTART_DELAY)
                continue

            logger.info("WhatsApp: bridge healthy.")

            # Monitor until it dies
            while self._running:
                ret = self._bridge_proc.poll()
                if ret is not None:
                    logger.warning("WhatsApp: bridge exited with code {} — restarting.", ret)
                    break
                await asyncio.sleep(2)

            self._kill_bridge()
            if self._running:
                await asyncio.sleep(self._BRIDGE_RESTART_DELAY)

    def _launch_bridge(self, bridge_dir: Path) -> subprocess.Popen | None:
        """Synchronous: spawn the bridge process."""
        node_script = bridge_dir / "index.js"
        if not node_script.exists():
            logger.error("WhatsApp: {} not found.", node_script)
            return None

        node_exe = self._find_node()
        if node_exe is None:
            logger.error("WhatsApp: node not found in PATH. Install Node.js >= 18.")
            return None

        auth_dir = str(bridge_dir / "wwebjs_auth")
        env_extras = {
            "WA_BRIDGE_PORT": str(self.config.bridge_port),
            "WA_DATA_PATH": auth_dir,
            "NANOBOT_WEBHOOK_URL": "http://127.0.0.1:3995/whatsapp/incoming",
        }
        import os
        env = {**os.environ, **env_extras}

        try:
            proc = subprocess.Popen(
                [node_exe, str(node_script)],
                cwd=str(bridge_dir),
                env=env,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            return proc
        except Exception as e:
            logger.error("WhatsApp: failed to start bridge: {}", e)
            return None

    async def _wait_for_bridge(self) -> bool:
        """Wait for the bridge HTTP server to come up (not for WA connection — that may wait on QR).

        Returns True once the HTTP server responds to /health.
        WA connection itself is async: the bridge stays in 'initializing' until the user
        scans the QR code (first run) or auto-reconnects (subsequent runs).
        We don't time out on that — messages simply won't arrive until WA is connected.
        """
        port = self.config.bridge_port
        deadline = asyncio.get_running_loop().time() + self._BRIDGE_STARTUP_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            if not self._running:
                return False
            try:
                resp = await self._http.get(f"http://127.0.0.1:{port}/health")
                if resp.status_code == 200:
                    return True  # HTTP server is up; WA connection state doesn't matter here
            except Exception:
                pass
            await asyncio.sleep(1)
        return False

    def _kill_bridge(self) -> None:
        if self._bridge_proc and self._bridge_proc.poll() is None:
            try:
                self._bridge_proc.terminate()
                self._bridge_proc.wait(timeout=5)
            except Exception:
                try:
                    self._bridge_proc.kill()
                except Exception:
                    pass
        self._bridge_proc = None

    # ── Internal: message consumer ────────────────────────────────────────────

    async def _consume_queue(self, queue: asyncio.Queue) -> None:
        """Read incoming WhatsApp messages from the Flask-bridged queue."""
        while self._running:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            sender_raw: str = payload.get("from", "")
            body: str = payload.get("body", "")

            if not sender_raw or not body:
                continue

            # Group chats end in @g.us — ignore them (private channel only)
            if sender_raw.endswith("@g.us"):
                continue

            logger.debug("WhatsApp inbound from {}: {!r}", sender_raw, body[:60])

            await self._handle_message(
                sender_id=sender_raw,
                chat_id=sender_raw,    # reply to the same chat
                content=body,
                metadata={"timestamp": payload.get("timestamp"), "type": payload.get("type")},
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_bridge_dir(self) -> Path | None:
        """Return path to whatsapp_bridge/ directory, or None if not found."""
        if self.config.bridge_dir:
            p = Path(self.config.bridge_dir).expanduser()
            return p if (p / "index.js").exists() else None

        # Auto-detect: walk up from this file to find the repo root.
        # Works for editable installs (pip install -e .) where __file__ is the real source.
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "whatsapp_bridge"
            if (candidate / "index.js").exists():
                return candidate

        return None

    @staticmethod
    def _find_node() -> str | None:
        """Return the path to the node executable, or None."""
        import shutil
        return shutil.which("node")
