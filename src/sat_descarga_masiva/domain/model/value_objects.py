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
class PackageId:
    value: str


@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("DateRange.start must be <= end")
