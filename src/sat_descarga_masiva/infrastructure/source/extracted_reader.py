"""FilesystemExtractedXmlReader — re-reads one extracted XML by identity (M2.8).

The address is ``<root>/extracted/<tipo>/<uuid>.xml``: the same layout
SafeZipExtractor writes, so a re-read returns the bytes that extraction derived,
not a fresh parse of the ZIP. Two independent guards keep that honest:

- **Path safety.** ``tipo`` and ``uuid`` come from an ``ExtractedXml`` record, so
  both are re-validated here (absolute path, drive letter, ``..`` traversal, or a
  smuggled separator) and the joined path is finally contained in
  ``<root>/extracted`` — a name can never walk out of the extraction root.
- **Store integrity (§6a.2).** The bytes are hashed and compared to the recorded
  ``sha256``; a mismatch is an ``ExtractionError``, so tampering surfaces here
  instead of being parsed as if it were the artifact we recorded. This says
  nothing about SAT authenticity (§6a.3) — the signature verifier owns that.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from sat_descarga_masiva.domain.errors import ExtractionError
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex

_DRIVE = re.compile(r"^[A-Za-z]:")


class FilesystemExtractedXmlReader:
    """Implements application.ports.source.ExtractedXmlReader over the filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._extracted = root / "extracted"

    def read(self, artifact: ExtractedXml) -> bytes:
        path = self._path(artifact)
        if not path.is_file():
            raise ExtractionError(f"extracted artifact {artifact.uuid} is missing at {path}")
        data = path.read_bytes()
        actual = sha256_hex(data)
        if actual != artifact.sha256:
            raise ExtractionError(
                f"extracted artifact {artifact.uuid} hash mismatch: "
                f"recorded {artifact.sha256}, found {actual}"
            )
        return data

    def _path(self, artifact: ExtractedXml) -> Path:
        """The recorded address, proven to stay inside the extraction root."""
        tipo = _safe_component(artifact.tipo, "tipo")
        uuid = _safe_component(artifact.uuid, "uuid")
        path = self._extracted / tipo / f"{uuid}.xml"
        contained = self._extracted.resolve()
        if not path.resolve().is_relative_to(contained):
            raise ExtractionError(f"extracted artifact {artifact.uuid} escapes {contained}")
        return path


def _safe_component(value: str, what: str) -> str:
    """One path component, refused if it could address anything else (mirrors the extractor)."""
    normalized = value.replace("\\", "/")
    if not normalized:
        raise ExtractionError(f"extracted artifact {what} is empty")
    if normalized.startswith("/") or _DRIVE.match(normalized):
        raise ExtractionError(f"extracted artifact {what} {value!r} uses an absolute path")
    if ".." in PurePosixPath(normalized).parts:
        raise ExtractionError(f"extracted artifact {what} {value!r} attempts path traversal")
    if "/" in normalized or value.startswith("~"):
        raise ExtractionError(f"extracted artifact {what} {value!r} is not a single path component")
    return value
