"""The account mapping port — which account a role resolves to, under which version (§8a:207).

A rule names `AccountRole`s and never an account: turning a role into the client's
chart-of-accounts entry is this port's job, so the concrete numbers stay a
human-owned, versioned decision (AGENT.md §8a:207's
``ClaveProdServ → AccountingCategory → AccountRole → Account`` YAML). Two consequences
the engine depends on:

- the resolution is **versioned**: ``AccountMapping.mapping_version`` is mandatory and
  travels in every posting's identity (§8:194) while being recorded with the posting
  (§8:159), so a line booked years ago stays explainable;
- the resolution is **total or nothing**: a role the mapping does not name is
  ``AccountMapping.resolve``'s ``None``, which the `PostingEligibilityValidator` turns
  into ``UNMAPPED_ACCOUNT`` + ``NEEDS_REVIEW`` — never a default account.

One mapping per managed client (``mapping_for(contributor_rfc)``): the same role lands in
a different chart for a different client. The *file layout* of the YAML — including the
deferred two-layer composition of catalog defaults with per-client overrides — is the
adapter's business; this port owns only the question.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.domain.model.value_objects import Rfc


@dataclass(frozen=True)
class AccountMapping:
    """One version of one client's chart of accounts (§8a:207).

    ``mapping_version`` is mandatory and non-blank because it is part of every posting's
    identity: a mapping without a version could not say under which decision a line was
    booked (§8:194). An empty ``accounts`` is legitimate — it resolves nothing, which the
    validator reports as ``UNMAPPED_ACCOUNT`` rather than as a broken mapping.
    """

    mapping_version: str
    accounts: Mapping[AccountRole, str]

    def __post_init__(self) -> None:
        if not self.mapping_version.strip():
            raise ValueError(
                "a mapping needs its version: §8:194 keys a posting by"
                " rule_version + mapping_version, so an unversioned mapping cannot post"
            )
        for role, account in self.accounts.items():
            if not account.strip():
                raise ValueError(f"{role.value}: an empty account is not a resolution")

    def resolve(self, role: AccountRole) -> str | None:
        """The account ``role`` resolves to, or ``None`` when this mapping does not name it.

        ``None`` is the whole vocabulary for "unmapped": §8a:207 forbids a default, so the
        caller must route it to review rather than pick something plausible.
        """
        return self.accounts.get(role)

    def unresolved(self, roles: Iterable[AccountRole]) -> tuple[AccountRole, ...]:
        """The roles of ``roles`` this mapping does not resolve, deduplicated, in first-seen order.

        First-seen order (not sorted) is what makes a refusal reproducible: the reason a human
        reads lists the legs in the order the rule proposed them.
        """
        return tuple(dict.fromkeys(role for role in roles if self.resolve(role) is None))


class MappingProvider(Protocol):
    """The managed client's chart of accounts, as the engine asks for it (§8a:207).

    ``mapping_for`` answers for the **contributor** whose books are being written, not for
    the document's issuer: §8:194 keeps the contributor in the posting identity precisely
    because the same CFDI lands in two clients' books under two different charts.
    """

    def mapping_for(self, client_rfc: Rfc) -> AccountMapping: ...
