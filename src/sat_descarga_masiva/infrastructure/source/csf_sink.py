"""FilesystemCsfArtifactSink — immutable, hash-named store for the CSF (§7a).

Layout:
    source/csf/<sha256>.pdf

The CSF is fiscal identity (§7a), so it is evidence: the name is its content
hash, storing the same constancia twice is a no-op, and an existing artifact is
never overwritten.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sat_descarga_masiva.domain.model.csf import CsfArtifact
from sat_descarga_masiva.domain.model.source import sha256_hex


class FilesystemCsfArtifactSink:
    """Implements application.ports.csf.CsfArtifactSink over the filesystem."""

    def __init__(self, root: Path, clock: Callable[[], datetime] | None = None) -> None:
        self._dir = root / "csf"
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def store(self, content: bytes) -> CsfArtifact:
        sha = sha256_hex(content)
        path = self._dir / f"{sha}.pdf"
        if not path.exists():
            # Immutable: never overwrite an existing artifact (§6 boundary).
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return CsfArtifact(sha256=sha, stored_path=str(path), recorded_at=self._clock())
