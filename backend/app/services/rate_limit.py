"""Additive in-process rate limiting for expensive endpoints.

Only applied to the routes that spend real upstream quota or trigger bulk work.
Existing routes are untouched: nothing in the original app calls this module.
"""
import threading
import time
from collections import deque

from fastapi import HTTPException

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_MAX_CALLS = 6

_windows = {}
_lock = threading.Lock()


class RateLimit:
    """Sliding-window limiter: at most `max_calls` per `window_seconds` per key."""

    def __init__(self, name, max_calls=DEFAULT_MAX_CALLS, window_seconds=DEFAULT_WINDOW_SECONDS):
        self.name = name
        self.max_calls = max_calls
        self.window_seconds = window_seconds

    def check(self, key):
        now = time.monotonic()
        with _lock:
            bucket = _windows.setdefault((self.name, key), deque())
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.max_calls:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
                raise HTTPException(
                    429,
                    f'Rate limit reached for {self.name}: at most {self.max_calls} requests per '
                    f'{self.window_seconds}s. Retry in {retry_after}s.',
                    headers={'Retry-After': str(retry_after)},
                )
            bucket.append(now)

    def state(self, key):
        now = time.monotonic()
        with _lock:
            bucket = _windows.get((self.name, key), deque())
            remaining = [stamp for stamp in bucket if now - stamp <= self.window_seconds]
        return {
            'name': self.name,
            'key': key,
            'used': len(remaining),
            'limit': self.max_calls,
            'window_seconds': self.window_seconds,
        }


batch_sync_limit = RateLimit('batch sync', max_calls=4, window_seconds=300)
metrics_import_limit = RateLimit('VFSTR metrics import', max_calls=3, window_seconds=900)
directory_refresh_limit = RateLimit('VFSTR directory refresh', max_calls=6, window_seconds=900)
backup_limit = RateLimit('database backup', max_calls=4, window_seconds=600)
export_limit = RateLimit('export', max_calls=30, window_seconds=60)


def all_states(key='anonymous'):
    return [limiter.state(key) for limiter in
            (batch_sync_limit, metrics_import_limit, directory_refresh_limit, backup_limit, export_limit)]


def reset():
    """Test helper: clear every window."""
    with _lock:
        _windows.clear()
