"""M2.4: per-CFDI perspective router (AGENT.md §7a/§8). Pure, offline, additive."""

from hypothesis import assume, given
from hypothesis import strategies as st

from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import RawCfd, RawImpuestos
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.perspective import resolve_perspective

GENERIC = frozenset({"XAXX010101000", "XEXX010101000"})


def _raw(emisor: str, receptor: str | None) -> RawCfd:
    return RawCfd(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=emisor,
        receptor_rfc=receptor,
        conceptos=(),
        impuestos=RawImpuestos(),
        total="116.00",
    )


def test_matching_emitido_resolves_without_review() -> None:
    resolution = resolve_perspective(Rfc("AAA010101AAA"), _raw("AAA010101AAA", "BBB010101BBB"))
    assert resolution.perspective is Perspective.EMITIDO
    assert resolution.matched is True
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_UNDETERMINED) is False


def test_matching_recibido_resolves_without_review() -> None:
    resolution = resolve_perspective(Rfc("BBB010101BBB"), _raw("AAA010101AAA", "BBB010101BBB"))
    assert resolution.perspective is Perspective.RECIBIDO
    assert resolution.matched is True
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_UNDETERMINED) is False


def test_mismatch_is_undetermined_with_mismatch_flag() -> None:
    resolution = resolve_perspective(Rfc("CCC010101CCC"), _raw("AAA010101AAA", "BBB010101BBB"))
    assert resolution.perspective is Perspective.UNDETERMINED
    assert resolution.matched is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is True
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_UNDETERMINED) is False


def test_generic_nacional_matching_emisor_is_undetermined() -> None:
    resolution = resolve_perspective(Rfc("XAXX010101000"), _raw("XAXX010101000", "BBB010101BBB"))
    assert resolution.perspective is Perspective.UNDETERMINED
    assert resolution.matched is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_UNDETERMINED) is True
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is False


def test_generic_extranjero_matching_receptor_is_undetermined() -> None:
    resolution = resolve_perspective(Rfc("XEXX010101000"), _raw("AAA010101AAA", "XEXX010101000"))
    assert resolution.perspective is Perspective.UNDETERMINED
    assert resolution.matched is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_UNDETERMINED) is True
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is False


def test_generic_matching_neither_is_a_concrete_mismatch() -> None:
    resolution = resolve_perspective(Rfc("XAXX010101000"), _raw("AAA010101AAA", "BBB010101BBB"))
    assert resolution.perspective is Perspective.UNDETERMINED
    assert resolution.matched is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is True
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_UNDETERMINED) is False


def test_both_participants_match_is_undetermined() -> None:
    resolution = resolve_perspective(Rfc("AAA010101AAA"), _raw("AAA010101AAA", "AAA010101AAA"))
    assert resolution.perspective is Perspective.UNDETERMINED
    assert resolution.matched is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_UNDETERMINED) is True
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is False


def test_rfc_comparison_is_normalized() -> None:
    resolution = resolve_perspective(Rfc("  bbb010101bbb "), _raw("aaa010101aaa", "BBB010101BBB"))
    assert resolution.perspective is Perspective.RECIBIDO
    assert resolution.matched is True


def test_receptor_absent_cannot_be_recibido() -> None:
    resolution = resolve_perspective(Rfc("BBB010101BBB"), _raw("AAA010101AAA", None))
    assert resolution.perspective is Perspective.UNDETERMINED
    assert resolution.matched is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is True


def test_receptor_absent_with_emisor_contributor_resolves_emitido() -> None:
    resolution = resolve_perspective(Rfc("AAA010101AAA"), _raw("AAA010101AAA", None))
    assert resolution.perspective is Perspective.EMITIDO
    assert resolution.matched is True
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is False
    assert resolution.review_flags.has_open(ReviewFlagType.PERSPECTIVE_UNDETERMINED) is False


def test_mismatch_is_review_state_not_a_silent_resolution() -> None:
    resolution = resolve_perspective(Rfc("CCC010101CCC"), _raw("AAA010101AAA", "BBB010101BBB"))
    assert resolution.perspective is Perspective.UNDETERMINED
    assert resolution.review_flags.open_of(ReviewFlagType.PERSPECTIVE_MISMATCH)


def test_matched_true_only_when_perspective_resolved() -> None:
    resolved = (
        resolve_perspective(Rfc("AAA010101AAA"), _raw("AAA010101AAA", "BBB010101BBB")),
        resolve_perspective(Rfc("BBB010101BBB"), _raw("AAA010101AAA", "BBB010101BBB")),
    )
    assert all(r.matched is True for r in resolved)
    unresolved = (
        resolve_perspective(Rfc("CCC010101CCC"), _raw("AAA010101AAA", "BBB010101BBB")),
        resolve_perspective(Rfc("AAA010101AAA"), _raw("AAA010101AAA", "AAA010101AAA")),
        resolve_perspective(Rfc("XAXX010101000"), _raw("XAXX010101000", "BBB010101BBB")),
    )
    assert all(r.matched is False for r in unresolved)
    assert all(r.perspective is Perspective.UNDETERMINED for r in unresolved)


def test_review_reason_is_non_empty() -> None:
    resolution = resolve_perspective(Rfc("CCC010101CCC"), _raw("AAA010101AAA", "BBB010101BBB"))
    open_flags = resolution.review_flags.open_of(ReviewFlagType.PERSPECTIVE_MISMATCH)
    assert open_flags
    assert all(flag.reason for flag in open_flags)


_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ALNUM = _LETTERS + "0123456789"
_rfc_strategy = st.builds(
    lambda p, d, h: p + d + h,
    st.text(alphabet=_LETTERS, min_size=3, max_size=4),
    st.text(alphabet="0123456789", min_size=6, max_size=6),
    st.text(alphabet=_ALNUM, min_size=3, max_size=3),
)


@given(a=_rfc_strategy, b=_rfc_strategy, c=_rfc_strategy)
def test_router_invariants(a: str, b: str, c: str) -> None:
    assume(len({a, b, c}) == 3)
    assume(not ({a, b, c} & GENERIC))
    raw = _raw(a, b)
    assert resolve_perspective(Rfc(a), raw).perspective is Perspective.EMITIDO
    assert resolve_perspective(Rfc(b), raw).perspective is Perspective.RECIBIDO
    mismatch = resolve_perspective(Rfc(c), raw)
    assert mismatch.perspective is Perspective.UNDETERMINED
    assert mismatch.matched is False
    assert mismatch.review_flags.has_open(ReviewFlagType.PERSPECTIVE_MISMATCH) is True
