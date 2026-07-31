"""Per-provider quirks for the OpenAI-compatible client.

Argon talks to exactly one endpoint at a time, chosen by name in config, so this
is not a routing table — it only records the handful of behaviours that differ
between endpoints (env var names, gateway attribution, model-prefix stripping).
An endpoint absent from this table still works; it just gets plain defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    env_key: str = ""  # env var the OpenAI SDK reads for the key
    display_name: str = ""
    default_api_base: str = ""
    is_gateway: bool = False  # routes arbitrary models (OpenRouter)
    strip_model_prefix: bool = False  # drop "vendor/" before sending
    supports_max_completion_tokens: bool = False
    supports_prompt_caching: bool = False
    # extra env vars, e.g. (("X_API_KEY", "{api_key}"),)
    env_extras: tuple[tuple[str, str], ...] = ()
    # per-model param overrides, e.g. (("gpt-oss", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="nim",
        env_key="NVIDIA_API_KEY",
        display_name="NVIDIA NIM",
        default_api_base="https://integrate.api.nvidia.com/v1",
    ),
    ProviderSpec(
        name="groq",
        env_key="GROQ_API_KEY",
        display_name="Groq",
        default_api_base="https://api.groq.com/openai/v1",
    ),
    ProviderSpec(
        name="openai",
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        default_api_base="https://api.openai.com/v1",
        supports_max_completion_tokens=True,
    ),
    ProviderSpec(
        name="openrouter",
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        default_api_base="https://openrouter.ai/api/v1",
        is_gateway=True,
        supports_prompt_caching=True,
    ),
    ProviderSpec(
        name="ollama",
        display_name="Ollama",
        default_api_base="http://localhost:11434/v1",
    ),
)


def find_by_name(name: str | None) -> ProviderSpec | None:
    """Look up a spec by config key. Unknown names are fine — they get defaults."""
    if not name:
        return None
    normalized = name.replace("-", "_").lower()
    return next((s for s in PROVIDERS if s.name == normalized), None)
