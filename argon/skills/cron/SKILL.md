---
name: cron
description: Schedule reminders and future tasks with the cron tool.
---

# Cron

`cron` schedules future messages. It only exists while the gateway is running — if it isn't in your tool list, say so rather than promising a reminder.

`message` is an instruction executed at fire time, and its result is delivered to him. For a plain reminder, write the instruction so the result *is* the reminder ("Tell Niranjan to leave for the lab").

## Timing

| He says | Use |
|---|---|
| every 20 minutes | `every_seconds=1200` |
| every day at 8am | `cron_expr="0 8 * * *"` |
| weekdays at 5pm | `cron_expr="0 17 * * 1-5"` |
| at a specific time | `at="<ISO datetime>"` — compute it from the current time; the job deletes itself after firing |

Times default to his configured timezone (Pacific). Only pass `tz` for a different IANA zone.

## Managing

`cron(action="list")` to see jobs, `cron(action="remove", job_id=...)` to clear one. You cannot add a job from inside a running cron job.
