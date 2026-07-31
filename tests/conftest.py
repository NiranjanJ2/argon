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
