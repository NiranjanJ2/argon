"""One transactional home for operational state.

Argon's load-bearing state used to be a handful of files rewritten in place:
``state.json``, ``plan.json``, ``checkins.json``. Every one of them read the
whole document, changed a field, and wrote it back with no lock and no atomic
replace. Two things follow from that, and both happened.

A torn write — the process dying between ``open(w)`` and the last byte — leaves
invalid JSON, and every reader in this codebase caught ``JSONDecodeError`` and
returned defaults. So a crash at the wrong moment did not surface as a fault; it
surfaced as an empty day. Argon behaved as though the plan, the session and
everything said today had never existed, which is indistinguishable from
forgetting.

And a lost update — the Flask API thread and an asyncio turn writing the same
file — silently discards one of them. Whichever wrote second won with a document
read before the first one landed.

SQLite fixes both without a dependency: one file, WAL, real transactions,
cross-thread and cross-process locking. Corruption raises :class:`StoreCorrupt`
rather than being papered over, because "needs recovery" is a true statement a
secretary can make and "you have nothing today" is not.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

from argon.paths import argon_home

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- JSON documents that used to be one file each. `key` is the old file's job.
CREATE TABLE IF NOT EXISTS docs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    updated_at REAL    NOT NULL
);
-- Everything Argon promised to deliver, and whether it actually did.
CREATE TABLE IF NOT EXISTS outbox (
    key        TEXT PRIMARY KEY,   -- idempotency key; one row per promise
    channel    TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    content    TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'message',
    due_at     REAL NOT NULL,
    state      TEXT NOT NULL,      -- pending | sent | failed | missed
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    sent_at    REAL
);
CREATE INDEX IF NOT EXISTS outbox_state_due ON outbox (state, due_at);
"""

#: Files folded into the database on first open. The originals are renamed
#: rather than deleted — a migration that cannot be inspected afterwards is a
#: migration you cannot recover from.
_JSON_MIGRATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("daily_state", ("daily", "state.json")),
    ("day_plan", ("daily", "plan.json")),
    ("checkin_ledger", ("daily", "checkins.json")),
)


class StoreCorrupt(RuntimeError):
    """The operational database cannot be read.

    Raised instead of returning an empty document. Callers are expected to let
    this reach the user as "state needs recovery", never to treat it as "there
    is nothing today".
    """


class _Local(threading.local):
    conn: sqlite3.Connection | None = None
    path: str | None = None


_local = _Local()
_init_lock = threading.Lock()
_initialised: set[str] = set()


def db_path() -> Path:
    """Where the operational database lives. Follows ``ARGON_HOME``."""
    return argon_home() / "argon.db"


def connect() -> sqlite3.Connection:
    """A connection for this thread, with the schema applied.

    sqlite3 connections are not safe to share between threads, and Argon runs
    the Flask API in a daemon thread beside the asyncio loop. One connection per
    thread plus WAL is the whole concurrency story.
    """
    path = str(db_path())
    if _local.conn is not None and _local.path == path:
        return _local.conn

    if _local.conn is not None:  # ARGON_HOME moved (tests)
        try:
            _local.conn.close()
        except Exception:  # noqa: BLE001
            pass
        _local.conn = None

    db_path().parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError as exc:
        raise StoreCorrupt(f"cannot open {path}: {exc}") from exc

    with _init_lock:
        if path not in _initialised:
            try:
                conn.executescript(_SCHEMA)
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                _migrate_json_files(conn)
            except sqlite3.DatabaseError as exc:
                raise StoreCorrupt(f"cannot initialise {path}: {exc}") from exc
            _initialised.add(path)

    _local.conn = conn
    _local.path = path
    return conn


def reset_for_tests() -> None:
    """Drop the cached connection so a new ARGON_HOME is picked up."""
    if _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:  # noqa: BLE001
            pass
    _local.conn = None
    _local.path = None
    with _init_lock:
        _initialised.clear()


@contextmanager
def txn() -> Iterator[sqlite3.Connection]:
    """One write transaction. ``BEGIN IMMEDIATE`` so writers queue, not clash."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.DatabaseError as exc:
        raise StoreCorrupt(str(exc)) from exc
    try:
        yield conn
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.DatabaseError:
            pass
        raise
    try:
        conn.execute("COMMIT")
    except sqlite3.DatabaseError as exc:
        raise StoreCorrupt(str(exc)) from exc


# -- documents -------------------------------------------------------------


def get_doc(key: str, default: Any = None) -> Any:
    """Read one document. Missing is ``default``; unreadable is an error."""
    try:
        row = connect().execute("SELECT value FROM docs WHERE key = ?", (key,)).fetchone()
    except sqlite3.DatabaseError as exc:
        raise StoreCorrupt(f"reading {key}: {exc}") from exc
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError as exc:
        raise StoreCorrupt(f"{key} holds invalid JSON: {exc}") from exc


def put_doc(key: str, value: Any) -> int:
    """Write one document, returning its new version."""
    import time

    payload = json.dumps(value, ensure_ascii=False)
    with txn() as conn:
        row = conn.execute("SELECT version FROM docs WHERE key = ?", (key,)).fetchone()
        version = (row["version"] + 1) if row else 1
        conn.execute(
            "INSERT INTO docs (key, value, version, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "version = excluded.version, updated_at = excluded.updated_at",
            (key, payload, version, time.time()),
        )
    return version


@contextmanager
def edit_doc(key: str, default: Any = None) -> Iterator[Any]:
    """Read-modify-write one document inside a single transaction.

    The lost-update fix. Every ``data = load(); data[x] = y; save(data)`` in
    this codebase was a race; this makes the read and the write one thing.
    """
    import time

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.DatabaseError as exc:
        raise StoreCorrupt(str(exc)) from exc
    try:
        row = conn.execute("SELECT value, version FROM docs WHERE key = ?", (key,)).fetchone()
        if row is None:
            value, version = (default if default is not None else {}), 0
        else:
            try:
                value = json.loads(row["value"])
            except json.JSONDecodeError as exc:
                raise StoreCorrupt(f"{key} holds invalid JSON: {exc}") from exc
            version = row["version"]
        yield value
        conn.execute(
            "INSERT INTO docs (key, value, version, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "version = excluded.version, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), version + 1, time.time()),
        )
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.DatabaseError:
            pass
        raise
    try:
        conn.execute("COMMIT")
    except sqlite3.DatabaseError as exc:
        raise StoreCorrupt(str(exc)) from exc


def doc_version(key: str) -> int:
    """Monotonic version of a document. 0 when it has never been written."""
    try:
        row = connect().execute("SELECT version FROM docs WHERE key = ?", (key,)).fetchone()
    except sqlite3.DatabaseError as exc:
        raise StoreCorrupt(f"reading {key}: {exc}") from exc
    return int(row["version"]) if row else 0


# -- migration -------------------------------------------------------------


def _migrate_json_files(conn: sqlite3.Connection) -> None:
    """Fold the old per-file state in, once, keeping the originals as backups."""
    import time

    home = argon_home()
    for key, parts in _JSON_MIGRATIONS:
        path = home.joinpath(*parts)
        if not path.exists():
            continue
        row = conn.execute("SELECT 1 FROM docs WHERE key = ?", (key,)).fetchone()
        if row is not None:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # A file that was already unreadable is not worth failing startup
            # over — but say so, rather than quietly starting from nothing.
            logger.warning("Could not migrate {} into the store: {}", path, exc)
            continue
        conn.execute(
            "INSERT INTO docs (key, value, version, updated_at) VALUES (?, ?, 1, ?)",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )
        backup = path.with_suffix(".json.pre-sqlite")
        try:
            os.replace(path, backup)
        except OSError as exc:  # noqa: PERF203
            logger.warning("Migrated {} but could not rename it: {}", path, exc)
        logger.info("Migrated {} into the operational store (backup: {})", path.name, backup.name)


# -- health ----------------------------------------------------------------


def health() -> dict[str, Any]:
    """Is the store usable? Never raises — this is what reports the failure."""
    try:
        conn = connect()
        row = conn.execute("PRAGMA quick_check").fetchone()
        status = row[0] if row else "unknown"
        docs = conn.execute("SELECT COUNT(*) AS n FROM docs").fetchone()["n"]
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM outbox WHERE state = 'pending'"
        ).fetchone()["n"]
        missed = conn.execute(
            "SELECT COUNT(*) AS n FROM outbox WHERE state IN ('missed', 'failed')"
        ).fetchone()["n"]
    except (StoreCorrupt, sqlite3.DatabaseError) as exc:
        return {"ok": False, "error": str(exc), "path": str(db_path())}
    return {
        "ok": status == "ok",
        "integrity": status,
        "path": str(db_path()),
        "docs": docs,
        "outbox_pending": pending,
        "outbox_unsent": missed,
    }


__all__ = [
    "StoreCorrupt", "connect", "txn", "get_doc", "put_doc", "edit_doc",
    "doc_version", "db_path", "health", "reset_for_tests", "SCHEMA_VERSION",
]
