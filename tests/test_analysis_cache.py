import time

from app.analysis_cache import AnalysisSession, AnalysisSessionCache


def _session(signature: str) -> AnalysisSession:
    return AnalysisSession(
        signature=signature,
        payload={},
        daily_rows=[],
        overview_daily_rows=None,
        created_at=time.monotonic(),
    )


def test_analysis_session_cache_rejects_mismatched_or_expired_entries():
    cache = AnalysisSessionCache(max_entries=2, ttl_seconds=0.01)
    token = cache.put(_session("request-a"))

    assert cache.get(token, "request-b") is None
    time.sleep(0.02)
    assert cache.get(token, "request-a") is None


def test_analysis_session_cache_evicts_oldest_entry_when_full():
    cache = AnalysisSessionCache(max_entries=1, ttl_seconds=10)
    first = cache.put(_session("first"))
    second = cache.put(_session("second"))

    assert cache.get(first, "first") is None
    assert cache.get(second, "second") is not None
