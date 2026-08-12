# Argon

Niranjan's personal assistant. Runs as a single service on the home server and
talks over Discord, WhatsApp, and an HTTP API for the iOS app and the desktop
readouts.

## Quickstart

```sh
git clone https://github.com/NiranjanJ2/argon && cd argon
bash setup.sh
```

That installs into a venv, asks for an LLM key and a Discord bot token,
generates the API token the widgets and the phone authenticate with, seeds the
workspace, and offers to install the systemd unit. Re-runnable — it only fills
in what is missing.

Google is the one part it cannot do for you. Create a **Desktop** OAuth client
at [console.cloud.google.com](https://console.cloud.google.com) with Calendar,
Tasks, Classroom, Drive and Gmail enabled, save it to
`~/.argon/google/client_secrets.json`, then:

```sh
argon google-auth work      # Tasks + Calendar — the one Argon needs most
argon google-auth school    # Classroom
argon google-auth personal  # optional
argon doctor                # what is actually working
```

`argon doctor` is the answer to "is it set up?" — it checks every integration
and says what to run for each thing that is not.

To configure without the shell script, `argon init` does the config half on its
own, and takes its answers from `ARGON_PROVIDER_KEY`, `ARGON_DISCORD_TOKEN`,
`ARGON_DISCORD_USER` and `ARGON_TIMEZONE` when given `--non-interactive`.

## Layout

```
argon/
  cli.py           argument parsing only
  runtime.py       the object graph: agent, channels, cron, heartbeat, check-ins
  config.py        schema + loading + migration from the pre-rename config
  paths.py         ~/.argon layout
  core/            agent loop, runner, context, memory, sessions, bus, commands
  tools/           what the model can call
  google/          calendar, classroom, drive, gmail, tasks
  productivity/    daily state, log, habits, bell schedule
  services/        cron, heartbeat, adaptive check-ins
  channels/        discord, whatsapp
  api/             HTTP surface (WhatsApp webhook + iOS app)
  prompts/         persona templates seeded into the workspace
  skills/          always-on and on-demand instruction files
```

Code and state are separate. The checkout holds no data; everything mutable
lives in `~/.argon` (override with `ARGON_HOME`).

Two files share a name across that boundary. `AGENTS.md` in the checkout is
guidance for people changing this codebase; `~/.argon/AGENTS.md` is the
operating prompt Argon itself runs on, seeded from `argon/prompts/`.

```
~/.argon/
  config.json
  SOUL.md AGENTS.md HEARTBEAT.md   persona + periodic tasks, edit these
  memory/     MEMORY.md (in every prompt), HISTORY.md (searchable log)
  sessions/   raw conversation history per chat
  daily/      per-day log and session state
  habits/ schedule/ google/ cron/ screentime/ skills/ media/
```

## Running

```sh
argon gateway          # the service
argon chat -m "..."    # one message from the terminal
argon chat             # interactive
argon doctor           # what is actually working
argon google-auth work # re-authenticate a Google account
argon migrate --from ~/argon   # move state out of an old in-repo workspace
```

`argon doctor` exists because Argon fails quietly by design — an expired Google
token just means those tools never register. Run it when something feels absent.

## Development

```sh
pip install -e '.[dev]'
pytest
```
