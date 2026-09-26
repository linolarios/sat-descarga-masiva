"""Signature verdict: what could be established about one CFDI sello (M2.5, §6a.3).

Domain-only. No XML, no crypto, no satcfdi and no network: a verdict is a fact about how
far authenticity validation got, never a SAT fact and never a posting decision. It carries
no signature bytes, no certificate material and no library object, so it is safe to persist
as review state (§8) and cheap to compare.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SignatureOutcome(StrEnum):
    """The five deterministic outcomes of CFDI authenticity validation.

    VALID            the emisor Sello and the TFD SelloSAT were established as authentic
    INVALID          complete material that the verifier rejected cryptographically
    ABSENT           required sello material is missing or empty in the document
    UNSUPPORTED      the document form is not one the verifier can validate at all
    VALIDATION_ERROR the verifier could not reach a result (external failure)
    """

    VALID = "valid"
    INVALID = "invalid"
    ABSENT = "absent"
    UNSUPPORTED = "unsupported"
    VALIDATION_ERROR = "validation_error"


@dataclass(frozen=True)
class SignatureVerdict:
    """A SignatureOutcome plus a short, deterministic explanation of it.

    `detail` is a fixed, side-effect-free description supplied by the verifier: never a
    library message and never a filesystem path. A verdict is therefore stable across
    runs and machines and can be persisted verbatim as review state.
    """

    outcome: SignatureOutcome
    detail: str = ""

    @property
    def is_valid(self) -> bool:
        """True only for an established VALID outcome; UNSUPPORTED is not valid."""
        return self.outcome is SignatureOutcome.VALID
