"""Live SAT. Opt-in: `pytest -m integration` with a FIEL de pruebas in env."""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.xfail(reason="TDD: implement the SAT gateways + facade", strict=False)
def test_authenticate_live() -> None:
    import os

    for var in ("SAT_FIEL_CER", "SAT_FIEL_KEY", "SAT_FIEL_PASSWORD"):
        if not os.getenv(var):
            pytest.skip(f"{var} not set")
    raise NotImplementedError
