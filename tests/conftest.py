"""Global safety net: no test may ever touch the real ``~/.argon``.

``argon.paths.argon_home()`` reads ``ARGON_HOME`` and every data path derives
from it, but some call sites create directories as a side effect of a read-only
check (``argon/tools/fs.py:25`` calls ``get_media_dir()``, which mkdirs, on every
path permission test). Pinning the env var for the whole session means a test
that forgets to isolate itself still cannot write to real state.

Tests that need to inspect what was written set ``ARGON_HOME`` to their own
``tmp_path`` with ``monkeypatch.setenv``; that simply overrides this.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_argon_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path_factory.mktemp("argon-home")))


@pytest.fixture(autouse=True)
def _reset_process_caches():
    """Drop the process-wide caches between tests.

    Both are keyed by workspace and so *usually* isolate themselves, which is
    worse than not isolating at all: a Classroom read cached under one test's
    path made a later test pass or fail depending on the order it ran in. The
    operational store also holds a per-thread connection that has to be dropped
    when ARGON_HOME moves.
    """
    from argon import commitments
    from argon.core import store

    commitments.invalidate()
    store.reset_for_tests()
    yield
    commitments.invalidate()
    store.reset_for_tests()
