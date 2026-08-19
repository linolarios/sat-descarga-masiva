"""SAT protocol version — endpoints/actions/signature profiles depend on it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SatProtocolVersion:
    major: int
    minor: int


V1_5 = SatProtocolVersion(1, 5)
