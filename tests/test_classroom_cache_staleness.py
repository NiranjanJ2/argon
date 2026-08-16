"""A stale Classroom cache must not make a reader wait for the re-crawl.

The crawl is one submissions call per assignment — about eight seconds. While a
poll waited for it, every reader stalled on the same two-minute cycle: the
desktop readout reported "read operation timed out" and recovered on its next
refresh, and the phone blocked silently for the same eight seconds.
"""

import time

import pytest

from argon import commitments


@pytest.fixture(autouse=True)
def _clear_cache():
    commitments._classroom_cache.clear()
    commitments._classroom_refreshing.clear()
    yield
    commitments._classroom_cache.clear()
    commitments._classroom_refreshing.clear()


def _seed(tmp_path, age_s: float, label: str = "cached"):
    snapshot = commitments.SourceSnapshot("classroom", ({"title": label},), None, ())
    commitments._classroom_cache[(str(tmp_path), 7)] = (time.monotonic() - age_s, snapshot)
    return snapshot


def test_a_fresh_cache_is_served_without_crawling(tmp_path, monkeypatch):
    _seed(tmp_path, age_s=1)
    monkeypatch.setattr(
        commitments, "_crawl_classroom",
        lambda *a: pytest.fail("crawled while the cache was fresh"))

    assert commitments.classroom_snapshot(tmp_path).items[0]["title"] == "cached"


def test_a_stale_cache_answers_immediately(tmp_path, monkeypatch):
    _seed(tmp_path, age_s=commitments.CLASSROOM_TTL_S + 1, label="stale")
    started = []

    def _slow_crawl(*args):
        started.append(True)
        time.sleep(0.4)  # stands in for the eight-second crawl
        return commitments.SourceSnapshot("classroom", ({"title": "new"},), None, ())

    monkeypatch.setattr(commitments, "_crawl_classroom", _slow_crawl)

    began = time.monotonic()
    result = commitments.classroom_snapshot(tmp_path)
    elapsed = time.monotonic() - began

    # Answered from the stale cache rather than waiting on the crawl.
    assert result.items[0]["title"] == "stale"
    assert elapsed < 0.2
    time.sleep(0.6)
    assert started, "the refresh never ran in the background"


def test_a_burst_of_polls_starts_one_crawl(tmp_path, monkeypatch):
    _seed(tmp_path, age_s=commitments.CLASSROOM_TTL_S + 1)
    crawls = []

    def _slow_crawl(*args):
        crawls.append(True)
        time.sleep(0.3)
        return commitments.SourceSnapshot("classroom", (), None, ())

    monkeypatch.setattr(commitments, "_crawl_classroom", _slow_crawl)

    for _ in range(25):
        commitments.classroom_snapshot(tmp_path)

    time.sleep(0.5)
    assert len(crawls) == 1, f"one stale window started {len(crawls)} crawls"


def test_no_cache_at_all_still_crawls_synchronously(tmp_path, monkeypatch):
    # Nothing to serve, so this one has to wait — there is no honest alternative.
    monkeypatch.setattr(
        commitments, "_crawl_classroom",
        lambda *a: commitments.SourceSnapshot("classroom", ({"title": "first"},), None, ()))

    assert commitments.classroom_snapshot(tmp_path).items[0]["title"] == "first"


def test_an_explicit_refresh_still_waits(tmp_path, monkeypatch):
    # Pull-to-refresh means "I want the truth now", so it may block.
    _seed(tmp_path, age_s=1, label="stale")
    monkeypatch.setattr(
        commitments, "_crawl_classroom",
        lambda *a: commitments.SourceSnapshot("classroom", ({"title": "new"},), None, ()))

    assert commitments.classroom_snapshot(tmp_path, fresh=True).items[0]["title"] == "new"
