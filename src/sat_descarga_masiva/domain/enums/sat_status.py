"""SAT status codes are OPEN (a new code must not crash the client)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorClassification(Enum):
    OK = "ok"
    RETRY = "retry"
    PARTITION = "partition"
    QUOTA_WAIT = "quota_wait"
    DUPLICATE = "duplicate"
    NOT_FOUND = "not_found"
    AUTHENTICATION = "authentication"
    FATAL = "fatal"
    UNKNOWN = "unknown"


_CLASSIFY: dict[str, ErrorClassification] = {
    "5000": ErrorClassification.OK,
    "5002": ErrorClassification.FATAL,
    "5003": ErrorClassification.PARTITION,  # tope maximo -> partition the range
    "5004": ErrorClassification.NOT_FOUND,
    "5005": ErrorClassification.DUPLICATE,
    "5011": ErrorClassification.QUOTA_WAIT,  # daily folio limit -> wait/resume
    "300": ErrorClassification.AUTHENTICATION,
    "301": ErrorClassification.FATAL,
    "302": ErrorClassification.FATAL,
    "303": ErrorClassification.FATAL,
    "304": ErrorClassification.AUTHENTICATION,
    "305": ErrorClassification.AUTHENTICATION,
    "404": ErrorClassification.RETRY,  # app "error no controlado" (not HTTP 404)
}


@dataclass(frozen=True)
class SatStatusCode:
    value: str

    @property
    def classification(self) -> ErrorClassification:
        return _CLASSIFY.get(self.value, ErrorClassification.UNKNOWN)
