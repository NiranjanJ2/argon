"""Command-line interface."""

from __future__ import annotations

import asyncio
import os
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
    from argon.api.server import (
        register_agent_handler,
        register_attention_trigger,
        register_cron_service,
        start_api_server,
    )
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

        def consider_now() -> bool:
            """A phone report asks the watch to look now, not at its next tick.

            Deliberately the *same* path as the timer, so mid-work, the
            emergency override, his start time and the one-message-an-hour cap
            all still bind. A second trigger with rules of its own is how the
            twelve-message evening happened the first time.
            """
            asyncio.run_coroutine_threadsafe(rt.heartbeat._tick(), loop)
            return True

        register_agent_handler(agent_turn)
        register_attention_trigger(consider_now)
        register_cron_service(rt.cron)
        try:
            start_api_server(cfg)
        except OSError as exc:
            # Everything else still works without it — Discord, cron, check-ins
            # — so this is a warning, not a reason to refuse to start. But it
            # must not be reported as OK, which is what it did before.
            console.print(f"[red]FAIL[/red] API on {cfg.api.host}:{cfg.api.port} — {exc}")
            console.print("[yellow]     the iOS app and desktop widgets cannot reach Argon[/yellow]")
        else:
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


@app.command("migrate-tasks")
def migrate_tasks(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Copy Google Tasks into Argon's own database. Safe to run twice."""
    from argon.tasks.local_store import migrate_from_google

    cfg = _load(config)
    out = migrate_from_google(cfg.workspace_path)
    console.print(f"[green]OK[/green] read {out['read_from_google']} from Google")
    console.print(f"     imported {out['imported']}, already present {out['already_present']}")
    console.print(f"     local now: {out['local_pending']} pending, {out['local_total']} total")
    console.print("[yellow]Nothing was deleted from Google.[/yellow]")


@app.command("google-auth")
def google_auth(
    account: str = typer.Argument(help="personal | work | school"),
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
def init(
    non_interactive: bool = typer.Option(
        False, "--non-interactive", "-y",
        help="Take every answer from the environment; never prompt.",
    ),
) -> None:
    """Create a working config. Safe to re-run — it only fills gaps.

    Setting Argon up used to mean hand-writing config.json against a schema
    documented nowhere, and the one field nothing tells you about — the API
    token — is the one whose absence makes every /v1 endpoint refuse requests
    while the service still looks healthy.
    """
    import json
    import secrets

    from argon.paths import argon_home
    from argon.utils.helpers import sync_workspace_templates

    ws = argon_home()
    ws.mkdir(parents=True, exist_ok=True)
    path = ws / "config.json"

    try:
        cfg = json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        console.print(f"[red]{path} is not valid JSON — move it aside and re-run.[/red]")
        raise typer.Exit(1)

    fresh = not cfg
    console.print(f"[bold]{'Setting up' if fresh else 'Updating'} Argon[/bold]  {ws}\n")

    def ask(prompt: str, env: str, *, secret: bool = False, default: str = "") -> str:
        """Environment first, then the existing config, then the human."""
        if value := os.environ.get(env, "").strip():
            return value
        if non_interactive:
            return default
        shown = f" [dim]({default})[/dim]" if default else ""
        got = typer.prompt(
            f"{prompt}{shown}", default=default, hide_input=secret,
            show_default=False,
        )
        return (got or default).strip()

    # -- the model ---------------------------------------------------------
    agents = cfg.setdefault("agents", {}).setdefault("defaults", {})
    provider = agents.get("provider") or ask(
        "LLM provider", "ARGON_PROVIDER", default="groq")
    agents["provider"] = provider

    providers = cfg.setdefault("providers", {}).setdefault(provider, {})
    if not providers.get("apiKey"):
        key = ask(f"{provider} API key", "ARGON_PROVIDER_KEY", secret=True)
        if key:
            providers["apiKey"] = key

    if not agents.get("timezone"):
        agents["timezone"] = ask(
            "Timezone", "ARGON_TIMEZONE", default="America/Los_Angeles")

    # -- how he talks to it ------------------------------------------------
    discord = cfg.setdefault("channels", {}).setdefault("discord", {})
    if not discord.get("token"):
        token = ask("Discord bot token [dim](blank to skip)[/dim]",
                    "ARGON_DISCORD_TOKEN", secret=True)
        if token:
            discord["token"] = token
            discord["enabled"] = True
            user = ask("Your Discord user ID", "ARGON_DISCORD_USER")
            if user:
                discord["allowFrom"] = [user]
    discord.setdefault("enabled", bool(discord.get("token")))

    # -- the API the widgets and the phone use -----------------------------
    # Generated, never asked for: a token someone chooses is a token someone
    # can guess, and this one fronts an endpoint that can run agent turns.
    api = cfg.setdefault("api", {})
    minted = False
    if not api.get("token"):
        api["token"] = secrets.token_urlsafe(32)
        minted = True

    path.write_text(json.dumps(cfg, indent=2) + "\n")
    path.chmod(0o600)  # it holds every credential Argon has

    seeded = sync_workspace_templates(ws, silent=True)

    console.print(f"[green]✓[/green] {path}")
    if minted:
        console.print("[green]✓[/green] API token generated for the widgets and iOS app")
    if seeded:
        console.print(f"[green]✓[/green] seeded {', '.join(seeded)}")

    # -- what is still missing ---------------------------------------------
    todo: list[str] = []
    if not providers.get("apiKey"):
        todo.append(f"Add your {provider} API key to {path}")
    if not discord.get("token"):
        todo.append("Add a Discord bot token, or talk to it with `argon chat`")

    secrets_file = ws / "google" / "client_secrets.json"
    if not secrets_file.exists():
        todo.append(
            "Google: create a Desktop OAuth client at console.cloud.google.com\n"
            "     (enable Calendar, Tasks, Classroom, Drive, Gmail), then save it as\n"
            f"     {secrets_file}"
        )
    else:
        from argon.google.auth import GoogleAuth

        for account, state in sorted(GoogleAuth(ws).status().items()):
            if state != "ok":
                todo.append(f"argon google-auth {account}")

    if todo:
        console.print("\n[bold]Still to do[/bold]")
        for i, item in enumerate(todo, 1):
            console.print(f"  {i}. {item}")
    console.print("\nThen: [bold]argon doctor[/bold] to see what is actually working.")


@app.command()
def doctor(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Check every integration and report what is actually working.

    Argon fails quietly by design — a dead Google token just means those tools
    never register. This surfaces that instead of letting it rot for months.
    """
    from argon.google.auth import ACCOUNT_SCOPES, OPTIONAL_ACCOUNTS, GoogleAuth

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

    # The operational store and anything Argon promised but never delivered.
    # Without this, a failed reminder was a row in a table nobody read.
    from argon.core import store as _store

    health = _store.health()
    if not health["ok"]:
        console.print(f"{bad} store     {health.get('error') or health.get('integrity')}")
    else:
        console.print(f"{ok} store     {health['path']} ({health['docs']} documents)")
        unsent = health["outbox_unsent"]
        pending = health["outbox_pending"]
        if unsent:
            console.print(
                f"{bad} delivery  {unsent} message(s) Argon could not deliver "
                f"— run `argon outbox` to see them"
            )
        elif pending:
            console.print(f"{warn} delivery  {pending} still owed")
        else:
            console.print(f"{ok} delivery  nothing outstanding")

    if cfg.google.enabled:
        auth = GoogleAuth(cfg.workspace_path)
        for account in ACCOUNT_SCOPES:
            state, detail = auth.verify(account)
            optional = account in OPTIONAL_ACCOUNTS
            if state == "ok":
                mark = ok
            else:
                mark = warn if optional else bad
            note = " [dim](optional)[/dim]" if optional else ""
            console.print(f"{mark} google    {account}: {state}{note}")
            # An optional account's remedy is noise — it is expected to be dead.
            if detail and not optional:
                console.print(f"           [dim]{detail}[/dim]")
    else:
        console.print(f"{warn} google    disabled in config")

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
# push
# ---------------------------------------------------------------------------


@app.command()
def push(
    text: str = typer.Argument("Argon can reach your phone.", help="Body of the notification"),
    title: str = typer.Option("Argon", "--title", help="Notification title"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Send one notification to the phone, and say exactly what Apple replied.

    Push fails silently by nature — the phone simply does not buzz — so the only
    way to know it works is to ask Apple and print the answer.
    """
    import asyncio

    from argon.ios.push import APNsClient, device_token

    cfg = _load(config)
    client = APNsClient(cfg)
    token, environment = device_token()

    console.print(f"configured : {client.configured}")
    console.print(f"token      : {'registered' if token else '[red]none[/red]'} ({environment})")
    if not client.configured:
        console.print(
            "[red]Push is not configured.[/red] Set ios.apns.enabled, teamId, keyId and "
            "bundleId in config.json, and put the key at ~/.argon/apns/AuthKey_<keyId>.p8"
        )
        raise typer.Exit(1)
    if not token:
        console.print(
            "[yellow]No device token yet.[/yellow] Open the app once so it registers."
        )
        raise typer.Exit(1)

    result = asyncio.run(client.send(title, text, category="ARGON_TASK"))
    if result.ok:
        console.print("[green]Apple accepted the notification.[/green]")
        return
    console.print(f"[red]Rejected:[/red] {result.status} {result.reason}")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# outbox
# ---------------------------------------------------------------------------


@app.command()
def outbox(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show what Argon promised to deliver and could not.

    A reminder that failed used to leave no trace a person could find. It is
    recorded either way now, and this is where it can be read.
    """
    from datetime import datetime

    from argon import clock
    from argon.core import store as _store

    _load(config)
    health = _store.health()
    if not health["ok"]:
        console.print(f"[red]The operational store is unreadable:[/red] {health.get('error')}")
        raise typer.Exit(1)

    rows = _store.connect().execute(
        "SELECT * FROM outbox ORDER BY due_at DESC LIMIT 40"
    ).fetchall()
    if not rows:
        console.print("Nothing has been queued for delivery yet.")
        return

    marks = {"sent": "[green]sent[/green]", "pending": "[yellow]owed[/yellow]",
             "failed": "[red]failed[/red]", "missed": "[red]missed[/red]"}
    for row in rows:
        when = datetime.fromtimestamp(row["due_at"], clock.tz())
        console.print(
            f"{marks.get(row['state'], row['state'])}  {when:%a %d %b %-I:%M %p}  "
            f"{row['channel']}  {row['content'][:60]!r}"
            + (f"  [red]{row['last_error']}[/red]" if row["last_error"] else "")
        )


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


# ---------------------------------------------------------------------------
# unlock
# ---------------------------------------------------------------------------


@app.command()
def unlock(
    minutes: int = typer.Option(
        None, "--minutes", "-m", help="How long to block any new lock."
    ),
    clear: bool = typer.Option(False, "--clear", help="End the override now."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Emergency release: drop any Screen Time block and refuse to impose another.

    Deliberately does not need the gateway, the model or the phone to be
    working — an escape hatch with dependencies is not an escape hatch. It
    edits the desired-state files directly, so it works even if `argon
    gateway` is dead.
    """
    from argon.ios import mode as ios_mode

    cfg = _load(config)
    if clear:
        ios_mode.clear_override()
        console.print("[yellow]Override cleared.[/yellow] Locks may be imposed again.")
        return

    record = ios_mode.engage_override(minutes or cfg.ios.override_minutes, source="cli")
    console.print(f"[green]Unlocked.[/green] No block can be imposed until {record['until']}.")
    console.print("[dim]The phone applies this the next time the app is open.[/dim]")
