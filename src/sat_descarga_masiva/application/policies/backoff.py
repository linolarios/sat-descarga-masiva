"""Polling backoff as a Strategy (deterministic in tests)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class PollingPolicy:
    initial_delay: timedelta
    max_delay: timedelta
    multiplier: float = 2.0
    timeout: timedelta = timedelta(hours=24)  # configurable; NOT a 72h business constant


class ExponentialBackoff:
    def __init__(self, policy: PollingPolicy) -> None:
        self._policy = policy

    def delay(self, attempt: int) -> timedelta:
        base = self._policy.initial_delay.total_seconds()
        secs = base * (self._policy.multiplier ** max(0, attempt))
        return timedelta(seconds=min(secs, self._policy.max_delay.total_seconds()))


class ImmediateBackoff:
    """No-wait strategy for tests."""

    def delay(self, attempt: int) -> timedelta:
        return timedelta(0)
