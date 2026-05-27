"""
Retry + circuit breaker layer (Fase 16 — anti-fragile hardening).

Two primitives:

- ``with_retry(fn, *args, **kwargs)`` — exponential backoff with jitter,
  honouring HTTP 429 ``Retry-After`` headers. Retries on network errors and
  on configurable status codes (default: 429, 500, 502, 503, 504).

- ``CircuitBreaker`` — per-domain failure tracker. After 5 consecutive
  failures within 5 minutes, the breaker opens and ``allow()`` returns
  False (fast-fail). After 60s it goes half-open: one probe is permitted;
  success closes the breaker, failure re-opens it.

Together they shield downstream hosts from amplification storms (we keep
hammering a broken endpoint) and shield the orchestrator from infinite
hangs on transient errors.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_RETRY_AFTER_CAP_SECONDS: float = 60.0
_JITTER_RATIO: float = 0.2  # ±20%


def _retry_after_seconds(exc: httpx.HTTPStatusError) -> float | None:
    """Parse ``Retry-After`` header (seconds form only; HTTP-date ignored)."""
    try:
        raw = exc.response.headers.get("retry-after")
    except AttributeError:
        return None
    if not raw:
        return None
    raw = raw.strip()
    try:
        secs = float(raw)
    except ValueError:
        return None
    if secs < 0:
        return None
    return min(secs, _RETRY_AFTER_CAP_SECONDS)


def _backoff_delay(attempt: int, base: float) -> float:
    """Exponential backoff: ``base * 2**attempt`` jittered ±20%."""
    raw = base * (2.0**attempt)
    jitter = raw * _JITTER_RATIO
    return max(0.0, raw + random.uniform(-jitter, jitter))


async def with_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    retryable_statuses: set[int] | frozenset[int] = _DEFAULT_RETRYABLE_STATUSES,
    **kwargs: Any,
) -> T:
    """Call ``fn(*args, **kwargs)`` with exponential backoff + jitter.

    Retries on:
    - ``httpx.HTTPStatusError`` whose status is in ``retryable_statuses``,
      honouring ``Retry-After`` for 429 responses (cap 60s).
    - ``httpx.TimeoutException`` / ``httpx.ConnectError`` (network flakiness).

    Raises the last exception after ``max_attempts`` total tries (initial +
    retries). ``max_attempts=1`` means "no retry, propagate immediately".
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    retryable_set = (
        retryable_statuses
        if isinstance(retryable_statuses, (set, frozenset))
        else frozenset(retryable_statuses)
    )

    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            status = getattr(exc.response, "status_code", None)
            if status not in retryable_set or attempt == max_attempts - 1:
                raise
            wait: float
            if status == 429:
                ra = _retry_after_seconds(exc)
                wait = ra if ra is not None else _backoff_delay(attempt, base_delay)
            else:
                wait = _backoff_delay(attempt, base_delay)
            logger.info(
                "with_retry: status %s on attempt %d/%d — sleeping %.2fs",
                status,
                attempt + 1,
                max_attempts,
                wait,
            )
            await asyncio.sleep(wait)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                raise
            wait = _backoff_delay(attempt, base_delay)
            logger.info(
                "with_retry: %s on attempt %d/%d — sleeping %.2fs",
                type(exc).__name__,
                attempt + 1,
                max_attempts,
                wait,
            )
            await asyncio.sleep(wait)

    # Defensive — we should always either return or re-raise inside the loop.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("with_retry: exited loop without result")


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────────────────────

# State machine: CLOSED → (5 fails / 5min) → OPEN → (60s) → HALF_OPEN
#   - CLOSED   : allow() returns True
#   - OPEN     : allow() returns False until cooldown elapses
#   - HALF_OPEN: allow() returns True for the FIRST caller; subsequent
#                callers see False until success/failure is recorded.

_STATE_CLOSED = "closed"
_STATE_OPEN = "open"
_STATE_HALF_OPEN = "half_open"


@dataclass
class _CircuitState:
    state: str = _STATE_CLOSED
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None
    half_open_probe_in_flight: bool = False


class CircuitBreaker:
    """Per-host circuit breaker — opens on consecutive failures."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        failure_window_seconds: float = 300.0,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.failure_threshold = int(failure_threshold)
        self.failure_window = float(failure_window_seconds)
        self.cooldown = float(cooldown_seconds)
        self._states: dict[str, _CircuitState] = {}

    @staticmethod
    def _host_of(url_or_host: str) -> str:
        if "://" in url_or_host:
            try:
                return (urlparse(url_or_host).hostname or "").lower()
            except (ValueError, AttributeError):
                return ""
        return url_or_host.lower()

    def _get(self, host: str) -> _CircuitState:
        s = self._states.get(host)
        if s is None:
            s = _CircuitState()
            self._states[host] = s
        return s

    def allow(self, url_or_host: str) -> bool:
        """Return True if a request to this host is permitted right now."""
        host = self._host_of(url_or_host)
        if not host:
            return True
        s = self._get(host)
        now = time.monotonic()

        if s.state == _STATE_OPEN:
            if s.opened_at is not None and (now - s.opened_at) >= self.cooldown:
                s.state = _STATE_HALF_OPEN
                s.half_open_probe_in_flight = False
            else:
                return False

        if s.state == _STATE_HALF_OPEN:
            if s.half_open_probe_in_flight:
                return False
            s.half_open_probe_in_flight = True
            return True

        return True  # CLOSED

    def record_success(self, url_or_host: str) -> None:
        """Mark a successful request — closes a half-open breaker."""
        host = self._host_of(url_or_host)
        if not host:
            return
        s = self._get(host)
        s.failures.clear()
        s.state = _STATE_CLOSED
        s.opened_at = None
        s.half_open_probe_in_flight = False

    def record_failure(self, url_or_host: str) -> None:
        """Record a failed request; may trip the breaker open."""
        host = self._host_of(url_or_host)
        if not host:
            return
        s = self._get(host)
        now = time.monotonic()
        # Prune failures outside the rolling window.
        cutoff = now - self.failure_window
        s.failures = [t for t in s.failures if t >= cutoff]
        s.failures.append(now)
        s.half_open_probe_in_flight = False

        if s.state == _STATE_HALF_OPEN:
            # Half-open probe failed → re-open.
            s.state = _STATE_OPEN
            s.opened_at = now
            return

        if len(s.failures) >= self.failure_threshold:
            s.state = _STATE_OPEN
            s.opened_at = now
            logger.warning(
                "CircuitBreaker: opened for %s after %d failures in %.0fs",
                host,
                len(s.failures),
                self.failure_window,
            )

    def state(self, url_or_host: str) -> str:
        """Return current state string for ``host``: closed / open / half_open."""
        host = self._host_of(url_or_host)
        if not host:
            return _STATE_CLOSED
        return self._get(host).state


# Module-level singleton — orchestrator uses this directly.
circuit_breaker: CircuitBreaker = CircuitBreaker()


__all__ = [
    "CircuitBreaker",
    "circuit_breaker",
    "with_retry",
]
