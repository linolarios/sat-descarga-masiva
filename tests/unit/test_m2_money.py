"""M2.3: MoneyPolicy — scale 2 (MXN/USD/CAD), ROUND_HALF_UP, unsupported currency."""

from decimal import Decimal

import pytest

from sat_descarga_masiva.domain.policy.money import (
    MoneyPolicy,
    NormalizedAmount,
    UnsupportedCurrency,
)

POLICY = MoneyPolicy()


def test_supported_currencies_use_scale_2() -> None:
    for currency in ("MXN", "USD", "CAD"):
        result = POLICY.normalize("123.456", currency)
        assert isinstance(result, NormalizedAmount)
        assert result.amount == Decimal("123.46")


def test_currency_is_case_insensitive() -> None:
    result = POLICY.normalize("10.00", "mxn")
    assert result == NormalizedAmount(Decimal("10.00"))


def test_round_half_up_boundaries() -> None:
    assert POLICY.normalize("0.005", "MXN") == NormalizedAmount(Decimal("0.01"))
    assert POLICY.normalize("0.0049", "MXN") == NormalizedAmount(Decimal("0.00"))
    assert POLICY.normalize("-0.005", "MXN") == NormalizedAmount(Decimal("-0.01"))


def test_unsupported_currency_returns_unsupported() -> None:
    result = POLICY.normalize("10.00", "EUR")
    assert isinstance(result, UnsupportedCurrency)
    assert result.currency == "EUR"


def test_unsupported_currency_has_no_numeric_interface() -> None:
    result = POLICY.normalize("10.00", "GBP")
    assert isinstance(result, UnsupportedCurrency)
    assert not isinstance(result, Decimal)  # not a Decimal
    with pytest.raises(TypeError):
        Decimal(result)  # no Decimal coercion
    with pytest.raises(TypeError):
        result + Decimal("1")  # no arithmetic path
