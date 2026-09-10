"""FiscalXmlParser -- port for deserializing raw CFDI XML into RawCfd (M2.3)."""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.raw_cfd import RawCfd


class FiscalXmlParser(Protocol):
    def parse(self, xml_bytes: bytes, source_hash: str) -> RawCfd: ...
