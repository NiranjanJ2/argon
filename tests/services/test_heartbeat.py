"""HEARTBEAT.md emptiness detection.

A template HEARTBEAT.md is nothing but headings and HTML comments. Returning it
made every 30-minute tick spend an LLM call to conclude "nothing to do", forever.
``_read_heartbeat_file`` must treat scaffolding as empty.
"""

from __future__ import annotations

from pathlib import Path

from argon.services.heartbeat import HeartbeatService

TEMPLATE = """\
# HEARTBEAT

<!--
Tasks Argon should check on every heartbeat. One per line.
Delete this comment once you add real tasks.
-->

## Active

<!-- e.g. - remind me to stretch at 3pm -->

## Done
"""


def _service(workspace: Path) -> HeartbeatService:
    # provider/model are irrelevant: no LLM call happens while reading the file.
    return HeartbeatService(workspace=workspace, provider=None, model="unused")


def _write(workspace: Path, text: str) -> None:
    (workspace / "HEARTBEAT.md").write_text(text, encoding="utf-8")


def test_missing_file_is_none(tmp_path):
    assert _service(tmp_path)._read_heartbeat_file() is None


def test_empty_file_is_none(tmp_path):
    _write(tmp_path, "")
    assert _service(tmp_path)._read_heartbeat_file() is None


def test_whitespace_only_file_is_none(tmp_path):
    _write(tmp_path, "\n\n   \n\t\n")
    assert _service(tmp_path)._read_heartbeat_file() is None


def test_template_of_headings_and_comments_is_none(tmp_path):
    _write(tmp_path, TEMPLATE)
    assert _service(tmp_path)._read_heartbeat_file() is None


def test_headings_only_is_none(tmp_path):
    _write(tmp_path, "# HEARTBEAT\n\n## Active\n\n## Done\n")
    assert _service(tmp_path)._read_heartbeat_file() is None


def test_multiline_comment_alone_is_none(tmp_path):
    _write(tmp_path, "<!--\n- this looks like a task\n- but it is commented out\n-->\n")
    assert _service(tmp_path)._read_heartbeat_file() is None


def test_a_real_task_line_returns_the_whole_file(tmp_path):
    content = TEMPLATE.replace("## Active\n", "## Active\n\n- Ping Niranjan about the essay\n")
    _write(tmp_path, content)

    result = _service(tmp_path)._read_heartbeat_file()

    # The LLM gets the file verbatim — headings included, not just the task line.
    assert result == content


def test_a_bare_task_line_with_no_headings_returns_content(tmp_path):
    _write(tmp_path, "Check whether the Classroom sync ran.\n")
    assert _service(tmp_path)._read_heartbeat_file() == "Check whether the Classroom sync ran.\n"


def test_indented_heading_still_counts_as_scaffolding(tmp_path):
    _write(tmp_path, "  # HEARTBEAT\n    ## Active\n")
    assert _service(tmp_path)._read_heartbeat_file() is None
