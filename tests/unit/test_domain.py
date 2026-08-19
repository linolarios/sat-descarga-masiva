from datetime import datetime, timedelta

import pytest

from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.enums.sat_status import ErrorClassification, SatStatusCode
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import DateRange, RequestId, Rfc


def test_rfc_normalizes_and_validates() -> None:
    assert Rfc(" aaa010101aaa ").value == "AAA010101AAA"
    with pytest.raises(ValueError):
        Rfc("not-an-rfc")


def test_request_id_rejects_bad_uuid() -> None:
    with pytest.raises(ValueError):
        RequestId("nope")


def test_date_range_orders() -> None:
    with pytest.raises(ValueError):
        DateRange(datetime(2026, 2, 1), datetime(2026, 1, 1))


def test_request_state_completed_is_3() -> None:
    assert RequestState.COMPLETED == 3


def test_sat_status_classification() -> None:
    assert SatStatusCode("5003").classification is ErrorClassification.PARTITION
    assert SatStatusCode("5011").classification is ErrorClassification.QUOTA_WAIT
    assert SatStatusCode("9999").classification is ErrorClassification.UNKNOWN


def test_access_token_repr_hides_value() -> None:
    now = datetime(2026, 1, 1)
    tok = AccessToken("supersecret", now, now + timedelta(minutes=5))
    assert "supersecret" not in repr(tok)
    assert tok.is_valid(now + timedelta(minutes=1))
