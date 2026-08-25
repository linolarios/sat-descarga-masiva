# AGENT.md — SAT-CFDI Accounting Tool (single source of truth · v1.3.1)

> **This is the only guide.** Everything the agent needs is here — architecture, the SAT library decision, the accounting rules, the CLI flows, the build order, and per-milestone task notes. There are no separate `ARCHITECTURE.md`/`ACCOUNTING.md` files and no out-of-band prompts.
>
> **Prime directive:** *The agent must never invent a fiscal, accounting, SAT-protocol, or persistence rule that is not defined by this document or an explicitly approved change. When behavior is unspecified, stop at the nearest well-defined boundary and ask the human.* You work **with** a human (solo dev) who also writes code.

## Contents
1. Mission · 2. Golden rules · 3. Stack · 4. Architecture, dependency rules & ledger · 5. SAT ingestion (`satcfdi`) · 6. Acquisition: source, metadata, cursor · 7. CLI — two flows · 8. Accounting engine + PostingEligibility · 9. DIOT · 10. AI & Settings · 11. Build order · 12. Testing · 13. Git/CI & Do/Don't · 14. References

---

## 1. Mission
A **complete local accounting tool** for Mexico: per client, **download** their CFDI from the SAT, then **process** them (parse → deterministic *asientos contables* → Excel → **DIOT** draft). All accounting is **rule-based and deterministic — no LLM inference.**

## 2. Golden rules
1. **Test-first for deterministic behavior.** Production behavior that is deterministic and unit-testable is written test-first (Red→Green→Refactor, one behavior per commit). Infrastructure spikes (e.g. discovering the `satcfdi` API) may be explored separately, but **no exploratory code is merged without corresponding automated tests.**
2. **Layer boundaries hold** (§4). `domain` imports no `requests`/`lxml`/`cryptography`/`satcfdi`/`sqlite3`/SOAP/URLs. SAT/XML/crypto/SQLite live only in `infrastructure/`.
3. **Fiscal state ≠ accounting state.** Only `contabilidad/` knows account names.
4. **Nothing uncertain is posted.** Every entry is `POSTED`/`NEEDS_REVIEW`/`SKIPPED`; a rule proposes, the **`PostingEligibilityValidator`** decides (§8). Ambiguity ⇒ `NEEDS_REVIEW`, never a silent default.
5. **Money is `decimal.Decimal`, never `float`** (§8 `MoneyPolicy`); `Debe == Haber` is a hard gate.
6. **Never touch real secrets.** No `.cer`/`.key`/password/token committed, logged, or in `repr`/exceptions. Secrets never logged; an RFC may be logged only where operationally needed. Unit tests use a generated self-signed FIEL; integration uses a *FIEL de pruebas* from env. Never call live SAT in unit tests or CI.
7. **Don't simplify away the §8 corrections.**
8. **Ask the human on fiscal/legal/ambiguous calls** (see Prime directive).

## 3. Stack
Python 3.11+, `uv` (commit `uv.lock`). Runtime: `satcfdi` (**pinned**, e.g. `satcfdi~=X.Y`), `cryptography`, `lxml`, `requests`, `pydantic`, `openpyxl`, `rich`, `pyyaml`, `keyring`. Dev: `pytest`, `pytest-cov`, `hypothesis`, `freezegun`, `responses`, `ruff`, `mypy`, `pre-commit`. CI installs the **locked** version; an agent must not casually upgrade `satcfdi` while working on accounting.
```bash
make check   # ruff + ruff format --check + mypy(strict, src) + unit tests  ← commit gate
make test / make itest
```

## 4. Architecture, dependency rules & ledger

**Pipeline (the accounting gate is explicit):**
```
SAT → SAT adapter → ingestion → immutable source → fiscal parsing → fiscal events
    → fiscal-state projection → accounting rules → ProposedJournalEntry
    → PostingEligibilityValidator → POSTED / NEEDS_REVIEW / SKIPPED → ledger
                                                                       ├── reconciliation
                                                                       ├── Excel
                                                                       └── DIOT
```

**Package map** (existing SAT-client code does not move/rename; new subsystems are siblings):
```
src/sat_descarga_masiva/
├── domain/ application/ infrastructure/ facade/ config/   # SAT client (INGESTION); facade = seam
├── source/        # immutable raw artifacts (append-only) + derived extraction/classification/dedup
├── fiscal/        # parse CFDI 4.0 → typed model; derive fiscal state
├── contabilidad/  # deterministic rules 4.1–4.15 → ProposedJournalEntry (+ PostingEligibilityValidator)
├── ledger/        # application PORTS (repositories/projections)
├── export/        # Excel renderer (ledger projection → xlsx)
├── diot/          # DIOT projection → SAT bulk-load .txt
├── recon/         # conservation checks
├── ai/            # OPTIONAL flag-only anomaly seam
└── cli/           # thin: parse/validate/load config/invoke use case/render — NO business logic
```

**Domain responsibilities (keep these from bleeding into each other):** `fiscal/` = *who is the taxpayer?* (identity: RFC, PersonaTipo, régimen, `ContributorProfile` from the CSF) · `contabilidad/` = *how do we account for a transaction?* · `ledger/` = *what was recorded?* · `infrastructure/` = *how do we talk to SAT?*. Contributor identity is a **fiscal** concept and must never migrate into `contabilidad/`.

**Dependency rules (enforceable):**
1. `domain` imports only stdlib/domain abstractions.
2. `application` imports `domain` + application ports.
3. `fiscal`/`contabilidad` depend on domain/application abstractions, **never** on infrastructure implementations.
4. `infrastructure` implements application ports.
5. `satcfdi`, `lxml`, `requests`, `cryptography`, `openpyxl`, `sqlite3`, SOAP/XML are **infrastructure** concerns.
6. `cli` invokes use cases; contains no business rules and no `for client in folders: <logic>`.
7. `export`/`diot` consume ledger **projections**; they never mutate accounting state, and `contabilidad` never imports `openpyxl`.
8. `ai` is optional and has **no authority** over deterministic accounting.
9. `facade` is the public seam for SAT ingestion.

**Persistence rule:** `ledger/` exposes application **ports** (repositories/projections). SQLite implementations live in `infrastructure/persistence/`. `domain`, `fiscal`, `contabilidad` **never** import `sqlite3`/SQLAlchemy/concrete adapters.

**Two truths & four distinct responsibilities:** SQLite is the *transactional accounting + processing-state store*; the **immutable source artifacts are the authoritative evidence** for downloaded documents. Keep these separate: **(a) source artifacts** — raw ZIP/extracted XML + hashes (evidence, immutable); **(b) `documents`** — one row per UUID = current identity/projection only (issuer/receiver/type/version/hashes), **not** a mutable status field; **(c) `metadata_snapshots`** — timestamped status *observations* (§6); **(d) `fiscal_events`** — the **single authoritative append-only history** from which current fiscal state is projected. A metadata observation that **materially changes fiscal state** (e.g. vigente→cancelado) **appends a `fiscal_event`**; a snapshot that changes nothing is recorded as an observation only. There is exactly one authoritative history (`fiscal_events`); `documents` and any cached state are projections, never the authority. *Append-only event history, not full event-sourcing/replay.*

**`MoneyPolicy`:** one central policy defines precision, rounding mode, currency scale. XML numeric strings convert **directly** to `Decimal`; `float` is prohibited in fiscal/accounting code. Don't scatter `.quantize(...)`.

## 5. SAT ingestion — `satcfdi` adapter (sanctioned)
`satcfdi` is the **sanctioned SAT protocol adapter for v1**, imported **only** by `infrastructure/sat/`. The application depends only on the existing gateway ports. The adapter translates `satcfdi` objects into project DTOs — **no `satcfdi` object, enum, exception, or XML element may cross the infrastructure boundary** (keep your own domain enums even though `satcfdi` exposes `EstadoSolicitud`/status codes). The adapter is **transport/protocol only**; it MUST NOT own cursor, retry, partition, metadata/CFDI correlation, package persistence, or accounting.

**The exact `satcfdi` API is version-dependent — verify it against the pinned version before writing/modifying the adapter** (`python -c "import satcfdi.pacs.sat as s; help(s.SAT)"`).

```text
>>> NON-NORMATIVE pseudocode — DO NOT COPY VERBATIM. The installed satcfdi is authoritative. <<<
```
The current `SAT` object exposes roughly `recover_comprobante_emitted_request()`, `recover_comprobante_received_request()`, `recover_comprobante_status()`, `recover_comprobante_download()` (+ Retenciones variants); a `Signer.load(certificate=…, key=…, password=…)` (password may be a **str**, not `.encode()`). Confirm names/signatures before use. Wire the adapter in at composition (`config/settings.py`/facade).

## 6. Acquisition — source, metadata, cursor
**Immutable source:** raw downloaded artifacts are **append-only and immutable**. Extraction/classification/deduplication produce **derived** records and never modify the original artifact.
```
source/raw/<zip> + manifest.json      # append-only, authoritative evidence
source/extracted/<xml>                # reproducibly derived from the ZIP
```
**Per-package manifest** (`manifest.json`): `sha256, client_rfc, service, direction, request_id, package_id, downloaded_at, satcfdi_version, application_version, query, policy_version`.

**Identity — UUID ≠ artifact hash:**
- `same UUID + same SHA-256` → duplicate artifact (skip).
- `same UUID + different SHA-256` → **integrity/conflict** → `NEEDS_REVIEW`; **never silently replace** the previous XML.

**CFDI and Metadata are two independent, independently-resumable acquisition jobs** (a Metadata failure must not fail a succeeded CFDI download); results are joined by UUID. Metadata is a **historical observation**, not a flag:
```
MetadataSnapshot{ uuid, status, cancellation_date, cancellation_reason, substitution_uuid, retrieved_at, source_hash }
```

**`DownloadCursor`** (define each field; an interrupted run must not create gaps):
```
DownloadCursor{ client_rfc, service, direction, query_start, query_end,
                last_successful_boundary, last_request_id, last_completed_at }
```
**`IncrementalDownloadPolicy{ overlap: timedelta }`** — the late-CFDI overlap is a **versioned policy**, not a magic number; record `policy_version` + `overlap_used` on the download job.

**Correlation IDs** thread through logs/records so one CFDI is traceable end-to-end: `pipeline_run → download_job → request_id(IdSolicitud) → package_id(IdPaquete) → source artifact → uuid → fiscal_event → journal_entry → excel/diot row`.

**§6a — Download & integrity security.** Be explicit about what each control does and does not guarantee:
1. **TLS verification is always on.** All SAT calls use HTTPS with certificate verification enabled — **never** `verify=False`/disabled TLS, not even "temporarily to get it working." A cert/TLS failure is a hard error, never bypassed.
2. **SHA-256 is *store* integrity, not SAT authenticity.** The per-ZIP/per-XML hashes (§6) prove *our stored copy hasn't changed since we saved it* and drive the UUID/hash conflict rule. The SAT Descarga Masiva service does **not** provide a signed checksum of each package, so there is **no** end-to-end SAT-issued hash to verify against — do not imply one exists.
3. **Real authenticity = the CFDI's own signature (validated in M2).** Each CFDI carries the SAT `tfd:TimbreFiscalDigital` (`SelloSAT`) + emisor `Sello`; validating them (via `satcfdi`) proves authenticity regardless of transport — stronger than any ZIP checksum. **Deterministic outcome map:** `valid` → proceed; `invalid` (sello mismatch) → `NEEDS_REVIEW`; `absent` (no TFD) → `NEEDS_REVIEW`; `unsupported` (version/algorithm the validator can't check) → `NEEDS_REVIEW` (never silently pass); `validation_error` (validator couldn't run, e.g. cert unreachable) → `NEEDS_REVIEW` for that document, don't block others, record the reason.
4. **Safe extraction.** Treat downloaded ZIPs as untrusted: bound total decompressed size and entry count (reject zip-bombs), and reject entries with absolute paths or `..` traversal. Extract only expected `.xml` (+ metadata) members.
5. **Secrets at rest:** the FIEL password lives only in `CredentialVault`; the private key stays inside the FIEL object; neither is ever written to `source/`, logs, `repr`, or manifests.
6. **Optional live status verification (`StatusVerificationPolicy`).** Beyond the Metadata join, the tool may query the SAT for a CFDI's **current** vigente/cancelado status at validation time (`satcfdi` supports per-UUID validation). Rules: it is a **timestamped `MetadataSnapshot`** like any other observation (same immutability — a fresh "cancelado" on an already-`POSTED` entry yields a 4.15 compensating reversal as `NEEDS_REVIEW`, never a mutation, §8); it is **opt-in and bounded** by a freshness policy (e.g. only re-check when the newest snapshot is older than *N* days, or only for documents about to be `POSTED`) because it is a per-UUID SAT call subject to rate limits; and a **failed live check never blocks posting** — fall back to the latest `MetadataSnapshot` and record the staleness. Metadata remains the default source of truth for status.

## 7. CLI — two flows (Download, then Process)
The user pre-creates one `/data/<cliente>/` per client. Its **`identidad/`** subfolder holds the contributor's identity artifacts — `<RFC>.cer`, `<RFC>.key`, and `constancia_situacion_fiscal.pdf` (**CSF**). Downloaded CFDIs live under **`source/raw/`** (authoritative, immutable ZIPs + manifests) and **`source/extracted/<Tipo>/`** (derived, reproducible, classified XMLs — this *is* the "CFDIs" set; any UI label like `CFDIs/` refers to `source/extracted/`, never a separate authoritative copy). `.cer`/`.key` are **cryptographic identity** (SAT auth only); the **CSF is fiscal identity**; extracted CFDIs are **transactional data** — three distinct concerns (§7a). **Download** is separated because the SAT WS is the one unreliable external step. **Process requires an accounting period `YYYY-MM`** (don't let the agent invent period semantics) **and a resolved `ContributorProfile`** (§7a). Each flow is success/fail **per client**; the CLI only invokes use cases (all iteration/logic lives in `ExecuteDownloadUseCase`/`ExecuteProcessUseCase`).

**§7a — Contributor identity (fiscal identity is first-class, owned by `fiscal/`).** Three concerns stay separate: **cryptographic identity** (`.cer/.key/password` → SAT auth, `infrastructure`), **fiscal identity** (CSF → `ContributorProfile`, `fiscal/`), **transactional data** (CFDIs). `fiscal/` answers *who is the taxpayer?*, `contabilidad/` *how to account?*, `ledger/` *what was recorded?*, `infrastructure/` *how to talk to SAT?*.

`fiscal/` owns `contributor.py`/`taxpayer.py`/`rfc.py`/`regimen.py` with:
```
PersonaTipo ∈ { FISICA, MORAL, GENERICO_NACIONAL, EXTRANJERO }
ContributorProfile{ rfc, legal_name, persona_tipo, regimenes_fiscales[], codigo_postal,
                    obligaciones, csf_hash, csf_obtained_at, profile_version }
```
- **CSF is the onboarding source of fiscal identity**, and an **immutable source artifact**: hash it (`csf_hash`, SHA-256), retain the original, never modify it; an accounting entry → `ContributorProfile` → `csf_hash` → original CSF is the audit chain.
- **RFC structure is *validation*, not the identity** (13≈física, 12≈moral); the **CSF is authoritative** for `persona_tipo`/`regimenes`. Generic/foreign RFCs (`XAXX010101000`, `XEXX010101000`) map to `GENERICO_NACIONAL`/`EXTRANJERO` — **never** silently to `FISICA` just because of length.
- **Identity-consistency checks (must pass, never silently continue):** (1) **Static contributor identity** — `CSF RFC == e.firma cert RFC == configured contributor RFC` (a fixed three-way equality, checked once at onboarding). (2) **Per-CFDI perspective match** — the contributor RFC must equal the *appropriate* participant, not every participant: `EMITIDO → contributor RFC == Emisor RFC`, `RECIBIDO → contributor RFC == Receptor RFC` (this **is** the §8 perspective router; a RECIBIDO CFDI legitimately has a different Emisor). Any mismatch, or an undetermined perspective, ⇒ `NEEDS_REVIEW`. (Processing under the wrong contributor RFC would contaminate the ledger.)
- CSF is a PDF whose layout varies: extract only what parses confidently and **require human confirmation** (versioned config, `profile_version`) — never guess; régimen undeterminable ⇒ that client's Process run is `NEEDS_REVIEW`/error.
- The `ContributorProfile` is an **input to accounting policy and DIOT obligation**; it does **not** own accounting rules and the **password never lives in it** (stays in `CredentialVault`).

| Menu | Action |
|---|---|
| 1 | **Download** — for every folder with `.cer` **and** `.key`, fetch its CFDI (retry on WS failure) |
| 2 | **Process** — extract → parse → accounting → Excel → DIOT, for a period `YYYY-MM` |
| 3 | **Settings** (`ConfigEditor`) · 4 | **Exit** |

**Download flow:** **download-ready** = `.cer`+`.key`+password present and the **cert RFC matches the folder RFC** (missing/mismatch ⇒ per-client error, others continue). *(The fuller onboarding gate — CSF present, `ContributorProfile` resolved, static three-way RFC identity per §7a — is required for **Process**, not for Download, so a client with a valid FIEL can still download while its fiscal identity is being confirmed.)* The CLI prompts for the query **date range** (`FechaInicio`/`FechaFin`), **defaulting to resume from the client's `DownloadCursor.last_successful_boundary`** (minus the `IncrementalDownloadPolicy.overlap`) so the common case is one keypress. Password via `CredentialVault`; run the **independent** CFDI + Metadata jobs; `source/` stores raw ZIP + manifest; advance `DownloadCursor`. **Retry transient** (timeout/5xx/app-`404`) with backoff; `5011`→wait, `5003`→partition. **Fatal, no retry:** `300`–`305`, `5005`. **No CFDI found** (`5004`/0 packages) ⇒ per-client empty/error (check period/RFC), not an infinite retry. Per-client isolation; resumable via `download_jobs`.

**Process flow (deterministic, needs no FIEL):** requires a resolved `ContributorProfile` (§7a); an empty/absent extracted set, or an undetermined régimen, ⇒ per-client error/`NEEDS_REVIEW`. Stages: `source` extract/classify (dedup per §6) → `fiscal` → `contabilidad` → `export` → `diot` (if obligated, §9). **Precise failure semantics:** the **unit of atomicity is the document** — each CFDI's fiscal events + proposed/validated entries commit in **one per-document transaction**, so a failure quarantines *that* document without rolling back others; **individual malformed documents are quarantined and do not abort other documents**; a **stage (infrastructure) failure prevents later stages** for that client. Parse result is `ParseOutcome ∈ {PARSED, PARTIAL, FAILED}` (FAILED = nothing parsed ⇒ client error; PARTIAL proceeds with a quarantine count). `NEEDS_REVIEW` is **not** a failure. **No cross-stage rollback.** Both flows log to `/logs/<run-timestamp>.log` and persist `PipelineRun{run_id, flow, client_rfc, period, status, failed_stage, per_stage{counts,message}}`.

## 8. Accounting engine + PostingEligibility (`contabilidad/`)

**A rule proposes; the validator decides.** Rules emit a `ProposedJournalEntry`; only **`PostingEligibilityValidator`** may assign `POSTED`. `POSTED` requires **all** of: source state eligible (vigente) at posting time · all required source fields present · deterministic account mapping · deterministic FX valuation · valid `Decimal` amounts · balanced `Debe==Haber` · no unresolved review flags · supported rule · `source_uuid`+`source_hash` present · `rule_version`/`policy_version`/`mapping_version` recorded. **Any failure ⇒ `NEEDS_REVIEW`.** Balance is checked twice (rule output *and* before ledger commit — defense in depth).

**`SKIPPED` vs `NEEDS_REVIEW` (deterministic per document, never a judgment call):** `SKIPPED` = deliberately outside accounting scope, **no human decision needed** — exactly: `TipoComprobante T`; `N` (payroll) **recibido**; `R` (retenciones) **recibido** (acuse, feeds DIOT). `NEEDS_REVIEW` = system **cannot safely determine** treatment — exactly: `N` **emitido** (payroll draft), `R` **emitido** (withholding draft), unmapped account, ambiguous FX, cancelled-then-posted, missing REP original, invalid/absent/unsupported sello (§6a), **any unsupported document type or CFDI version**, or perspective undetermined. **Unsupported is never silently `SKIPPED`.**

**Perspective router:** `EMITIDO` if `client_rfc==emisor`, `RECIBIDO` if `==receptor`. **Perspective undetermined ⇒ `NEEDS_REVIEW`** (or explicit `INVALID_SOURCE`) — **never silent `SKIPPED`**. Normalize RFCs. **Generic RFCs (`XAXX010101000`/`XEXX010101000`) are never by themselves sufficient to infer perspective.** Retenciones read emisor/receptor from the `retenciones:` root.

**Régimen fiscal & PersonaTipo (owned by `fiscal/`, §7a):** `contabilidad/` **consumes** `ContributorProfile.regimenes`/`persona_tipo` only where a **documented** accounting/fiscal rule requires it, and records the régimen applied on each entry. It must **not** own CSF parsing or RFC classification, and must **not** branch on `persona_tipo`/régimen (`if FISICA … elif MORAL …`) unless a specific rule here mandates it; any régimen-specific treatment not explicitly defined ⇒ `NEEDS_REVIEW`, never inferred.

**Posting state vs review state (separate — do not conflate).** An entry's **posting state** ∈ {`PROPOSED`, `POSTED`, `SKIPPED`} is assigned once by the `PostingEligibilityValidator` and is **immutable** thereafter. **Review state** is an independent additive layer: zero or more `ReviewFlag`s (validator, AI, or later discovery) may open/close against an entry **without changing its posting state**. `NEEDS_REVIEW` = *a PROPOSED entry the validator could not post* **or** *any entry carrying an open flag* — a review condition, not a mutation. `POSTED` is a permanent historical fact; corrections happen through **new** compensating records (Rule 4.15), never by editing the original.

**Ledger fact invariant:** only validated `POSTED` entries are **accounting facts** and feed sub-ledgers / IVA / DIOT / reconciliation. `PROPOSED`, `SKIPPED`, and flagged records are persisted for audit but are **not** accounting facts.

**`ASSUMED_PUE` (explicit):** an **accounting assumption** that a `PUE` CFDI is a completed single-exhibition payment (the SAT presumption) — **not** independently verified payment evidence (no bank movement confirmed). The cash leg goes to a **clearing** account and stays `ASSUMED_PUE` until bank reconciliation promotes it to `SUPPORTED_BY_BANK`; it never asserts a specific bank movement.

**Rule contract (every rule 4.x follows this shape):** `preconditions` (required source fields/state; unmet ⇒ `NEEDS_REVIEW`) → typed `inputs` (from the fiscal model) → `calculation` (`Decimal`/`MoneyPolicy`) → `journal_lines` (must balance) → explicit `review_conditions`/`skip_conditions`. A rule is a **pure function** returning `ProposedJournalEntry` (never self-`POSTED`), exhaustively covered by golden + mutation tests (§12).

**Rules 4.1–4.15** (base = **SubTotal − Descuento**):

| Type | Persp. | Method | Rule | Entry |
|---|---|---|---|---|
| I | EMITIDO | PUE | 4.1 | DR Clearing=Total · CR Ingresos=base · CR IVA Trasl. Cobrado=IVA · `ASSUMED_PUE` |
| I | EMITIDO | PPD | 4.2 | DR Clientes=Total · CR Ingresos=base · CR IVA Trasl. No Cobrado=IVA |
| I | RECIBIDO | PUE | 4.3 | DR Gasto/Inv=base · DR IVA Acred. Pagado=IVA · CR Clearing=Total · `ASSUMED_PUE` |
| I | RECIBIDO | PPD | 4.4 | DR Gasto/Inv=base · DR IVA Pendiente=IVA · CR Proveedores=Total |
| E | EMITIDO/RECIBIDO | — | 4.6–4.9 | Sales return / vendor refund-credit (mirror of I) |
| P | EMITIDO *(you received)* | — | 4.5a | cash IN, per DoctoRelacionado |
| P | RECIBIDO *(you paid)* | — | 4.5b | cash OUT, per DoctoRelacionado |
| N | EMITIDO/RECIBIDO | — | 4.10/4.11 | payroll `NEEDS_REVIEW` draft / SKIPPED |
| T | — | — | 4.12 | SKIPPED |
| R | EMITIDO/RECIBIDO | — | 4.13/4.14 | withholding `NEEDS_REVIEW` draft / SKIPPED (feeds DIOT) |
| any | — | — | 4.15 | cancellation reversal as `NEEDS_REVIEW` |

- **Discounts:** revenue/expense **and** IVA base use `SubTotal − Descuento`. `discount_policy ∈ {net, gross+contra}`. Never credit `Ingresos = SubTotal` ignoring the discount.
- **REP (4.5):** loop **every** `pago20:DoctoRelacionado`; model `Pago{currency, exchange_rate, amount, docs[]}` and `DoctoRelacionado{uuid, ImpSaldoAnt, ImpPagado, ImpSaldoInsoluto, impuestos_dr[]}`. IVA transfer from **actual `impuestos_dr[]`** — aggregate by tax type/rate/factor; **never** `impuestos_dr[0]`. Invariant (when source has the fields): `ImpSaldoAnt − ImpPagado == ImpSaldoInsoluto`, else `NEEDS_REVIEW`. Original not in ledger ⇒ post cash, flag IVA transfer `NEEDS_REVIEW`. Type-P has no header `MetodoPago`/`FormaPago`; `UsoCFDI=CP01`.
- **FX:** `FXRateProvider` port (`get_rate(currency, date) -> FXRate`) with `CfdiExchangeRateProvider` / `ConfiguredRateProvider` / `BanxicoRateProvider` and an `FXRateResolutionPolicy`. Accounting **never** calls Banxico directly. Convert to MXN; record `valuation_date/source/rate/policy_version`; ambiguous ⇒ `NEEDS_REVIEW`.
- **Cancellation (temporal):** status from the `MetadataSnapshot` join (not the XML). Posting eligibility is evaluated against the **latest known fiscal state at posting time**; distinguish `posted_at` from the snapshot's `retrieved_at`/`source_state_effective_at` so a later metadata refresh cannot rewrite history. Never-posted `Cancelado` ⇒ `SKIPPED`. Previously `POSTED` then `Cancelado` ⇒ **4.15** compensating reversal (opposite sides, dated in the **cancellation** period) as `NEEDS_REVIEW`. **Posted journal entries are immutable; subsequent fiscal-state changes generate compensating entries, never mutate the original.** motivo `01` = *replaced* via `TipoRelacion 04`.
- **Idempotency = `PostingFingerprint`** over `{contributor_rfc, source_uuid, rule_id, rule_version, line_key, mapping_version}`. `contributor_rfc` is included because the **same CFDI UUID legitimately appears in two managed clients' folders** (client A emitido, client B recibido) with different perspective and different books; `rule_id` already encodes perspective (4.1 EMITIDO vs 4.3 RECIBIDO are distinct rules), so perspective needs no separate field once `contributor_rfc` + `rule_id` are present. **Historical `POSTED` entries are immutable**; a new `mapping_version` applies only to future processing or an explicit re-post/rebuild workflow — never silent rebuild.
- **Classification:** `ClaveProdServ → AccountingCategory → Account` (unmapped ⇒ `NEEDS_REVIEW`; capital goods ⇒ fixed-asset review). Egreso: `CfdiRelacionados/@TipoRelacion` (`01`/`07`/`04`, possibly multiple UUIDs). **Taxes:** Traslados+Retenciones; `TipoFactor∈{Tasa,Cuota,Exento}`; 0%≠exento; IVA=`002`.
- **Reconciliation (`recon/`):** `AR_close = AR_open + invoices − collections − credit_notes ± adjustments` (needs imported opening balances; else movements-only); IVA conservation from the event history; REP fiscal `Σ ImpPagado == Pago.Monto` (payment ccy) vs accounting MXN (separate). **Opening balances come from an `OpeningBalanceProvider` (external/manual input) — never derived from CFDI.**

## 9. DIOT — `diot/`
Monthly per-vendor report of IVA pagado/acreditable/retenido/trasladado, **for a period `YYYY-MM`**. As of 2025 it is filed via the SAT **online platform** with a bulk-load `.txt` (pipe-delimited) — *confirm the current channel/structure against the SAT instructivo; don't hardcode it.* **The tool generates + pre-validates the file; the human uploads/signs with e.firma.** **DIOT obligation comes from the client's `ContributorProfile.obligaciones` (from the CSF, §7a)**, confirmable in settings (`files_diot`); support "sin operaciones". Data from the ledger's RECIBIDO projection — not re-parsed XML. Layout is a **versioned `DiotLayoutProfile{ version, effective_from, effective_to, field_count, encoding, delimiter, fields[], source_document }`**. Output `/output/<Cliente>_DIOT_<AAAAMM>.txt` (+ optional Excel mirror). Treat as a **draft/reconciliation** artifact (the SAT platform also pre-fills from CFDIs).

## 10. AI seam & Settings
**`ai/`** (optional): `AnomalyDetector.review(entries, ledger_view) -> list[ReviewFlag]`. **Posting state and review state are separate** (§8): AI (and any later discovery) **raises a `ReviewFlag` against** an entry — it opens/attaches review state; it **never changes the entry's posting state**. A `POSTED` entry stays `POSTED` (an immutable historical fact) even while flagged; resolution happens through new records (a 4.15 compensating entry or a human clearing the flag), never mutation. AI **cannot** modify amounts, change accounts, create journal lines, alter posting state, or change fiscal state. Ship a `NullAnomalyDetector`. **Settings — `ConfigEditor`:** `rich` forms over YAML (`ClaveProdServ→Category→Account`, `FormaPago→banco`, retención accounts, discount/FX/PUE/payroll switches, `files_diot`, active `DiotLayoutProfile`/`SignatureProfile`/`IncrementalDownloadPolicy` versions). Validated on save; `mapping_version` bumped for traceability.

## 11. Build order (with incremental schema)
- **M1 — DOWNLOAD.** Wrap `satcfdi` as `SatcfdiGateway` behind the ports (§5, verify API, pin version). `CredentialVault` + `DownloadCursor` + `IncrementalDownloadPolicy`; independent CFDI + Metadata jobs; immutable `source/` + manifests + UUID/hash conflict rule. **Ledger schema:** `download_jobs`, `download_cursors`, source records. Integration test = full chain (auth→request→verify→download→ZIP→SHA-256→manifest→extracted XML), against a *FIEL de pruebas*.
- **M2 — PROCESS/parse (`fiscal/`).** Onboard the contributor: store the **CSF as an immutable source artifact** (SHA-256), resolve the **`ContributorProfile`** from CSF + `.cer`, run the **static three-way RFC identity check** (CSF ↔ cert ↔ configured) at onboarding, and the **per-CFDI perspective match** (contributor RFC == Emisor if EMITIDO, == Receptor if RECIBIDO) during processing; mismatch or undetermined perspective ⇒ `NEEDS_REVIEW` (§7a). `lxml`→pydantic (`Decimal`, original currency, `pago20`/`nomina12`/retenciones-root, concept `ClaveProdServ`, tax list). **Validate each CFDI's `tfd:TimbreFiscalDigital`/`SelloSAT` + emisor `Sello` (§6a); failed/absent sello ⇒ `NEEDS_REVIEW`.** Append-only `fiscal_events` + projection. **Schema:** `contributor_profiles`, `documents`, `fiscal_events`, `metadata_snapshots`.
- **M3 — ACCOUNTING (`contabilidad/`).** Rules 4.1–4.15 → `ProposedJournalEntry` → `PostingEligibilityValidator`. `posting_snapshot.source_hash` references the **extracted-XML SHA-256** (the exact bytes parsed into the fiscal model — the ZIP hash lives on the source manifest, not the entry). **Schema:** `journal_entries`, `journal_lines` (immutable), `posting_snapshot`, `review_flags`.
- **M4 — EXCEL (`export/`).** Read-only projections → 9 sheets (`CFDIs_Raw, Asientos, Auxiliar_Clientes, Auxiliar_Proveedores, Resumen_IVA, Catalogo_Sin_Asiento, Requiere_Revision, Reconciliacion, Politicas_Aplicadas`); only `POSTED` feeds sub-ledgers/IVA. Output `/output/<Cliente>_<AAAAMM>.xlsx` (same `<Cliente>_<AAAAMM>` convention as the DIOT file).
- **M5 — DIOT (`diot/`).** Per §9.
- **M6 — CLI + Settings.** The two flows + `ConfigEditor` + logging. `ai/` stub anytime.
  *M2–M5 are offline and fully testable on sample XMLs, independent of M1.*

## 12. Testing
**Test tiers (SAT stays outside CI): (1) offline unit + `SatGateway` contract tests** (fakes, golden XML) — run in CI; **(2) offline integration** — the full local chain on fixture packages (extract→parse→account→export/DIOT), no network — run in CI; **(3) opt-in live SAT test** — `@pytest.mark.integration`, env-gated on a *FIEL de pruebas*, **never in CI**, run manually. Mock only at boundaries (SAT gateway, HTTP, disk). **Adapter contract tests:** one `SatGateway` contract suite run against `FakeSatGateway`, `SatcfdiGateway`, and any future impl (proves swappability; both conform to the same typed port). Crypto: assert round-trip+structure, never a raw signature. **SAT golden XML fixtures** (v1.5): `auth/solicitud_emitidos/solicitud_recibidos/verification/download`. **Accounting:** golden entries per fixture (all types, PUE→ASSUMED_PUE, discount net & contra, exento/IEPS, multi-doc REP, partial payment, FX, credit note `01` multi-UUID, substitution `04`); a **temporal cancellation fixture** (T0 Vigente→POSTED, T1 Cancelado→4.15 NEEDS_REVIEW); a **"posted entry never mutates"** test (original identity/hash unchanged; new compensating entry created); `hypothesis` invariants (balance; no `POSTED` from `Cancelado`-at-posting; `Σ ImpPagado == Pago.Monto`; `ImpSaldoAnt−ImpPagado==ImpSaldoInsoluto`; conservation). **Mutation tests** corrupt: Total/Subtotal/IVA/TipoCambio/UUID/ImpPagado **and** metadata status/cancellation date/folio_sustitución/currency/tax factor/tax rate/payment relation ⇒ reject or `NEEDS_REVIEW`, never silent posting. Assert no `float` in fiscal/accounting amounts. Keep unit tests offline/deterministic (`freezegun`, fakes). **DoD:** covered · `make check` green · coverage not decreased · typed+docstringed · no secret touched · boundaries respected · small conventional-commit PR.

## 13. Git/CI & Do/Don't
Trunk-based; Conventional Commits. CI runs lint+types+unit on 3.11/3.12 against the **locked** `satcfdi`, and **never calls SAT**. `.gitignore` blocks `*.cer *.key *.pem *.pfx *.zip .env`.
**Do:** test deterministic behavior first · keep boundaries · Decimal via `MoneyPolicy` · uncertainty → `NEEDS_REVIEW` via the validator · resolve FIEL password only via `CredentialVault` · keep `satcfdi` inside `infrastructure` (pinned) · treat DIOT/SAT/overlap layouts as versioned profiles · manifest every package · ask the human when unspecified.
**Don't:** import `satcfdi`/SOAP/XML/`requests`/`sqlite3`/`sleep`/`now`/`openpyxl` into domain/application/`contabilidad` · copy the §5 pseudocode verbatim · let a rule self-assign `POSTED` · auto-post a cancellation reversal · mutate a posted entry · silently replace a conflicting XML · classify a perspective mismatch as `SKIPPED` · use `impuestos_dr[0]` · call Banxico from accounting · **disable TLS verification** (§6a) · **extract untrusted ZIPs without size/entry/path-traversal limits** (§6a) · imply the SAT provides a package checksum · invent unspecified rules · leak secrets in `repr`/logs.

## 14. References
`satcfdi` (pinned adapter lib) · **phpcfdi/sat-ws-descarga-masiva** (envelope/v1.5 reference) · `cfdiclient` (alt) · SAT PDFs in `/docs`. For accounting semantics this file is authoritative; for unclear **fiscal** rules, ask the human.

> **README scope boundary:** This tool does not compute a complete tax return. It derives fiscal/accounting movements and a DIOT draft from the CFDI/payment information available to it. Bank movements, opening balances, non-CFDI transactions and tax adjustments are incorporated separately. The DIOT file is a draft for upload/signature on the SAT platform.