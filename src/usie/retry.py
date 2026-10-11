"""Retry policy: bounded exponential backoff, only for transient failures."""

from __future__ import annotations

import random
from dataclasses import dataclass

from usie.models import StatusDecision


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 2.0
    factor: float = 2.0
    max_delay_s: float = 60.0
    jitter: float = 0.2

    def should_retry(self, decision: StatusDecision, attempt: int) -> bool:
        """attempt is 1-based, the attempt that just finished. Blocked/unavailable never retry."""
        return decision.retryable and attempt < self.max_attempts

    def delay_s(self, attempt: int, rng: random.Random | None = None) -> float:
        raw = min(self.max_delay_s, self.base_delay_s * (self.factor ** (attempt - 1)))
        if self.jitter <= 0:
            return raw
        r = (rng or random).uniform(-self.jitter, self.jitter)  # noqa: S311
        return max(0.0, raw * (1 + r))
