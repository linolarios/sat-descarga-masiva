"""Classify a SAT status code into an action. No control-flow on raw literals elsewhere."""

from __future__ import annotations

from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.enums.sat_status import (
    ErrorClassification,
    SatStatusCode,
    classify_state,
)
from sat_descarga_masiva.domain.model.results import VerificationResult


def classify(code: str | RequestState) -> ErrorClassification:
    """Map an event code or an EstadoSolicitud onto one action category."""
    if isinstance(code, RequestState):
        return classify_state(code)
    return SatStatusCode(code).classification


def classify_verification(result: VerificationResult) -> ErrorClassification:
    """Decide what the verify poll should do with one SAT response.

    A decisive event code wins; a bare 5000 (or an OPEN, unknown code) falls back
    to EstadoSolicitud, so a code this client has never seen cannot crash it.
    """
    classification = classify(result.cod_estatus.value)
    if classification not in (ErrorClassification.OK, ErrorClassification.UNKNOWN):
        return classification
    return classify(result.state)
