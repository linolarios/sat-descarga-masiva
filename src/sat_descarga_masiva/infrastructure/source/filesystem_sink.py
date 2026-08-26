"""FilesystemSourceArtifactSink — immutable, append-only raw source store.

Layout (per package, in the flat `raw/` dir so many packages coexist):
    source/raw/<sha256>.zip
    source/raw/<sha256>.manifest.json

Immutable by construction: the artifact name is its content hash, so storing the
same package twice is a no-op and we NEVER overwrite an existing artifact — that
would silently replace evidence (AGENT.md §6 / boundary).
"""

from __future__ import annotations

import json
from pathlib import Path

from sat_descarga_masiva.domain.model.manifest import Manifest
from sat_descarga_masiva.domain.model.source import sha256_hex
from sat_descarga_masiva.infrastructure.source.codec import manifest_to_dict


class FilesystemSourceArtifactSink:
    """Implements application.ports.source.SourceArtifactSink over the filesystem."""

    def __init__(self, root: Path) -> None:
        self._raw = root / "raw"

    def store(self, manifest: Manifest, content: bytes) -> None:
        sha = manifest.sha256
        if sha256_hex(content) != sha:
            raise ValueError("manifest.sha256 does not match the package content hash")
        zip_path = self._raw / f"{sha}.zip"
        if zip_path.exists():
            # Duplicate artifact: immutable, never overwrite existing evidence.
            return
        self._raw.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(content)
        manifest_path = self._raw / f"{sha}.manifest.json"
        manifest_path.write_text(json.dumps(manifest_to_dict(manifest), indent=2))
