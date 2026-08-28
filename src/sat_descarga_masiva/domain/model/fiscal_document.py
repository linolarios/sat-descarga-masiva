"""Fiscal document identity & status (AGENT.md §4/§7a). Domain-only.

FiscalDocumentStatus is the SAT fiscal fact (VIGENTE/CANCELLED), whose
authoritative source is a Metadata observation — NEVER the parser. In CFDI-only
M2 no Metadata exists, so every document is UNKNOWN; undeterminable means
UNKNOWN, not a fabricated value.
"""

from __future__ import annotations

from enum import StrEnum


class FiscalDocumentStatus(StrEnum):
    UNKNOWN = "unknown"
    VIGENTE = "vigente"
    CANCELLED = "cancelled"
