"""Immutable SAT endpoints (v1.5). Verify against the phpcfdi reference if signatures fail."""

from __future__ import annotations

from dataclasses import dataclass

from sat_descarga_masiva.domain.enums.catalog import ServiceType


@dataclass(frozen=True)
class SatEndpoints:
    authentication: str
    request: str
    verification: str
    download: str


CFDI = SatEndpoints(
    authentication="https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/Autenticacion/Autenticacion.svc",
    request="https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/SolicitaDescargaService.svc",
    verification="https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/VerificaSolicitudDescargaService.svc",
    download="https://cfdidescargamasiva.clouda.sat.gob.mx/DescargaMasivaService.svc",
)

RETENCIONES = SatEndpoints(
    authentication="https://retendescargamasivasolicitud.clouda.sat.gob.mx/Autenticacion/Autenticacion.svc",
    request="https://retendescargamasivasolicitud.clouda.sat.gob.mx/SolicitaDescargaService.svc",
    verification="https://retendescargamasivasolicitud.clouda.sat.gob.mx/VerificaSolicitudDescargaService.svc",
    download="https://retendescargamasiva.clouda.sat.gob.mx/DescargaMasivaService.svc",
)


def endpoints_for(service: ServiceType) -> SatEndpoints:
    return CFDI if service is ServiceType.CFDI else RETENCIONES
