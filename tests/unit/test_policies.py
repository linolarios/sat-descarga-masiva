from datetime import datetime, timedelta

from sat_descarga_masiva.application.policies.backoff import (
    ExponentialBackoff,
    ImmediateBackoff,
    PollingPolicy,
)
from sat_descarga_masiva.application.specifications.query import validate
from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.value_objects import DateRange, Rfc


def test_exponential_backoff_caps_at_max() -> None:
    policy = PollingPolicy(initial_delay=timedelta(seconds=30), max_delay=timedelta(minutes=5))
    bo = ExponentialBackoff(policy)
    assert bo.delay(0) == timedelta(seconds=30)
    assert bo.delay(10) == timedelta(minutes=5)  # capped


def test_immediate_backoff_never_waits() -> None:
    assert ImmediateBackoff().delay(3) == timedelta(0)


def _query(**kw: object) -> DownloadQuery:
    base = dict(
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_type=RequestType.CFDI,
        date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
        rfc_solicitante=Rfc("AAA010101AAA"),
    )
    base.update(kw)
    return DownloadQuery(**base)  # type: ignore[arg-type]


def test_recibidos_xml_requires_vigente() -> None:
    assert "recibidos XML requires document_status=VIGENTE" in validate(_query())
    assert validate(_query(document_status=DocumentStatus.VIGENTE)) == []
