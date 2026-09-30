"""Ports for the immutable source-artifact store and safe package extraction (§6, §6a)."""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.manifest import Manifest
from sat_descarga_masiva.domain.model.source import ExtractedXml
from sat_descarga_masiva.domain.model.value_objects import PackageId


class SourceArtifactSink(Protocol):
    """Append-only immutable raw store: writes source/raw/<sha256>.zip + manifest sidecar."""

    def store(self, manifest: Manifest, content: bytes) -> None: ...


class PackageExtractor(Protocol):
    """Safe, bound, re-runnable extraction of a downloaded package (AGENT.md §6a #4)."""

    def extract(self, package_id: PackageId, content: bytes) -> tuple[ExtractedXml, ...]: ...


class ExtractedXmlReader(Protocol):
    """Re-read the EXACT bytes of one extracted XML artifact (M2.8, §6a.2).

    Addressed by the artifact's own identity (``tipo`` + ``uuid``) — never by
    scanning a directory — and the bytes must still hash to the recorded
    ``sha256`` before any consumer sees them, so a file that changed underneath us
    (or a path smuggled out of the extraction root) fails with ``ExtractionError``
    instead of silently feeding a parse.
    """

    def read(self, artifact: ExtractedXml) -> bytes: ...
