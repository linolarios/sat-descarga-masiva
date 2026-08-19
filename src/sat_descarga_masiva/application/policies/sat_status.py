"""Classify a SAT status code into an action. No control-flow on raw literals elsewhere."""

from __future__ import annotations

from sat_descarga_masiva.domain.enums.sat_status import ErrorClassification, SatStatusCode


def classify(code: str) -> ErrorClassification:
    return SatStatusCode(code).classification
