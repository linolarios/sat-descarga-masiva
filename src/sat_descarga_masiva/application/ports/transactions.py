"""Port for the per-document transaction boundary (M2.8, §4/§11 M2).

The application must not know how a transaction is spelled: it opens one, writes
the projection row and the review facts that belong to that document, and is
guaranteed that either both survive or neither does. The adapter owns the
mechanism (one shared SQLite connection, explicit ``BEGIN IMMEDIATE``), so the
guarantee here is behavioural, not a connection-state inspection.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol


class UnitOfWork(Protocol):
    """One unit of work: enter begins, a clean exit commits, an exception rolls back."""

    def transaction(self) -> AbstractContextManager[None]: ...
