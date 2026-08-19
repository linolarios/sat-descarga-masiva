"""Signature profile as configuration (swap algorithms without touching signing code)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignatureProfile:
    signature_algorithm: str
    digest_algorithm: str
    canonicalization_algorithm: str
    transforms: tuple[str, ...]


SAT_V15_SIGNATURE_PROFILE = SignatureProfile(
    signature_algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1",
    digest_algorithm="http://www.w3.org/2000/09/xmldsig#sha1",
    canonicalization_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    transforms=("http://www.w3.org/2000/09/xmldsig#enveloped-signature",),
)
