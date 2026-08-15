# Working on Argon

Guidance for anyone — human or agent — changing this codebase.

This file is about *building* Argon. The prompts Argon itself runs on are
`argon/prompts/SOUL.md` (who it is) and `argon/prompts/AGENTS.md` (how it uses
its tools); editing those changes the assistant's behaviour, not the build.

**Those three files under `argon/prompts/` are the only prompts that exist.**
There used to be a second `SOUL.md` at the repo root, left over from an earlier
generation and loaded by nothing, which told the model to address Niranjan by
name — the opposite of what the live prompt says. Reading the repository, you
could not tell which one was real. Copies under `~/.argon` are *installed
output*, seeded and updated from `argon/prompts/` by
`sync_workspace_templates`, which keeps a manifest so a file he has edited
himself is never overwritten. Edit the bundled prompt, not the installed copy.

## What this is

A personal assistant for one person, Niranjan, reachable over Discord and a
small HTTP API. It runs as a systemd service on a home server. It is not a
product and has no other users, so "what would help him" beats "what would
generalise".

```
argon/core/       agent loop, sessions, memory, journal, delivery target
argon/services/   things on timers — check-ins, cron, heartbeat, agenda
argon/tools/      what the model can call
argon/google/     Tasks, Calendar, Classroom, Gmail, Drive, auth
argon/productivity/  daily state, the day plan, habits, bell schedule
argon/api/        Flask surface for the iOS app and desktop widgets
desktop/          SwiftBar plugin + two Übersicht widgets
```

Runtime state lives in `~/.argon`, never in the checkout. Nothing in
`argon/api/server.py` may await — Flask runs in a daemon thread beside the
asyncio loop.

## The one lesson worth carrying

Almost every real bug in this project has been **two parts of the system
believing different things about the same fact**, and almost every durable fix
has been making one place authoritative and deriving the rest.

- Four records of "what is he doing right now" produced a 2921-minute study
  session and a working afternoon classified as free time.
- The delivery address was inferred from a cache, so clearing the cache silently
  stopped two days of messages.
- The check-in ledger stored `"(sent)"` instead of the message, so the model
  could not see it had already asked, and asked nine times.

When you find a discrepancy, ask which component should *own* that fact. Adding
a reconciliation step between two owners is how you get a third bug.

## Guards, not instructions

Prompt text the model can ignore is not a guard. When behaviour must hold:

- Put it in the gate (`pick_occasion`) or in what the prompt is *given*, not in
  prose asking the model to behave.
- Prefer withholding data over instructing restraint. It cannot mention what it
  was never shown.
- Where a fact must be stated (Classroom, the agenda, overdue tasks), fetch it
  and put it in the prompt rather than suggesting a tool call — the model
  reliably skips optional lookups.

`set_focus_mode` is the worked example: a `confirmed` parameter was useless,
because the model set it itself and locked the phone at 1:47 AM. Consent is now
inferred from the shape of the conversation instead.

## Tests

`pytest` from the repo root. ~520 tests, all offline; Google and the LLM are
always stubbed.

- **Name the bug, not the function.** Test names and docstrings here say what
  went wrong and what it cost. A test called `test_a_block_end_is_not_a_reword_of
  _its_own_start` tells the next reader why the branch exists; `test_dedupe`
  does not.
- **Mutation-test anything load-bearing.** Break the line on purpose and confirm
  a test fails. Several guards in this repo were verified this way and two were
  found to be untested that way.
- **Never depend on the wall clock.** Pin it. Two cron tests offset from real
  `now()` and started failing at 22:50 on unchanged code, which reads exactly
  like whatever you just did broke something.
- **Simulate whole days.** Three check-in bugs were only visible by ticking a
  simulated day through the gate; none would have shown up in a unit test.

## Deploying

The server is `agentneon@192.168.68.72`; the repo lives at `~/argon` and the
service runs as `argon.service`.

```sh
git pull                                              # on the server
kill $(systemctl show argon.service -p MainPID --value)   # Restart=always
.venv/bin/python -m argon.cli doctor                  # every integration
```

There is no passwordless sudo, which is why the restart is a `kill`.

## Live data has no undo

`~/.argon` is production. Google Tasks, calendar events and cron jobs written
during a test are real and visible on his phone; memory facts written during a
test are read back as things he said. A test task called "finish physics lab
writeup" was once mistaken for the assistant hallucinating an assignment two
days later. If you must write, clean up in the same session and say so.
