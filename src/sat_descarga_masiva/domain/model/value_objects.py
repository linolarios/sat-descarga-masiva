"""Domain value objects. No I/O, no protocol, no cryptography."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

_RFC = re.compile(r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$")


@dataclass(frozen=True)
class Rfc:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        if not _RFC.match(normalized):
            raise ValueError(f"invalid RFC: {self.value!r}")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True)
class RequestId:
    value: str

    def __post_init__(self) -> None:
        UUID(self.value)  # raises ValueError on a malformed id


@dataclass(frozen=True)
class Uuid:
    """A CFDI UUID (TFD) -- fiscal identity, canonicalized for reliable joins (§6).

    The raw layer preserves the source representation verbatim; this value object
    is the normalized fiscal-model identity used for UUID joins and dedup.
    """

    value: str

    def __post_init__(self) -> None:
        canonical = str(UUID(self.value)).upper()  # raises ValueError if malformed
        object.__setattr__(self, "value", canonical)


@dataclass(frozen=True)
class PackageId:
    value: str


@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("DateRange.start must be <= end")


_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class Period:
    """An ``YYYY-MM`` fiscal period (§7/§9).

    §7 and §9 both speak in monthly periods and AGENT.md warns not to invent period
    semantics, so this value object owns exactly one rule: the period is a calendar
    month written ``YYYY-MM``. It is deliberately *not* a date range and carries no
    notion of "which documents belong to it" — selecting artifacts is not the
    orchestration's decision (§7's process flow operates on the set it is given).
    """

    value: str

    def __post_init__(self) -> None:
        if not _PERIOD.match(self.value):
            raise ValueError(f"invalid period: {self.value!r} (expected YYYY-MM)")
