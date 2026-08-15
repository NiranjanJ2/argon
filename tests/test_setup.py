"""Getting from a clone to a running Argon.

`setup.sh` used to write ~/.nanobot/config.json, install a `nanobot` binary,
configure a provider no longer in use and demand a WhatsApp number that is not.
None of it would have worked on a fresh machine, and nothing tested it.

Everything that needs judgement now lives in `argon init`, which is here.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from argon.cli import app

runner = CliRunner()


def _run(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    result = runner.invoke(app, ["init", "--non-interactive"])
    assert result.exit_code == 0, result.output
    return result


def _config(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


class TestAFreshMachine:
    def test_one_command_produces_a_loadable_config(self, tmp_path, monkeypatch):
        _run(tmp_path, monkeypatch, ARGON_PROVIDER_KEY="gsk_x",
             ARGON_DISCORD_TOKEN="tok", ARGON_DISCORD_USER="123")

        from argon.config import load_config

        cfg = load_config(tmp_path / "config.json")
        assert cfg.api.token
        assert cfg.channels.discord["enabled"] is True

    def test_the_api_token_is_generated_not_asked_for(self, tmp_path, monkeypatch):
        """Nothing tells you this field exists, and without it every /v1 route
        refuses requests while the service still looks healthy."""
        _run(tmp_path, monkeypatch, ARGON_PROVIDER_KEY="gsk_x")
        token = _config(tmp_path)["api"]["token"]

        assert len(token) >= 40

    def test_the_workspace_is_seeded(self, tmp_path, monkeypatch):
        _run(tmp_path, monkeypatch, ARGON_PROVIDER_KEY="gsk_x")

        for name in ("SOUL.md", "AGENTS.md", "HEARTBEAT.md"):
            assert (tmp_path / name).is_file()
        assert (tmp_path / "memory" / "MEMORY.md").exists()

    def test_the_config_is_not_world_readable(self, tmp_path, monkeypatch):
        """It holds the provider key, the Discord token and the API token."""
        _run(tmp_path, monkeypatch, ARGON_PROVIDER_KEY="gsk_x")
        assert (tmp_path / "config.json").stat().st_mode & 0o077 == 0


class TestReRunning:
    def test_it_never_regenerates_the_api_token(self, tmp_path, monkeypatch):
        """Rotating it silently would lock out the widgets and the phone."""
        _run(tmp_path, monkeypatch, ARGON_PROVIDER_KEY="gsk_x")
        first = _config(tmp_path)["api"]["token"]

        _run(tmp_path, monkeypatch, ARGON_PROVIDER_KEY="gsk_x")
        assert _config(tmp_path)["api"]["token"] == first

    def test_it_does_not_clobber_settings(self, tmp_path, monkeypatch):
        _run(tmp_path, monkeypatch, ARGON_PROVIDER_KEY="gsk_x")
        cfg = _config(tmp_path)
        cfg["gateway"] = {"checkins": {"unpromptedFromHour": 16}}
        (tmp_path / "config.json").write_text(json.dumps(cfg))

        _run(tmp_path, monkeypatch, ARGON_PROVIDER_KEY="gsk_x")
        assert _config(tmp_path)["gateway"]["checkins"]["unpromptedFromHour"] == 16

    def test_a_corrupt_config_is_refused_rather_than_overwritten(self, tmp_path, monkeypatch):
        (tmp_path / "config.json").write_text("{not json")
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))

        result = runner.invoke(app, ["init", "--non-interactive"])

        assert result.exit_code == 1
        assert (tmp_path / "config.json").read_text() == "{not json"


class TestPromptTemplateUpdates:
    @staticmethod
    def _bundle(tmp_path, monkeypatch, text: str):
        import importlib.resources

        package = tmp_path / "package"
        prompts = package / "prompts"
        prompts.mkdir(parents=True, exist_ok=True)
        (prompts / "SOUL.md").write_text(text)
        monkeypatch.setattr(importlib.resources, "files", lambda _name: package)
        return prompts

    def test_an_untouched_seed_is_updated_when_the_bundle_changes(self, tmp_path, monkeypatch):
        from argon.utils.helpers import sync_workspace_templates

        prompts = self._bundle(tmp_path, monkeypatch, "first")
        workspace = tmp_path / "workspace"
        sync_workspace_templates(workspace, silent=True)
        (prompts / "SOUL.md").write_text("second")

        changed = sync_workspace_templates(workspace, silent=True)

        assert (workspace / "SOUL.md").read_text() == "second"
        assert changed == ["SOUL.md"]

    def test_a_user_edited_prompt_is_preserved(self, tmp_path, monkeypatch):
        from argon.utils.helpers import sync_workspace_templates

        prompts = self._bundle(tmp_path, monkeypatch, "first")
        workspace = tmp_path / "workspace"
        sync_workspace_templates(workspace, silent=True)
        (workspace / "SOUL.md").write_text("my local instructions")
        (prompts / "SOUL.md").write_text("second")

        changed = sync_workspace_templates(workspace, silent=True)

        assert (workspace / "SOUL.md").read_text() == "my local instructions"
        assert changed == []

    def test_force_updates_a_known_safe_existing_install(self, tmp_path, monkeypatch):
        from argon.utils.helpers import sync_workspace_templates

        self._bundle(tmp_path, monkeypatch, "bundled")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "SOUL.md").write_text("old bundled copy")
        (workspace / "memory").mkdir()
        (workspace / "memory" / "MEMORY.md").write_text("real memory")

        changed = sync_workspace_templates(workspace, silent=True, force=True)

        assert (workspace / "SOUL.md").read_text() == "bundled"
        assert (workspace / "memory" / "MEMORY.md").read_text() == "real memory"
        assert "SOUL.md" in changed


class TestTheApiReportsItsOwnFailure:
    def test_a_taken_port_is_not_reported_as_ok(self, tmp_path, monkeypatch):
        """It logged "HTTP API on ..." and printed OK while the serving thread
        died of "Address already in use" a line later."""
        import socket

        from argon.api.server import start_api_server
        from argon.config import ApiConfig, Config

        held = socket.socket()
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        try:
            cfg = Config(api=ApiConfig(host="127.0.0.1", port=port, token="t"))
            with pytest.raises((OSError, SystemExit)):
                start_api_server(cfg)
        finally:
            held.close()

    def test_a_free_port_still_starts(self, tmp_path, monkeypatch):
        import socket

        from argon.api.server import start_api_server
        from argon.config import ApiConfig, Config

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        start_api_server(Config(api=ApiConfig(host="127.0.0.1", port=port, token="t")))
