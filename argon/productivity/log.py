"""Daily log — writes to workspace/daily/YYYY-MM-DD.md (daily.md for today)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")


def _now() -> datetime:
    return datetime.now(_TZ)


def _today_key() -> str:
    now = _now()
    if now.hour < 4:
        from datetime import timedelta
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


class DailyLog:
    """Append-only daily markdown log."""

    def __init__(self, workspace: Path) -> None:
        self._dir = workspace / "daily"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self) -> Path:
        return self._dir / f"{_today_key()}.md"

    def _ensure_header(self) -> None:
        p = self._path()
        if not p.exists():
            p.write_text(f"# Daily Log — {_today_key()}\n\n")

    def append(self, entry: str, *, tag: str = "") -> None:
        self._ensure_header()
        ts = _now().strftime("%H:%M")
        prefix = f"**[{ts}]**" + (f" `{tag}`" if tag else "")
        with self._path().open("a") as f:
            f.write(f"\n{prefix} {entry}\n")

    def log_home_arrival(self) -> None:
        self.append("Got home.", tag="arrival")

    def log_task_started(self, task_title: str) -> None:
        self.append(f"Started: **{task_title}**", tag="task")

    def log_task_done(self, task_title: str, minutes: int | None = None) -> None:
        duration = f" ({minutes} min)" if minutes else ""
        self.append(f"Completed: **{task_title}**{duration}", tag="done")

    def log_mode_change(self, mode: str) -> None:
        self.append(f"Mode → `{mode}`", tag="mode")

    def log_note(self, note: str) -> None:
        self.append(note, tag="note")

    def log_reminder_sent(self) -> None:
        self.append("Reminder sent.", tag="reminder")

    def read(self) -> str:
        from datetime import timedelta
        today_key = _today_key()
        p = self._path()
        today_content = p.read_text() if p.exists() else None

        # If today's log has nothing meaningful, also surface yesterday's for context.
        is_empty_today = not today_content or today_content.strip() == f"# Daily Log — {today_key}"
        yesterday_key = (_now() - timedelta(days=1)).strftime("%Y-%m-%d") if not (_now().hour < 4) else (_now() - timedelta(days=2)).strftime("%Y-%m-%d")
        yesterday_path = self._dir / f"{yesterday_key}.md"

        sections: list[str] = []
        if today_content and not is_empty_today:
            sections.append(today_content)
        else:
            sections.append(f"# Daily Log — {today_key}\n\n(Nothing logged yet today.)\n")

        if yesterday_path.exists():
            sections.append(f"\n---\n## Yesterday ({yesterday_key})\n\n" + yesterday_path.read_text())

        return "\n".join(sections)

    def get_path(self) -> Path:
        return self._path()

    # Symlink today's log as daily.md for easy access
    def refresh_symlink(self) -> None:
        link = self._dir / "daily.md"
        target = self._path()
        try:
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(target.name)
        except Exception:
            pass
