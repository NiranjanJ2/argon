"""Tool name resolution."""

from __future__ import annotations

import pytest

from argon.tools.base import Tool
from argon.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Harmony control tokens
#
# gpt-oss glues them onto the function name. Seen live 6 times in one day, each
# costing a wasted model call plus a "not found" listing every registered tool.
# ---------------------------------------------------------------------------


class _Probe(Tool):
    @property
    def name(self) -> str:
        return "list_tasks"

    @property
    def description(self) -> str:
        return "probe"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs) -> str:
        return "ran"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_Probe())
    return registry


@pytest.mark.parametrize("dirty", [
    "list_tasks<|channel|>commentary",
    "list_tasks<|channel|>analysis",
    "list_tasks<|end|>",
    "  list_tasks<|channel|>commentary  ",
])
def test_a_harmony_polluted_name_still_resolves(dirty):
    name, tool = _registry().resolve(dirty)
    assert tool is not None
    assert name == "list_tasks"


def test_a_clean_name_is_untouched():
    assert _registry().resolve("list_tasks")[1] is not None


def test_a_genuinely_unknown_tool_still_fails():
    name, tool = _registry().resolve("no_such_tool")
    assert tool is None
    assert name == "no_such_tool"


async def test_a_polluted_call_executes_rather_than_erroring():
    result = await _registry().execute("list_tasks<|channel|>commentary", {})
    assert result == "ran"


def test_the_provider_cleans_the_name_before_anyone_sees_it():
    """Logs and the user-facing hint should show the real tool name."""
    from argon.providers.openai_compat import _clean_tool_name

    assert _clean_tool_name("list_tasks<|channel|>commentary") == "list_tasks"
    assert _clean_tool_name("get_status") == "get_status"
    assert _clean_tool_name(None) == ""
