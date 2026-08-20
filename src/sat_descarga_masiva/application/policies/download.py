"""Download acquisition policies (§6): versioned late-CFDI overlap, not a magic number."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class IncrementalDownloadPolicy:
    """How much time before the last known good boundary is re-queried."""

    overlap: timedelta
    policy_version: int

    def __post_init__(self) -> None:
        if self.overlap < timedelta(0):
            raise ValueError("overlap must not be negative")
