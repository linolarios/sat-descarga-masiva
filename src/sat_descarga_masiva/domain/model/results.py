"""Typed protocol results returned by gateways/use-cases."""

from __future__ import annotations

from dataclasses import dataclass, field

from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.enums.sat_status import SatStatusCode
from sat_descarga_masiva.domain.model.value_objects import PackageId, RequestId


@dataclass(frozen=True)
class SubmitResult:
    request_id: RequestId | None
    cod_estatus: SatStatusCode
    mensaje: str


@dataclass(frozen=True)
class VerificationResult:
    state: RequestState
    cod_estatus: SatStatusCode
    numero_cfdis: int = 0
    mensaje: str = ""
    ids_paquetes: tuple[PackageId, ...] = field(default_factory=tuple)


@dataclass(frozen=True, repr=False)
class Package:
    package_id: PackageId
    content: bytes

    def __repr__(self) -> str:
        return f"Package(package_id={self.package_id!r}, bytes={len(self.content)})"
