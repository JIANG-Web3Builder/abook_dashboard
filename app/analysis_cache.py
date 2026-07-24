from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
import time
from typing import Any
from uuid import uuid4


@dataclass
class AnalysisSession:
    signature: str
    payload: dict[str, Any]
    daily_rows: list[dict[str, Any]] | None
    overview_daily_rows: list[dict[str, Any]] | None
    created_at: float


class AnalysisSessionCache:
    def __init__(self, *, max_entries: int = 8, ttl_seconds: float = 900.0):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, AnalysisSession] = OrderedDict()
        self._lock = RLock()

    def put(self, session: AnalysisSession) -> str:
        token = uuid4().hex
        with self._lock:
            self._entries[token] = session
            self._entries.move_to_end(token)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return token

    def get(self, token: str | None, signature: str) -> AnalysisSession | None:
        if not token:
            return None
        with self._lock:
            session = self._entries.get(token)
            if session is None:
                return None
            if time.monotonic() - session.created_at > self.ttl_seconds:
                del self._entries[token]
                return None
            if session.signature != signature:
                return None
            self._entries.move_to_end(token)
            return session

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class ResultCache:
    """Small in-process TTL/LRU cache for computed analytics payloads."""

    def __init__(self, *, max_entries: int = 8, ttl_seconds: float = 900.0):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = RLock()

    def put(self, signature: str, value: Any) -> None:
        with self._lock:
            self._entries[signature] = (time.monotonic(), value)
            self._entries.move_to_end(signature)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def get(self, signature: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(signature)
            if entry is None:
                return None
            created_at, value = entry
            if time.monotonic() - created_at > self.ttl_seconds:
                del self._entries[signature]
                return None
            self._entries.move_to_end(signature)
            return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def request_signature(request: Any) -> str:
    payload = request.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


analysis_session_cache = AnalysisSessionCache()
direction_analytics_cache = ResultCache()
newcomer_analytics_cache = ResultCache()
