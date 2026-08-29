"""Port for parsing the Constancia de Situación Fiscal (M2.2)."""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.csf import CsfData


class CSFParser(Protocol):
    def parse(self, pdf_bytes: bytes) -> CsfData: ...
