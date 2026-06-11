"""Per-source circuit breaker for the data-router fallback chain.

Why: when a provider rate-limits or is down, hammering it for every
symbol burns latency and quota that better sources need. The breaker
trips after N failures inside a sliding window and opens a cooldown
during which the router skips the source automatically.

State is in-process - survives a single backend run, resets on restart
(by design - restart is a natural reprobe).

API:
  trip = CircuitBreaker()
  if trip.is_open(name): skip
  trip.record(name, ok=True/False)
"""

from __future__ import annotations

import threading
import time
from collections import deque


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 6,
        window_s: float = 60.0,
        cooldown_s: float = 300.0,
    ):
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self._failures: dict[str, deque[float]] = {}
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_open(self, source: str) -> bool:
        with self._lock:
            until = self._open_until.get(source, 0.0)
            if until and time.time() < until:
                return True
            if until and time.time() >= until:
                # cooldown expired - reset and let next call probe
                self._open_until.pop(source, None)
                self._failures.pop(source, None)
            return False

    def record(self, source: str, ok: bool) -> None:
        with self._lock:
            if ok:
                # success clears any in-flight failure stream
                self._failures.pop(source, None)
                self._open_until.pop(source, None)
                return
            now = time.time()
            dq = self._failures.setdefault(source, deque())
            dq.append(now)
            cutoff = now - self.window_s
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.failure_threshold:
                self._open_until[source] = now + self.cooldown_s

    def state(self) -> dict[str, dict]:
        """Snapshot for diagnostics / health endpoints."""
        with self._lock:
            now = time.time()
            return {
                src: {
                    "open": self._open_until.get(src, 0.0) > now,
                    "open_until": self._open_until.get(src, 0.0),
                    "failures_in_window": len(self._failures.get(src, ())),
                }
                for src in set(self._failures) | set(self._open_until)
            }

    def reset(self, source: str | None = None) -> None:
        with self._lock:
            if source is None:
                self._failures.clear()
                self._open_until.clear()
            else:
                self._failures.pop(source, None)
                self._open_until.pop(source, None)


_default = CircuitBreaker()


def default_breaker() -> CircuitBreaker:
    return _default
