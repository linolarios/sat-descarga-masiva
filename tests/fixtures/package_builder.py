"""Deterministic fixture *package* builder for tier-2 tests (AGENT.md §12 (2)).

A package is the shape SAT hands us: a ZIP whose members are `<uuid>.xml`. Building one
from the committed CFDI XML fixtures is what lets an offline test cross the
extract -> parse -> project seam without the live SAT.

Byte-stable by construction: ZIP_STORED (no compressor/version drift), a fixed 1980 zip
epoch instead of a clock, and no randomness — so the committed `.zip` fixture can be
regenerated and compared byte for byte.

Run:  python tests/fixtures/generate_package_fixture.py
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from lxml import etree  # type: ignore[import-untyped]  # no stubs installed

FIXTURES = Path(__file__).resolve().parent

#: The zip epoch, deliberately not the build time: identical bytes on every run/machine.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

#: The committed package fixture's file name.
PACKAGE_NAME = "cfdi_package_01.zip"

#: One TFD-bearing CFDI 4.0 (projectable) plus the two artifacts the projection must refuse
#: (no TFD UUID / unsupported 3.3 version) — the chain needs both outcomes to be useful.
#:
#: One member per fixture, and no two fixtures may map to one member name: every
#: TFD-bearing fixture in this directory carries the generator's single fixed UUID, so two
#: of them cannot travel in one package. The name/TFD-mismatch case is therefore tier-1.
DEFAULT_FIXTURES: tuple[str, ...] = (
    "cfdi_tfd_4_0.xml",
    "cfdi_egreso_4_0.xml",
    "cfdi_ingreso_3_3.xml",
)

_TFD_LOCAL_NAME = "TimbreFiscalDigital"
_TFD_XPATH = f".//*[local-name()='{_TFD_LOCAL_NAME}']"


@dataclass(frozen=True)
class PackageMember:
    """One member of the package: the fixture it came from, its name, its bytes."""

    fixture: str
    name: str
    data: bytes

    @property
    def artifact_uuid(self) -> str:
        """The uuid the member is *named for* (the extractor's artifact identity)."""
        return self.name.removesuffix(".xml")


def tfd_uuid(xml: bytes) -> str | None:
    """The UUID the document's own TFD carries, or None when it has no TFD.

    Reads the *Timbre Fiscal Digital* element specifically: a CFDI also carries
    `CfdiRelacionado` UUIDs naming *other* documents, and taking the first `UUID=` attribute
    in the file would silently name this artifact after a different one (§6).
    """
    timbres = etree.fromstring(xml).xpath(_TFD_XPATH)
    if not timbres:
        return None
    return timbres[0].get("UUID")


def artifact_name(fixture: str, xml: bytes) -> str:
    """The `<uuid>.xml` name a real package would use for this CFDI.

    Prefers the UUID the CFDI itself carries (its TFD). A fixture without one gets a
    deterministic synthetic name instead: that name is deliberately *not* corroborated by
    the document — exactly the artifact the projection has to refuse rather than trust.
    """
    tfd = tfd_uuid(xml)
    if tfd is not None:
        return f"{tfd}.xml"
    return f"{uuid5(NAMESPACE_URL, f'sat-descarga-masiva.test/{fixture}')}.xml"


def package_members(fixtures: tuple[str, ...] = DEFAULT_FIXTURES) -> tuple[PackageMember, ...]:
    """The package's members in declared order (a member name is derived, never guessed)."""
    members = tuple(
        PackageMember(fixture=fixture, name=artifact_name(fixture, data), data=data)
        for fixture, data in ((f, (FIXTURES / f).read_bytes()) for f in fixtures)
    )
    names = [member.name for member in members]
    if len(set(names)) != len(names):
        raise ValueError(f"two fixtures would share one member name: {names}")
    return members


def build_package_bytes(fixtures: tuple[str, ...] = DEFAULT_FIXTURES) -> bytes:
    """The package ZIP bytes: identical for a given fixture set, on every run."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for member in package_members(fixtures):
            info = zipfile.ZipInfo(member.name, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, member.data)
    return buffer.getvalue()
