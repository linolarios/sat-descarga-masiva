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
