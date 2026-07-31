"""Configuration schema and loading.

Config lives at ``~/.argon/config.json``: camelCase on disk, snake_case in
Python.  Every LLM endpoint Argon talks to is OpenAI-compatible, so providers are
just a name -> {apiKey, apiBase} map and ``agents.defaults.provider`` picks one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pydantic
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings
from pydantic.alias_generators import to_camel

from argon.paths import argon_home, get_config_path

# Convenience defaults, so a provider entry usually needs only an apiKey.
KNOWN_API_BASES: dict[str, str] = {
    "nim": "https://integrate.api.nvidia.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}


class Base(BaseModel):
    """Accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ProviderConfig(Base):
    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None


class AgentDefaults(Base):
    model: str = "openai/gpt-oss-20b"
    fallback_model: str | None = None
    provider: str = "nim"
    max_tokens: int = 8192
    context_window_tokens: int = 65_536
    context_block_limit: int | None = None
    temperature: float = 0.1
    max_tool_iterations: int = 25
    max_tool_result_chars: int = 16_000
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    reasoning_effort: str | None = None
    timezone: str = "America/Los_Angeles"
    # Clear raw chat history after N hours idle (0 = never).
    idle_session_reset_hours: float = 0.167
    # Hard cap on raw messages kept per session (0 = token-based only).
    # Left at 0 a single cron session grew to 14MB and was rewritten on every save.
    max_session_messages: int = 60


class AgentsConfig(Base):
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ChannelsConfig(Base):
    """Per-channel settings (discord, whatsapp) live in extra fields as dicts."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    send_progress: bool = True
    send_tool_hints: bool = False
    send_max_retries: int = Field(default=3, ge=0, le=10)


class LockdownConfig(Base):
    """Phone lockdown — mail to an SMS gateway fires an iOS Shortcut."""

    email: str = ""
    password: str = ""
    phone: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.email and self.password and self.phone)


class ApiConfig(Base):
    """Local HTTP surface: WhatsApp bridge webhook + the iOS app."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 3995
    token: str = ""  # bearer token required by /v1/* endpoints


class HeartbeatConfig(Base):
    enabled: bool = True
    interval_s: int = 30 * 60
    keep_recent_messages: int = 8
    model: str | None = None
    provider: str | None = None


class GatewayConfig(Base):
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class GoogleConfig(Base):
    enabled: bool = True


class WebSearchConfig(Base):
    provider: str = "duckduckgo"
    api_key: str = ""
    base_url: str = ""
    max_results: int = 5


class WebToolsConfig(Base):
    enable: bool = True
    proxy: str | None = None
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(Base):
    enable: bool = False
    timeout: int = 60
    path_append: str = ""


class MCPServerConfig(Base):
    type: Literal["stdio", "sse", "streamableHttp"] | None = None
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    tool_timeout: int = 30
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])


class ToolsConfig(Base):
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class Config(BaseSettings):
    """Root configuration."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    lockdown: LockdownConfig = Field(default_factory=LockdownConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    google: GoogleConfig = Field(default_factory=GoogleConfig)

    model_config = ConfigDict(env_prefix="ARGON_", env_nested_delimiter="__")

    @property
    def workspace_path(self) -> Path:
        """Data root. Code and state are deliberately not the same directory."""
        return argon_home()

    def resolve_provider(self, name: str | None = None) -> tuple[str, ProviderConfig]:
        """Return (name, config) for the active provider.

        Falls back to the first provider holding credentials, so a mistyped name
        degrades to something usable instead of a hard crash at startup.
        """
        wanted = name or self.agents.defaults.provider
        p = self.providers.get(wanted)
        if p is not None and (p.api_key or p.api_base):
            return wanted, p
        for candidate, cfg in self.providers.items():
            if cfg.api_key or cfg.api_base:
                logger.warning(
                    "Provider {!r} has no credentials; using {!r}", wanted, candidate
                )
                return candidate, cfg
        return wanted, p or ProviderConfig()

    def api_base_for(self, name: str) -> str | None:
        p = self.providers.get(name)
        if p and p.api_base:
            return p.api_base
        return KNOWN_API_BASES.get(name)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_config_path_override: Path | None = None


def set_config_path(path: Path) -> None:
    global _config_path_override
    _config_path_override = path


def config_path() -> Path:
    return _config_path_override or get_config_path()


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a pre-rename (nanobot) config in memory. Never writes back."""
    defaults = data.get("agents", {}).get("defaults", {})
    # workspace is no longer configurable — state always lives in ~/.argon.
    defaults.pop("workspace", None)
    defaults.pop("memoryWindow", None)

    # Providers used to be a fixed object with ~24 mostly-empty entries.
    providers = data.get("providers")
    if isinstance(providers, dict):
        data["providers"] = {
            name: cfg
            for name, cfg in providers.items()
            if isinstance(cfg, dict) and (cfg.get("apiKey") or cfg.get("apiBase"))
        }

    # Lockdown credentials used to hide inside the channels blob.
    channels = data.get("channels")
    if isinstance(channels, dict):
        moved = {
            "email": channels.pop("triggerEmail", ""),
            "password": channels.pop("triggerPassword", ""),
            "phone": channels.pop("triggerPhone", ""),
        }
        channels.pop("pushcutToken", None)
        channels.pop("pushcut_token", None)
        if any(moved.values()) and "lockdown" not in data:
            data["lockdown"] = moved

    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data


def load_config(path: Path | None = None) -> Config:
    """Load config from disk, falling back to defaults if absent or invalid."""
    target = path or config_path()
    if target.exists():
        try:
            return Config.model_validate(_migrate(json.loads(target.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            logger.warning("Failed to load config from {}: {} — using defaults", target, e)
    return Config()


def save_config(config: Config, path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(config.model_dump(by_alias=True, mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
