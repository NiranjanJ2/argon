"""Command-line interface."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import typer
from loguru import logger
from rich.console import Console

from argon import __logo__, __version__
from argon.config import config_path, load_config, save_config, set_config_path

app = typer.Typer(
    name="argon",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} argon — personal assistant",
    no_args_is_help=True,
)
console = Console()


def _load(config: str | None = None):
    if config:
        path = Path(config).expanduser().resolve()
        if not path.exists():
            console.print(f"[red]Config not found: {path}[/red]")
            raise typer.Exit(1)
        set_config_path(path)
    return load_config()


def _version_callback(value: bool):
    if value:
        console.print(f"{__logo__} argon v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=_version_callback, is_eager=True
    ),
):
    """Argon — Niranjan's personal assistant."""


# ---------------------------------------------------------------------------
# gateway
# ---------------------------------------------------------------------------


@app.command()
def gateway(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    verbose: bool = typer.Option(False, "--verbose", help="Debug logging"),
):
    """Run the assistant: channels, cron, heartbeat, check-ins, HTTP API."""
    from argon.api.server import register_agent_handler, start_api_server
    from argon.runtime import build_runtime, run
    from argon.utils.helpers import sync_workspace_templates

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    cfg = _load(config)
    sync_workspace_templates(cfg.workspace_path)
    console.print(f"{__logo__} argon v{__version__} — workspace {cfg.workspace_path}")

    # The API server bridges its Flask threads into this loop, so it has to
    # exist before anything registers a handler against it.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    rt = build_runtime(cfg)

    if cfg.api.enabled:
        from argon.core.bus import OutboundMessage
        from argon.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

        def agent_turn(message: str, session_key: str, timeout_s: float) -> str:
            """Bridge a Flask request thread into the agent's event loop.

            A webhook (phone shortcut, automation) has no screen to read a reply
            on, so its answer is delivered to Niranjan's chat as well as returned.
            """
            to_chat = session_key.startswith("webhook:")
            channel, chat_id = rt.pick_target() if to_chat else ("ios", session_key)

            async def turn() -> str:
                resp = await rt.agent.process_direct(
                    message, session_key=session_key, channel=channel, chat_id=chat_id
                )
                text = (resp.content if resp else "") or ""
                # process_direct returns None when the model already sent via the
                # message tool — don't deliver it twice.
                if to_chat and resp and text and text != EMPTY_FINAL_RESPONSE_MESSAGE:
                    await rt.bus.publish_outbound(
                        OutboundMessage(channel=channel, chat_id=chat_id, content=text)
                    )
                return text

            return asyncio.run_coroutine_threadsafe(turn(), loop).result(timeout=timeout_s)

        register_agent_handler(agent_turn)
        start_api_server(cfg)
        console.print(f"[green]OK[/green] API on {cfg.api.host}:{cfg.api.port}")
        if not cfg.api.token:
            console.print("[yellow]     api.token unset — /v1 endpoints refuse all requests[/yellow]")

    if rt.channels.enabled_channels:
        console.print(f"[green]OK[/green] Channels: {', '.join(rt.channels.enabled_channels)}")
    else:
        console.print("[yellow]No channels enabled[/yellow]")
    jobs = rt.cron.status()["jobs"]
    if jobs:
        console.print(f"[green]OK[/green] Cron: {jobs} job(s)")
    console.print(
        f"[green]OK[/green] Heartbeat every {cfg.gateway.heartbeat.interval_s}s, "
        "check-ins adaptive"
    )

    loop.run_until_complete(run(rt))


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


@app.command()
def chat(
    message: str = typer.Option(None, "--message", "-m", help="Send one message and exit"),
    session: str = typer.Option("cli:direct", "--session", "-s", help="Session key"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show runtime logs"),
):
    """Talk to Argon from the terminal (no Discord round-trip)."""
    from argon.core.bus import MessageBus
    from argon.core.loop import AgentLoop
    from argon.paths import get_cron_store
    from argon.runtime import build_provider
    from argon.services.cron import CronService
    from argon.utils.helpers import sync_workspace_templates

    logger.enable("argon") if logs else logger.disable("argon")
    cfg = _load(config)
    sync_workspace_templates(cfg.workspace_path)
    agent = AgentLoop(
        cfg, MessageBus(), build_provider(cfg), cron_service=CronService(get_cron_store())
    )

    async def ask(text: str) -> str:
        resp = await agent.process_direct(text, session_key=session)
        return (resp.content if resp else "") or ""

    async def once() -> None:
        console.print(await ask(message))
        await agent.close_mcp()

    async def repl() -> None:
        console.print(f"{__logo__} interactive — Ctrl-C or 'exit' to quit\n")
        try:
            while True:
                text = console.input("[bold blue]you[/bold blue] ").strip()
                if not text:
                    continue
                if text.lower() in {"exit", "quit", ":q"}:
                    break
                console.print(f"\n[cyan]{__logo__} argon[/cyan]\n{await ask(text)}\n")
        except (KeyboardInterrupt, EOFError):
            console.print("\nBye.")
        finally:
            await agent.close_mcp()

    asyncio.run(once() if message else repl())


# ---------------------------------------------------------------------------
# google-auth
# ---------------------------------------------------------------------------


@app.command("google-auth")
def google_auth(
    account: str = typer.Argument(help="personal | work | school | trigger"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Authenticate a Google account (prints a URL to open in your browser)."""
    from argon.google.auth import ACCOUNT_SCOPES, GoogleAuth

    cfg = _load(config)
    if account not in ACCOUNT_SCOPES:
        console.print(f"[red]Unknown account '{account}'. Use: {', '.join(ACCOUNT_SCOPES)}[/red]")
        raise typer.Exit(1)

    auth = GoogleAuth(cfg.workspace_path)
    secrets = cfg.workspace_path / "google" / "client_secrets.json"
    if not secrets.exists():
        console.print(f"[red]client_secrets.json not found at {secrets}[/red]")
        console.print(
            "Download it from Google Cloud Console -> APIs & Services -> Credentials "
            "-> OAuth 2.0 Client IDs (Desktop app) -> Download JSON."
        )
        raise typer.Exit(1)

    try:
        auth.authenticate(account)
    except Exception as e:
        console.print(f"[red]Authentication failed: {e}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]OK[/green] {account} authenticated.")
    if not cfg.google.enabled:
        cfg.google.enabled = True
        save_config(cfg)
        console.print("[green]OK[/green] google.enabled set to true.")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Check every integration and report what is actually working.

    Argon fails quietly by design — a dead Google token just means those tools
    never register. This surfaces that instead of letting it rot for months.
    """
    from argon.google.auth import ACCOUNT_SCOPES, GoogleAuth

    logger.disable("argon")  # the checks below log their own failures
    cfg = _load(config)
    ok, bad = "[green]OK  [/green]", "[red]FAIL[/red]"
    warn = "[yellow]WARN[/yellow]"

    console.print(f"\n{__logo__} argon v{__version__}\n")
    console.print(f"{ok if config_path().exists() else bad} config    {config_path()}")
    console.print(f"{ok} data      {cfg.workspace_path}")

    name, provider = cfg.resolve_provider()
    has_creds = bool(provider.api_key or provider.api_base)
    console.print(
        f"{ok if has_creds else bad} provider  {name} -> {cfg.api_base_for(name) or '(default)'}"
    )
    console.print(f"{ok} model     {cfg.agents.defaults.model}")

    enabled = [
        n for n in ("discord", "whatsapp")
        if (getattr(cfg.channels, n, None) or {}).get("enabled")
    ]
    console.print(f"{ok if enabled else warn} channels  {', '.join(enabled) or 'none enabled'}")

    if cfg.google.enabled:
        auth = GoogleAuth(cfg.workspace_path)
        for account in ACCOUNT_SCOPES:
            state, detail = auth.verify(account)
            mark = ok if state == "ok" else bad
            console.print(f"{mark} google    {account}: {state}")
            if detail:
                console.print(f"           [dim]{detail}[/dim]")
    else:
        console.print(f"{warn} google    disabled in config")

    console.print(
        f"{ok if cfg.lockdown.configured else warn} lockdown  "
        f"{'configured' if cfg.lockdown.configured else 'not configured'}"
    )
    console.print(
        f"{ok if cfg.api.token else warn} api       "
        f"{cfg.api.host}:{cfg.api.port} "
        f"{'(token set)' if cfg.api.token else '(no token — /v1 endpoints refuse requests)'}"
    )

    sessions = sorted(
        (cfg.workspace_path / "sessions").glob("*.jsonl"),
        key=lambda p: p.stat().st_size, reverse=True,
    )
    if sessions:
        big = sessions[0]
        mb = big.stat().st_size / 1_048_576
        mark = warn if mb > 5 else ok
        console.print(f"{mark} sessions  {len(sessions)} files, largest {big.name} {mb:.1f}MB")
    console.print()


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


@app.command()
def migrate(
    source: Path = typer.Option(..., "--from", help="Old workspace (e.g. ~/argon)"),
    old_config: Path = typer.Option(
        Path.home() / ".nanobot" / "config.json", "--old-config",
        help="Pre-rename config file",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would move"),
):
    """Move state from the old in-repo workspace into ~/.argon."""
    from argon.paths import argon_home

    home = argon_home()
    source = source.expanduser()
    moves = [
        (source / d, home / d)
        for d in ("memory", "sessions", "daily", "habits", "schedule", "google", "cron", "skills")
    ]
    # SOUL.md / AGENTS.md / HEARTBEAT.md are deliberately NOT carried over:
    # the old copies contain the contradictory persona and references to tools
    # that no longer exist. sync_workspace_templates seeds fresh ones.

    console.print(f"{__logo__} migrating {source} -> {home}\n")
    for src, dest in moves:
        if not src.exists():
            continue
        if dest.exists():
            console.print(f"  [dim]skip   {dest.name} (already present)[/dim]")
            continue
        console.print(f"  copy   {src} -> {dest}")
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                # symlinks=True: daily/ holds a daily.md symlink to today's log,
                # which is dangling whenever today has no entries yet. Following
                # it aborts the whole copy.
                shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)

    old_config = old_config.expanduser()
    if old_config.exists() and not config_path().exists():
        console.print(f"  config {old_config} -> {config_path()}")
        if not dry_run:
            save_config(load_config(old_config))

    if dry_run:
        console.print("\n[yellow]Dry run — nothing written.[/yellow]")
    else:
        console.print("\n[green]Done.[/green] Verify with: argon doctor")


if __name__ == "__main__":
    app()
