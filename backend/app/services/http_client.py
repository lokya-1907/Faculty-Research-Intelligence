"""Shared resilient HTTP helper.

Additive module: nothing imports it unless it chooses to. Provides bounded
retry with exponential backoff and jitter for transient upstream failures, and
a small circuit breaker so a persistently failing provider stops being hammered.
"""
import logging
import random
import threading
import time

import httpx

logger = logging.getLogger('researchpulse.http')

# Status codes worth retrying: upstream throttling and transient server faults.
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})

DEFAULT_ATTEMPTS = 4
DEFAULT_BACKOFF = 0.6
DEFAULT_MAX_BACKOFF = 8.0
BREAKER_THRESHOLD = 5
BREAKER_COOLDOWN = 120.0


class RetryExhausted(RuntimeError):
    """Raised when every attempt failed; carries the final cause."""

    def __init__(self, message, cause=None, attempts=0):
        super().__init__(message)
        self.cause = cause
        self.attempts = attempts


class CircuitOpen(RuntimeError):
    """Raised when a provider is in cooldown after repeated failures."""


class _Breaker:
    def __init__(self, threshold=BREAKER_THRESHOLD, cooldown=BREAKER_COOLDOWN):
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at = 0.0
        self._threshold = threshold
        self._cooldown = cooldown

    def check(self, key):
        with self._lock:
            if self._failures >= self._threshold:
                remaining = self._cooldown - (time.monotonic() - self._opened_at)
                if remaining > 0:
                    raise CircuitOpen(
                        f'Provider {key} is temporarily suspended after repeated failures; '
                        f'retrying in {remaining:.0f}s.'
                    )
                # Cooldown elapsed: half-open, let the next call through.
                self._failures = self._threshold - 1

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._opened_at = 0.0

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = time.monotonic()

    def state(self, key):
        with self._lock:
            open_now = self._failures >= self._threshold
            remaining = 0.0
            if open_now:
                remaining = max(0.0, self._cooldown - (time.monotonic() - self._opened_at))
            return {
                'provider': key,
                'consecutive_failures': self._failures,
                'open': open_now,
                'retry_after_seconds': round(remaining, 1),
            }


_breakers = {}
_breakers_lock = threading.Lock()


def breaker_for(key):
    with _breakers_lock:
        if key not in _breakers:
            _breakers[key] = _Breaker()
        return _breakers[key]


def breaker_states():
    with _breakers_lock:
        keys = list(_breakers)
    return [_breakers[key].state(key) for key in keys]


def _sleep_for(attempt, backoff, max_backoff, retry_after=None):
    if retry_after:
        try:
            return min(float(retry_after), max_backoff)
        except (TypeError, ValueError):
            pass
    delay = min(backoff * (2 ** attempt), max_backoff)
    # Full jitter keeps retries from synchronising across callers.
    return random.uniform(delay * 0.5, delay)


def get_json(url, *, params=None, headers=None, timeout=25.0, provider='upstream',
             attempts=DEFAULT_ATTEMPTS, backoff=DEFAULT_BACKOFF,
             max_backoff=DEFAULT_MAX_BACKOFF, breaker=True, transport=None):
    """GET a URL and return parsed JSON, retrying transient failures.

    `transport` lets tests inject a fake httpx-like client.
    Raises RetryExhausted (wrapping the last error) or CircuitOpen.
    """
    client = transport or httpx
    gate = breaker_for(provider) if breaker else None
    if gate:
        gate.check(provider)

    last_error = None
    for attempt in range(attempts):
        try:
            response = client.get(url, params=params, headers=headers, timeout=timeout)
        except httpx.HTTPError as error:
            last_error = error
            if gate:
                gate.record_failure()
            if attempt == attempts - 1:
                break
            delay = _sleep_for(attempt, backoff, max_backoff)
            logger.warning('provider=%s attempt=%s/%s transport error=%s retrying_in=%.2fs',
                           provider, attempt + 1, attempts, error, delay)
            time.sleep(delay)
            continue

        status = getattr(response, 'status_code', 0)
        if status in RETRY_STATUS and attempt < attempts - 1:
            retry_after = None
            try:
                retry_after = response.headers.get('Retry-After')
            except Exception:
                retry_after = None
            delay = _sleep_for(attempt, backoff, max_backoff, retry_after)
            logger.warning('provider=%s attempt=%s/%s status=%s retrying_in=%.2fs',
                           provider, attempt + 1, attempts, status, delay)
            if gate:
                gate.record_failure()
            time.sleep(delay)
            continue

        if status in RETRY_STATUS:
            last_error = RuntimeError(f'HTTP {status} from {provider} after {attempts} attempts.')
            if gate:
                gate.record_failure()
            break

        raiser = getattr(response, 'raise_for_status', None)
        if callable(raiser):
            try:
                raiser()
            except httpx.HTTPError as error:
                if gate:
                    gate.record_failure()
                raise RetryExhausted(str(error), cause=error, attempts=attempt + 1) from error
            except RuntimeError:
                # Some injected/standalone responses cannot raise_for_status
                # (no bound request). Fall back to the status code.
                if not getattr(response, 'is_success', True):
                    if gate:
                        gate.record_failure()
                    raise RetryExhausted(f'HTTP {status} from {provider}.', cause=response, attempts=attempt + 1)
        elif getattr(response, 'is_success', True) is False:
            # Injected transports may expose only is_success/status_code.
            if gate:
                gate.record_failure()
            raise RetryExhausted(f'HTTP {status} from {provider}.', cause=response, attempts=attempt + 1)

        if gate:
            gate.record_success()
        return response.json()

    message = f'{provider} did not respond successfully after {attempts} attempts: {last_error}'
    raise RetryExhausted(message, cause=last_error, attempts=attempts)
