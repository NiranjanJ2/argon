# Changelog

Argon is one person's assistant, so this reads as what changed for Niranjan
rather than as a release log. Dates are when the work landed.

The recurring theme, worth stating once: nearly every bug here was two parts of
the system holding different beliefs about the same fact. Fixes that made a
single place authoritative held. Fixes that told the model to behave did not.

## Unreleased

### A clone that starts — 2026-08-11

`setup.sh` could not have worked on any machine: it wrote `~/.nanobot/config.json`,
installed a `nanobot` binary, configured a provider no longer in use, demanded a
WhatsApp number for a disabled channel, and never created the API token — the one
field whose absence makes every `/v1` route refuse requests while the service
still reports itself healthy.

- **`argon init`** owns the config now, because it is Python and can be tested.
  Generates the API token rather than asking for one, chmods 600, seeds the
  workspace, lists what is still missing. Re-running fills gaps only: it never
  rotates the token (that would lock out the widgets and the phone) and never
  clobbers settings.
- **`setup.sh` is thin** — prerequisites, venv, `argon init`, an optional systemd
  unit templated from the running user rather than one machine's hardcoded paths.
- **The gateway used to print `OK API on 0.0.0.0:3995` and then fail to bind it.**
  Werkzeug exits from inside the serving thread, so the conflict appeared a line
  *after* the success message. The port is probed before anything is claimed, and
  the failure stays non-fatal — Discord, cron and check-ins do not need that socket.
- Smaller: `pip install -e .[discord]` asked for an extra that does not exist,
  the Python floor disagreed with `pyproject`, and `HeartbeatConfig.model` was
  declared twice so its documented default was dead.

Found by cloning the repo to an empty directory and running it end to end, which
is also now a test. The suite is time-independent as of this change — five tests
read the wall clock and failed after 23:00; verified green under five timezones.

### The day's plan drives everything — 2026-08-11

Argon used to reach out on a timer: fixed windows plus an `idle` nudge every two
hours whenever any task was open. From the receiving end that is indistinguishable
from random, and the reasonable response is to stop reading the messages.

- **The 4 PM brief.** School runs the morning; the evening is the part of the day
  Niranjan actually runs, and that message is the product. It states Google
  Classroom assignments and past-due work outright rather than suggesting a tool
  call the model kept skipping, and asks for two or three things and one question.
- **`set_day_plan` records his answer**, and those blocks *are* the check-in
  schedule — a word as each starts, a word as each ends, an offer during any long
  stretch he left open. Retired `morning`, `after_school`, `idle` and `ambient`:
  every one was a generic time rather than a moment he chose.
- **Nothing unprompted before 4 PM** (`checkins.unpromptedFromHour`). Bounds
  discretionary messages only; anything he scheduled himself still arrives at its
  own hour, and the plan question stops at 8 PM because by then it is not a
  question about today.
- **Commitments seed the plan.** A day with reminders he made already has a shape;
  asking him to describe it is the failure this design exists to prevent. An
  ambient calendar fixture is not the same as an answer, and does not count as one.

Three bugs came out of walking a simulated day through the gate at ten-minute
ticks, none of which unit tests would have found: free-stretch dedupe keyed on
when the gap was *noticed* (one afternoon, three messages); the daily cap
exhausted by discretionary offers, silently swallowing a 7 PM block he had asked
for; and the 25-minute floor closing a block's grace window before it lifted, so
back-to-back blocks lost the second entirely.

### Memory that survives being written to — 2026-08-11

- **Duplicates.** Facts were deduped on exact lowered text, and the model restates
  what it knows every night, never identically — so `MEMORY.md` held three facts
  twice, a trailing full stop apart. Matching is on content now, by containment
  rather than symmetric overlap, with an exact-match floor for short facts.
- **Skipped days.** `pending_day` took the *newest* unconsolidated day and marked
  it done, stepping over older ones permanently. Oldest first.
- **Standing facts.** A recurring shape of his life — school hours, when he is
  free — never expires and is never dropped to make room for a one-off deadline.
- **`AGENTS.md` said there was no `remember` tool** and to just acknowledge such
  requests. Both `remember` and `recall` had been registered the whole time; the
  model did as it was told, which is why nothing he said about his schedule ever
  survived.

### Delivery, dates and duplicates — 2026-08-11

- **Nine "what's your plan?" messages a day, three days running.** `on_check_in`
  recorded the placeholder `"(sent)"` as what was said, so the next check-in was
  shown `(sent)` as its own history and could not tell it had already asked. It
  also defeated the reword filter, which suppressed the *ledger entry* while the
  message had gone out, so the daily cap never engaged.
- **Every `block_end` was silenced.** The reword filter ran on occasions the
  ledger already dedupes by id, and "How did X go?" necessarily shares its subject
  with "X starts now".
- **Invented weekdays.** Asked for his week it answered "08/12 Mon, 08/14 Sat,
  08/16 Mon" — Wednesday, Friday and Sunday. The dates were right every time;
  nothing named a weekday, so it did the arithmetic itself. Calendar events,
  assignments and tasks carry a computed label now.
- **Cancelling a reminder** removes its mirrored calendar event. `cron add` wrote
  one and `cron remove` did not, so a cancelled lock-in reappeared in his week.

### One record for "what is he doing right now" — 2026-08-05

That question had four independent answers — `mode`, `current_task`,
`work_session_start`, and a `started_at` stamp in Google Tasks metadata — with no
transition keeping them together.

- `start_task` set the task and never touched `mode`, so a working afternoon was
  classified as free time and interrupted.
- `complete_task` cleared the task and left `mode` on "working" with no start, so
  the gate measured a zero-minute session and fell silent for the rest of the day.
- `started_at` lived in the durable store, which has no day boundary: a task begun
  at 1:42 AM still read as running two evenings later, and completing it recorded
  **2921 minutes** of study into the habit averages.

`DailyState` now owns a single `session` and everything else is projected from it,
so the tools, the HTTP API and the readouts cannot disagree.

### Check-ins reached nobody for two days — 2026-08-05

`pick_target()` found the delivery address by scanning session files, so archiving
one stale file deleted the only record of the Discord DM. Every check-in was
generated, logged as "spoke", recorded in the ledger, and dropped. The address is
written down on its own now, and a message that cannot be delivered warns.

### Desktop readouts — 2026-08-04 → 08-11

- SwiftBar menu, an Übersicht dashboard, and a **Now** panel to start work from —
  all rendering the same view model, so they cannot disagree.
- Interactive: start, complete, put down, reschedule, reprioritise, tick off a plan
  block. Writes go through Argon's own tool classes, so a task completed from the
  menu bar gets the daily-log and habit side effects.
- They reach Argon over the Cloudflare tunnel rather than an SSH port-forward that
  needed its Access token renewed. Cloudflare 403s `Python-urllib` on sight, which
  presented as an indistinguishable-from-bad-token error.

### Earlier — 2026-02 → 08

- Forked from nanobot; renamed and flattened into `core`/`tools`/`google`/
  `productivity`/`services`. Data root moved to `~/.argon`.
- Day-scoped memory replaced a rewrite-in-full prompt that had decayed
  `MEMORY.md` to two lines while `HISTORY.md` reached 8 MB.
- iOS Screen Time integration with desired-state reconciliation, and three
  independent emergency overrides.
- The email→SMS→Shortcut lockdown path was removed entirely: it took no duration,
  no reason and no confirmation, and the model reached for it over `set_focus_mode`
  — told "today I want to lock in", it locked the phone at 1:37 AM.
- Interactive chat on Groq with NIM as an automatic standby.
