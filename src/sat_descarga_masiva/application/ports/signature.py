"""CfdiSignatureVerifier -- port for CFDI authenticity validation (M2.5, §6a.3).

The verifier is handed the EXACT source bytes of one downloaded CFDI, so the verdict and
the document source_hash refer to the same artifact: nothing is re-serialized between
extraction and validation, and no SAT round trip is implied.
"""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.signature import SignatureVerdict


class CfdiSignatureVerifier(Protocol):
    def verify(self, xml_bytes: bytes) -> SignatureVerdict: ...
