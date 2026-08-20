"""M1: DownloadCursor value + IncrementalDownloadPolicy."""

from datetime import datetime, timedelta

import pytest

from sat_descarga_masiva.application.policies.download import IncrementalDownloadPolicy
from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc

RFC = Rfc("AAA010101AAA")
START = datetime(2026, 1, 1)
END = datetime(2026, 1, 31)


def _cursor() -> DownloadCursor:
    return DownloadCursor(
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        query_start=START,
        query_end=END,
        last_successful_boundary=datetime(2026, 1, 21),
        last_request_id=RequestId("4e80345d-917f-40bb-a98c-4a73939353c5"),
        last_completed_at=datetime(2026, 1, 21, 10),
    )


def test_cursor_holds_full_shape() -> None:
    cursor = _cursor()
    assert cursor.client_rfc == RFC
    assert cursor.service is ServiceType.CFDI
    assert cursor.direction is Direction.RECIBIDOS
    assert cursor.last_request_id is not None


def test_cursor_optional_fields_default_none() -> None:
    cursor = DownloadCursor(RFC, ServiceType.CFDI, Direction.RECIBIDOS, START, END)
    assert cursor.last_successful_boundary is None
    assert cursor.last_request_id is None
    assert cursor.last_completed_at is None


def test_incremental_policy_holds_fields() -> None:
    policy = IncrementalDownloadPolicy(timedelta(hours=6), 2)
    assert policy.overlap == timedelta(hours=6)
    assert policy.policy_version == 2


def test_incremental_policy_rejects_negative_overlap() -> None:
    with pytest.raises(ValueError):
        IncrementalDownloadPolicy(timedelta(days=-1), 1)
