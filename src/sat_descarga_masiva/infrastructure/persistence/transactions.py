"""SqliteUnitOfWork — the explicit per-document transaction boundary (M2.8, §4/§11 M2).

Python's ``sqlite3`` opens transactions *implicitly* before DML and holds them until
``commit()``. That is fine for a single ``save()``, but M2.8's boundary is a decision
of the application: "these writes are one document's unit of work". So this adapter
runs the connection in SQLite's own autocommit mode (``isolation_level = None``, so
the driver never opens a transaction behind our back) and issues the transaction
statements itself.

``BEGIN IMMEDIATE`` rather than the deferred default: the write lock is taken up
front, so a competing writer fails at BEGIN instead of half-way through a unit, and a
clean exit commits everything the unit wrote while any exception rolls all of it back.

Two consequences, both deliberate:

- The repositories and stores of this package must not commit inside a unit. They are
  built with ``commit=False`` (see ``SqliteDocumentRepository``/``SqliteReviewFlagStore``)
  when M2.8 wires them, because their own COMMIT would end the unit early and destroy
  the all-or-nothing guarantee.
- Units do not nest. A second ``transaction()`` while one is open raises
  ``sqlite3.OperationalError`` instead of silently flattening into the outer one; M2.8
  opens exactly one unit per document.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class SqliteUnitOfWork:
    """Implements application.ports.transactions.UnitOfWork over one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        # No implicit BEGIN: from here on, a transaction exists only because this
        # class opened one, and a stray statement autocommits instead of lingering.
        self._conn.isolation_level = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Begin immediately; commit on a clean exit, roll back on any exception."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")
