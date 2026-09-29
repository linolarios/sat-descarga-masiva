"""Ports for parsing the Constancia de Situación Fiscal (M2.2) and for retaining
it as an immutable source artifact (§7a)."""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.csf import CsfArtifact, CsfData


class CSFParser(Protocol):
    def parse(self, pdf_bytes: bytes) -> CsfData: ...


class CsfArtifactSink(Protocol):
    """Retain the original CSF bytes and return their stored identity (§7a).

    The CSF is fiscal identity and an immutable source artifact: it is stored
    under its content hash, never overwritten, and the returned
    :class:`CsfArtifact` carries the ``csf_hash`` that a persisted
    ``ContributorProfileRecord`` points back to (the §7a audit chain).
    """

    def store(self, content: bytes) -> CsfArtifact: ...
