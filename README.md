# Argon

Niranjan's personal assistant. Runs as a single service on the home server and
talks over Discord, WhatsApp, and an HTTP API for the iOS app.

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
