"""Safe-extraction limits (§6a #4): a versioned policy, not magic numbers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractionPolicy:
    max_total_bytes: int
    max_entries: int

    def __post_init__(self) -> None:
        if self.max_total_bytes <= 0:
            raise ValueError("max_total_bytes must be positive")
        if self.max_entries <= 0:
            raise ValueError("max_entries must be positive")
