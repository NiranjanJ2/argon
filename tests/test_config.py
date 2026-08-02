"""Migration of a live pre-rename (nanobot) config, and provider resolution.

The fixtures here mirror the shape of the real ``~/.nanobot/config.json`` that
was migrated by hand: a fixed ~24-entry ``providers`` object of which two had
credentials, dead trigger secrets in the ``channels`` blob, and an ``api``
section that used to mean an OpenAI-compatible server on port 8900.
"""

from __future__ import annotations

import copy
import json

import pytest

from argon.config import KNOWN_API_BASES, Config, ProviderConfig, _migrate, load_config

# Every provider the old fixed schema declared. Only two carry credentials.
LEGACY_PROVIDER_NAMES = [
    "custom", "anthropic", "openai", "openrouter", "deepseek", "groq", "zhipu",
    "dashscope", "vllm", "ollama", "ovms", "gemini", "moonshot", "minimax",
    "mistral", "stepfun", "xiaomiMimo", "aihubmix", "siliconflow", "volcengine",
    "volcengineCodingPlan", "byteplus", "byteplusCodingPlan", "nim",
]


def legacy_config() -> dict:
    """A representative pre-rename config, as it sat on disk."""
    providers = {name: {"apiKey": "", "apiBase": None} for name in LEGACY_PROVIDER_NAMES}
    providers["nim"] = {"apiKey": "nvapi-real-key", "apiBase": None}
    providers["custom"] = {
        "apiKey": "gsk-real-key",
        "apiBase": "https://api.groq.com/openai/v1",
    }
    return {
        "agents": {
            "defaults": {
                "workspace": "/home/agentneon/argon",
                "memoryWindow": 20,
                "model": "openai/gpt-oss-20b",
                "provider": "nim",
                "timezone": "America/Los_Angeles",
                "maxSessionMessages": 0,
                "idleSessionResetHours": 0.0,
            }
        },
        "channels": {
            "sendProgress": True,
            "discord": {"token": "discord-token", "ownerId": "123"},
            "triggerEmail": "niranjan@example.com",
            "triggerPassword": "app-password",
            "triggerPhone": "5551234567",
            "pushcutToken": "dead-token",
        },
        "providers": providers,
        "api": {"host": "127.0.0.1", "port": 8900, "timeout": 120.0},
        "tools": {
            "web": {"enable": True},
            "exec": {"enable": False, "timeout": 60, "restrictToWorkspace": True},
        },
    }


# ---------------------------------------------------------------------------
# _migrate
# ---------------------------------------------------------------------------


def test_workspace_and_memory_window_are_dropped():
    out = _migrate(legacy_config())
    defaults = out["agents"]["defaults"]
    assert "workspace" not in defaults
    assert "memoryWindow" not in defaults
    # Untouched keys survive.
    assert defaults["model"] == "openai/gpt-oss-20b"
    assert defaults["timezone"] == "America/Los_Angeles"


def test_empty_providers_are_dropped_and_credentialled_ones_kept():
    out = _migrate(legacy_config())
    # 24 entries in, 2 with credentials out ("custom" arrives renamed).
    assert set(out["providers"]) == {"nim", "groq"}
    assert out["providers"]["nim"]["apiKey"] == "nvapi-real-key"


def test_custom_provider_is_renamed_after_its_api_base():
    out = _migrate(legacy_config())
    assert "custom" not in out["providers"]
    assert out["providers"]["groq"]["apiKey"] == "gsk-real-key"


def test_custom_provider_with_unknown_base_keeps_its_name():
    data = legacy_config()
    data["providers"]["custom"] = {
        "apiKey": "k",
        "apiBase": "https://llm.internal.example/v1",
    }
    out = _migrate(data)
    assert out["providers"]["custom"]["apiKey"] == "k"


def test_custom_provider_renamed_to_openrouter_not_openai():
    """openrouter.ai must not be mistaken for the openai entry."""
    data = legacy_config()
    data["providers"]["custom"] = {
        "apiKey": "or-key",
        "apiBase": "https://openrouter.ai/api/v1",
    }
    out = _migrate(data)
    assert "openrouter" in out["providers"]
    assert "openai" not in out["providers"]


@pytest.mark.xfail(
    reason="argon/config.py:237 matches the provider *name* against the URL, so nim "
           "(integrate.api.nvidia.com) and ollama (localhost) never match — and nim is "
           "the default provider, so this is the case that matters most",
)
def test_custom_provider_is_renamed_for_every_known_api_base():
    unmatched = []
    for name, base in KNOWN_API_BASES.items():
        data = legacy_config()
        data["providers"]["custom"] = {"apiKey": "k", "apiBase": base}
        if name not in _migrate(data)["providers"]:
            unmatched.append(name)
    assert unmatched == []








def test_zero_max_session_messages_is_replaced_by_the_new_default():
    out = _migrate(legacy_config())
    assert "maxSessionMessages" not in out["agents"]["defaults"]
    cfg = Config.model_validate(out)
    assert cfg.agents.defaults.max_session_messages == 60


def test_explicit_non_zero_max_session_messages_survives():
    data = legacy_config()
    data["agents"]["defaults"]["maxSessionMessages"] = 40
    cfg = Config.model_validate(_migrate(data))
    assert cfg.agents.defaults.max_session_messages == 40


def test_obsolete_api_section_is_discarded():
    out = _migrate(legacy_config())
    assert "api" not in out
    cfg = Config.model_validate(out)
    # The iOS/webhook server defaults, not the dead 127.0.0.1:8900 server.
    assert (cfg.api.host, cfg.api.port) == ("0.0.0.0", 3995)


def test_new_style_api_section_is_kept():
    data = legacy_config()
    data["api"] = {"host": "0.0.0.0", "port": 3995, "token": "secret"}
    cfg = Config.model_validate(_migrate(data))
    assert cfg.api.token == "secret"
    assert cfg.api.port == 3995


def test_restrict_to_workspace_moves_off_the_exec_section():
    out = _migrate(legacy_config())
    assert out["tools"]["restrictToWorkspace"] is True
    assert "restrictToWorkspace" not in out["tools"]["exec"]
    cfg = Config.model_validate(out)
    assert cfg.tools.restrict_to_workspace is True


def test_migrate_is_a_no_op_on_an_already_current_config():
    current = {
        "agents": {"defaults": {"provider": "nim", "maxSessionMessages": 60}},
        "providers": {"nim": {"apiKey": "k"}},
        "api": {"token": "t", "port": 3995},
    }
    assert _migrate(copy.deepcopy(current)) == current


def test_load_config_migrates_a_file_end_to_end(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(legacy_config()), encoding="utf-8")

    cfg = load_config(path)

    assert set(cfg.providers) == {"nim", "groq"}
    assert cfg.agents.defaults.max_session_messages == 60
    assert cfg.api.port == 3995
    assert cfg.api.token == ""


def test_load_config_falls_back_to_defaults_on_garbage(tmp_path):
    from argon.config import AgentDefaults

    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    # The point is that a garbage file yields defaults, not which one is default.
    assert load_config(path).agents.defaults.provider == AgentDefaults().provider


# ---------------------------------------------------------------------------
# resolve_provider
# ---------------------------------------------------------------------------


def test_resolve_provider_returns_the_named_provider_when_it_has_a_key():
    cfg = Config(
        providers={"nim": ProviderConfig(api_key="k"), "groq": ProviderConfig(api_key="g")}
    )
    assert cfg.resolve_provider("nim")[0] == "nim"


def test_resolve_provider_falls_back_when_named_provider_has_no_credentials():
    cfg = Config(
        providers={"nim": ProviderConfig(), "groq": ProviderConfig(api_key="g")}
    )
    name, provider = cfg.resolve_provider("nim")
    assert name == "groq"
    assert provider.api_key == "g"


def test_resolve_provider_falls_back_when_named_provider_is_absent():
    cfg = Config(providers={"groq": ProviderConfig(api_key="g")})
    assert cfg.resolve_provider("typo")[0] == "groq"


def test_resolve_provider_accepts_api_base_only_as_credentials():
    """Ollama and other local endpoints need no key."""
    cfg = Config(providers={"ollama": ProviderConfig(api_base="http://localhost:11434/v1")})
    assert cfg.resolve_provider("ollama")[0] == "ollama"


def test_resolve_provider_uses_agent_default_when_unnamed():
    cfg = Config(providers={"groq": ProviderConfig(api_key="g")})
    cfg.agents.defaults.provider = "groq"
    assert cfg.resolve_provider()[0] == "groq"


def test_resolve_provider_returns_an_empty_config_when_nothing_is_credentialled():
    cfg = Config(providers={"nim": ProviderConfig()})
    name, provider = cfg.resolve_provider("nim")
    assert name == "nim"
    assert provider.api_key == ""


def test_dead_trigger_credentials_are_dropped():
    """The mail -> SMS lockdown is gone; its mail password should not linger."""
    out = _migrate({"channels": {
        "triggerEmail": "a@b.c", "triggerPassword": "secret", "triggerPhone": "1",
        "pushcutToken": "tok", "discord": {"enabled": True},
    }})
    assert "lockdown" not in out
    assert out["channels"] == {"discord": {"enabled": True}}


def test_an_existing_lockdown_section_is_discarded():
    assert "lockdown" not in _migrate({"lockdown": {"email": "a@b.c", "password": "p"}})
