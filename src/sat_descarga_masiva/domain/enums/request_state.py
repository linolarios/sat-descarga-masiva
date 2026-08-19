"""SAT EstadoSolicitud as a stable protocol-state enum."""

from __future__ import annotations

from enum import IntEnum


class RequestState(IntEnum):
    ACCEPTED = 1
    PROCESSING = 2
    COMPLETED = 3
    ERROR = 4
    REJECTED = 5
    EXPIRED = 6  # state 6; not a fixed 72h business constant
