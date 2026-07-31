# Argon Skills

Built-in skills. Each is a directory containing a `SKILL.md` with YAML frontmatter
(`name`, `description`, optional `always: true`) followed by markdown instructions.

Skills marked `always: true` are injected into the system prompt on every turn, so they
are paid for on every request — keep them short and keep them true. Every other skill
appears only as a name/description line in the skills summary and is pulled in on demand
via `read_file`.

## Available Skills

| Skill | Always | Description |
|-------|--------|-------------|
| `productivity` | yes | Niranjan's daily system — school day, tasks, focus sessions, check-ins |
| `cron` | no | Scheduling reminders and future tasks |

## Rules for adding a skill

- Never document a tool that isn't registered in `argon/core/loop.py`. `exec`, `write_file`,
  and `edit_file` are deliberately not available; skills that need a shell cannot work here.
- Don't restate a tool's schema — parameters are already sent to the model via function
  calling. Document only the judgment the schema can't convey: when to use it, when not to.
- Persona and voice live in `SOUL.md`, not in a skill. Operating and memory rules live in
  `AGENTS.md`. Don't duplicate either one here.
