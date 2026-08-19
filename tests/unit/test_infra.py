from sat_descarga_masiva.domain.enums.catalog import ServiceType
from sat_descarga_masiva.infrastructure.sat.endpoints import endpoints_for
from sat_descarga_masiva.infrastructure.sat.signing.profiles import SAT_V15_SIGNATURE_PROFILE


def test_endpoints_differ_by_service() -> None:
    cfdi = endpoints_for(ServiceType.CFDI)
    ret = endpoints_for(ServiceType.RETENCIONES)
    assert "retendescargamasiva" in ret.download
    assert cfdi.download != ret.download


def test_signature_profile_is_rsa_sha1() -> None:
    assert SAT_V15_SIGNATURE_PROFILE.signature_algorithm.endswith("rsa-sha1")
