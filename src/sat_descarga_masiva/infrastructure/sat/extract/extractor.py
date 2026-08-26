"""SafeZipExtractor — bound, traversal-safe extraction of downloaded packages.

Writes reproducibly derived XMLs to source/extracted/<tipo>/<uuid>.xml with a
per-XML SHA-256 (the future posting_snapshot.source_hash). Implements AGENT.md
§6a #4: bound decompressed size + entry count (zip-bomb), reject absolute / ..
traversal, extract only `.xml` members.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree  # type: ignore[import-untyped]  # no stubs installed

from sat_descarga_masiva.application.policies.extraction import ExtractionPolicy
from sat_descarga_masiva.domain.errors import ExtractionError
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import PackageId

# TipoDeComprobante (I/E/T/P) -> classified subdir name.
_TIPO_MAP = {
    "I": "ingreso",
    "E": "egreso",
    "T": "traslado",
    "P": "pago",
}

_DRIVE = re.compile(r"^[A-Za-z]:")


class SafeZipExtractor:
    """Implements application.ports.source.PackageExtractor over the filesystem."""

    def __init__(self, root: Path, policy: ExtractionPolicy) -> None:
        self._root = root
        self._policy = policy

    def extract(self, package_id: PackageId, content: bytes) -> tuple[ExtractedXml, ...]:
        extracted: list[ExtractedXml] = []
        total = 0
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            infos = zf.infolist()
            if len(infos) > self._policy.max_entries:
                raise ExtractionError(
                    f"package {package_id.value} exceeds max_entries={self._policy.max_entries}"
                )
            for info in infos:
                if info.is_dir():
                    continue
                name = info.filename
                normalized = name.replace("\\", "/")
                if not normalized.lower().endswith(".xml"):
                    continue
                self._reject_unsafe_path(name, normalized)
                if info.file_size > self._policy.max_total_bytes:
                    raise ExtractionError(
                        f"entry {name!r} exceeds max_total_bytes={self._policy.max_total_bytes}"
                    )
                data = zf.read(info)
                total += len(data)
                if total > self._policy.max_total_bytes:
                    raise ExtractionError(
                        f"package {package_id.value} decompressed size exceeds limit"
                    )
                sha = sha256_hex(data)
                tipo = self._tipo_of(data)
                uuid = PurePosixPath(normalized).stem
                dest = self._root / "extracted" / tipo / f"{uuid}.xml"
                if dest.exists() and sha256_hex(dest.read_bytes()) != sha:
                    raise ExtractionError(
                        f"destination {dest} conflicts with existing different content"
                    )
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                extracted.append(ExtractedXml(uuid=uuid, tipo=tipo, sha256=sha))
        return tuple(extracted)

    @staticmethod
    def _reject_unsafe_path(original: str, normalized: str) -> None:
        if not normalized:
            raise ExtractionError("zip member has an empty path")
        if normalized.startswith("/") or _DRIVE.match(normalized):
            raise ExtractionError(f"zip member {original!r} uses an absolute path")
        if ".." in PurePosixPath(normalized).parts:
            raise ExtractionError(f"zip member {original!r} attempts path traversal")

    @staticmethod
    def _tipo_of(data: bytes) -> str:
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError:
            return "otro"
        tipo = root.get("TipoDeComprobante")
        if tipo is None:
            return "otro"
        return _TIPO_MAP.get(tipo, "otro")
