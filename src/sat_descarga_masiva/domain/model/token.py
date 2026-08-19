"""Access token — repr-safe (never leak the token value)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, repr=False)
class AccessToken:
    value: str
    created_at: datetime
    expires_at: datetime

    def is_valid(self, now: datetime) -> bool:
        return self.created_at <= now < self.expires_at

    def __repr__(self) -> str:
        return "AccessToken(***)"
