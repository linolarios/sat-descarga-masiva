"""Composition settings — the only place concrete config is assembled."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sat_descarga_masiva.application.policies.backoff import PollingPolicy
from sat_descarga_masiva.domain.enums.catalog import ServiceType
from sat_descarga_masiva.infrastructure.sat.endpoints import SatEndpoints, endpoints_for


def _default_polling() -> PollingPolicy:
    return PollingPolicy(initial_delay=timedelta(seconds=30), max_delay=timedelta(minutes=10))


@dataclass(frozen=True)
class Settings:
    service: ServiceType = ServiceType.CFDI
    polling: PollingPolicy = field(default_factory=_default_polling)

    @property
    def endpoints(self) -> SatEndpoints:
        return endpoints_for(self.service)
