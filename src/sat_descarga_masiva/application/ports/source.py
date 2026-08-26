"""Ports for the immutable source-artifact store (§6)."""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.manifest import Manifest


class SourceArtifactSink(Protocol):
    """Append-only immutable raw store: writes source/raw/<sha256>.zip + manifest sidecar."""

    def store(self, manifest: Manifest, content: bytes) -> None: ...
