"""Perspective -- the contributor role in a CFDI (AGENT.md §7a/§8). Domain-only."""

from __future__ import annotations

from enum import StrEnum


class Perspective(StrEnum):
    EMITIDO = "emitido"
    RECIBIDO = "recibido"
    UNDETERMINED = "undetermined"
