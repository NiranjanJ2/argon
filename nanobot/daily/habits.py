"""Habit tracker — learns Niranjan's patterns over time.

Stores per-subject time data, priority patterns, and work timing.
File: workspace/habits/habits.json
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")


def _now() -> datetime:
    return datetime.now(_TZ)


class HabitsTracker:
    """Learns and surfaces Niranjan's productivity patterns."""

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "habits" / "habits.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            return json.loads(self._path.read_text())
        return {
            "subject_times": {},       # subject -> list of actual minutes
            "priority_order": {},      # subject -> avg priority rank (1=highest)
            "work_start_times": [],    # list of HH:MM strings when user said ready to work
            "sleep_times": [],         # list of HH:MM strings when last activity logged
            "session_lengths": [],     # list of total work session minutes per day
            "task_completion_rate": [],# list of (total_tasks, completed_tasks) per day
        }

    def _save(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, indent=2))

    def record_task_completion(self, subject: str | None, actual_minutes: int, priority_rank: int) -> None:
        """Call when a task is completed."""
        if not subject or actual_minutes <= 0:
            return
        data = self._load()
        subj = subject.lower().strip()
        data["subject_times"].setdefault(subj, []).append(actual_minutes)
        data["priority_order"].setdefault(subj, []).append(priority_rank)
        # Keep last 30 data points per subject
        data["subject_times"][subj] = data["subject_times"][subj][-30:]
        data["priority_order"][subj] = data["priority_order"][subj][-30:]
        self._save(data)

    def record_work_start(self) -> None:
        data = self._load()
        data["work_start_times"].append(_now().strftime("%H:%M"))
        data["work_start_times"] = data["work_start_times"][-60:]
        self._save(data)

    def record_day_end(self, total_tasks: int, completed_tasks: int, session_minutes: int) -> None:
        data = self._load()
        data["task_completion_rate"].append((total_tasks, completed_tasks))
        data["task_completion_rate"] = data["task_completion_rate"][-30:]
        data["session_lengths"].append(session_minutes)
        data["session_lengths"] = data["session_lengths"][-30:]
        self._save(data)

    # ------------------------------------------------------------------
    # Insights
    # ------------------------------------------------------------------

    def get_time_estimate(self, subject: str) -> int | None:
        """Return estimated minutes for a subject based on history."""
        data = self._load()
        times = data["subject_times"].get(subject.lower().strip(), [])
        if not times:
            return None
        # Use 75th percentile (slightly pessimistic — better to overestimate)
        sorted_times = sorted(times)
        idx = int(len(sorted_times) * 0.75)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    def get_typical_work_start(self) -> str | None:
        """Return typical HH:MM that user starts working."""
        data = self._load()
        times = data["work_start_times"]
        if not times:
            return None
        # Convert to minutes, take median
        minutes = sorted(int(t[:2]) * 60 + int(t[3:]) for t in times)
        median = minutes[len(minutes) // 2]
        return f"{median // 60:02d}:{median % 60:02d}"

    def get_completion_rate(self) -> float | None:
        data = self._load()
        rates = data["task_completion_rate"]
        if not rates:
            return None
        totals = [c / t for t, c in rates if t > 0]
        if not totals:
            return None
        return sum(totals) / len(totals)

    def get_summary(self) -> dict[str, Any]:
        """Return a summary suitable for injecting into agent context."""
        data = self._load()
        subject_estimates = {}
        for subj, times in data["subject_times"].items():
            if times:
                sorted_t = sorted(times)
                idx = int(len(sorted_t) * 0.75)
                subject_estimates[subj] = sorted_t[min(idx, len(sorted_t) - 1)]

        return {
            "subject_time_estimates_minutes": subject_estimates,
            "typical_work_start": self.get_typical_work_start(),
            "avg_completion_rate": self.get_completion_rate(),
            "typical_session_length_minutes": (
                int(sum(data["session_lengths"]) / len(data["session_lengths"]))
                if data["session_lengths"] else None
            ),
        }
