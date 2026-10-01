"""M3 step 1: two status vocabularies that must never be conflated (D-M3-7b, §6).

`DocumentStatus` is the **query** side: our codes for the filter a Solicitud asks
for, persisted in the query record and mapped to satcfdi's words at the SOAP
boundary. A downloaded document's own state is `FiscalDocumentStatus`. Merging
them would let a query code stand in for a received status — the one thing the
posting eligibility of a cancellation depends on.
"""

from sat_descarga_masiva.domain.enums.catalog import DocumentStatus
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus


def test_the_query_filter_codes_are_the_persisted_codes() -> None:
    """These strings are stored in the query record, so they are a format, not a detail."""
    assert {status.name: status.value for status in DocumentStatus} == {
        "TODOS": "0",
        "VIGENTE": "1",
        "CANCELADO": "2",
    }


def test_the_query_filter_is_numeric_and_worded_only_on_the_wire() -> None:
    """§5: satcfdi's `'Vigente'`/`'Cancelado'` words stay in the adapter."""
    assert all(status.value.isdigit() for status in DocumentStatus)
    assert not any(status.value.isdigit() for status in FiscalDocumentStatus)


def test_received_status_is_a_separate_vocabulary() -> None:
    """§6a: a document's status carries an explicit unknown — never a query code."""
    assert {status.name: status.value for status in FiscalDocumentStatus} == {
        "UNKNOWN": "unknown",
        "VIGENTE": "vigente",
        "CANCELLED": "cancelled",
    }
    assert set(FiscalDocumentStatus) & set(DocumentStatus) == set()
