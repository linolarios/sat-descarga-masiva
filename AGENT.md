# AGENT.md — SAT-CFDI Accounting Tool (single source of truth · v1.1)

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

**Two truths:** SQLite is the application's *transactional accounting + processing-state store*; the **immutable source artifacts remain the authoritative evidence** for downloaded documents. `documents` holds immutable identity/current metadata; **`fiscal_events` is an append-only history**, and current fiscal state = `projection(fiscal_events)` (a cache may be derived but is never the authority). *This is an append-only event history, not full event-sourcing/replay — describe it that way.*

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

## 7. CLI — two flows (Download, then Process)
The user pre-creates one `/data/<cliente>/` per client with its `.cer`+`.key`. **Download** is separated because the SAT WS is the one unreliable external step. **Process requires an accounting period `YYYY-MM`** (don't let the agent invent period semantics). Each flow is success/fail **per client**; the CLI only invokes use cases (all iteration/logic lives in `ExecuteDownloadUseCase`/`ExecuteProcessUseCase`).

| Menu | Action |
|---|---|
| 1 | **Download** — for every folder with `.cer` **and** `.key`, fetch its CFDI (retry on WS failure) |
| 2 | **Process** — extract → parse → accounting → Excel → DIOT, for a period `YYYY-MM` |
| 3 | **Settings** (`ConfigEditor`) · 4 | **Exit** |

**Download flow:** eligible = both `.cer`+`.key` (missing ⇒ per-client error, others continue). Password via `CredentialVault`; run the **independent** CFDI + Metadata jobs; `source/` stores raw ZIP + manifest; advance `DownloadCursor`. **Retry transient** (timeout/5xx/app-`404`) with backoff; `5011`→wait, `5003`→partition. **Fatal, no retry:** `300`–`305`, `5005`. **No CFDI found** (`5004`/0 packages) ⇒ per-client empty/error (check period/RFC), not an infinite retry. Per-client isolation; resumable via `download_jobs`.

**Process flow (deterministic, needs no FIEL):** empty/absent extracted set ⇒ per-client error. Stages: `source` extract/classify (dedup per §6) → `fiscal` → `contabilidad` → `export` → `diot` (if `files_diot`). **Precise failure semantics:** a stage is atomic at its DB-transaction boundary; **individual malformed documents are quarantined and do not abort other documents**; a **stage (infrastructure) failure prevents later stages** for that client. Parse result is `ParseOutcome ∈ {PARSED, PARTIAL, FAILED}` (FAILED = nothing parsed ⇒ client error; PARTIAL still proceeds with a quarantine count). `NEEDS_REVIEW` is **not** a failure. **No cross-stage rollback.** Both flows log to `/logs/<run-timestamp>.log` and persist `PipelineRun{run_id, flow, client_rfc, period, status, failed_stage, per_stage{counts,message}}`.

## 8. Accounting engine + PostingEligibility (`contabilidad/`)

**A rule proposes; the validator decides.** Rules emit a `ProposedJournalEntry`; only **`PostingEligibilityValidator`** may assign `POSTED`. `POSTED` requires **all** of: source state eligible (vigente) at posting time · all required source fields present · deterministic account mapping · deterministic FX valuation · valid `Decimal` amounts · balanced `Debe==Haber` · no unresolved review flags · supported rule · `source_uuid`+`source_hash` present · `rule_version`/`policy_version`/`mapping_version` recorded. **Any failure ⇒ `NEEDS_REVIEW`.** Balance is checked twice (rule output *and* before ledger commit — defense in depth).

**`SKIPPED` vs `NEEDS_REVIEW`:** `SKIPPED` = deliberately outside accounting scope, **no human decision needed** (e.g. `T`, `N`/`R` recibidas, known informational). `NEEDS_REVIEW` = system **cannot safely determine** treatment (unmapped account, ambiguous FX, cancelled-posted, missing REP original, AI anomaly, perspective undetermined).

**Perspective router:** `EMITIDO` if `client_rfc==emisor`, `RECIBIDO` if `==receptor`. **Perspective undetermined ⇒ `NEEDS_REVIEW`** (or explicit `INVALID_SOURCE`) — **never silent `SKIPPED`**. Normalize RFCs. **Generic RFCs (`XAXX010101000`/`XEXX010101000`) are never by themselves sufficient to infer perspective.** Retenciones read emisor/receptor from the `retenciones:` root.

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
- **Idempotency = `PostingFingerprint`** over `{source_uuid, rule_id, rule_version, line_key, mapping_version}` (not just `(uuid, rule_id, line)`). **Historical `POSTED` entries are immutable**; a new `mapping_version` applies only to future processing or an explicit re-post/rebuild workflow — never silent rebuild.
- **Classification:** `ClaveProdServ → AccountingCategory → Account` (unmapped ⇒ `NEEDS_REVIEW`; capital goods ⇒ fixed-asset review). Egreso: `CfdiRelacionados/@TipoRelacion` (`01`/`07`/`04`, possibly multiple UUIDs). **Taxes:** Traslados+Retenciones; `TipoFactor∈{Tasa,Cuota,Exento}`; 0%≠exento; IVA=`002`.
- **Reconciliation (`recon/`):** `AR_close = AR_open + invoices − collections − credit_notes ± adjustments` (needs imported opening balances; else movements-only); IVA conservation from the event history; REP fiscal `Σ ImpPagado == Pago.Monto` (payment ccy) vs accounting MXN (separate). **Opening balances come from an `OpeningBalanceProvider` (external/manual input) — never derived from CFDI.**

## 9. DIOT — `diot/`
Monthly per-vendor report of IVA pagado/acreditable/retenido/trasladado, **for a period `YYYY-MM`**. As of 2025 it is filed via the SAT **online platform** with a bulk-load `.txt` (pipe-delimited) — *confirm the current channel/structure against the SAT instructivo; don't hardcode it.* **The tool generates + pre-validates the file; the human uploads/signs with e.firma.** Per-client `files_diot` flag (support "sin operaciones"). Data from the ledger's RECIBIDO projection — not re-parsed XML. Layout is a **versioned `DiotLayoutProfile{ version, effective_from, effective_to, field_count, encoding, delimiter, fields[], source_document }`**. Output `/output/<Cliente>_DIOT_<AAAAMM>.txt` (+ optional Excel mirror). Treat as a **draft/reconciliation** artifact (the SAT platform also pre-fills from CFDIs).

## 10. AI seam & Settings
**`ai/`** (optional): `AnomalyDetector.review(entries, ledger_view) -> list[ReviewFlag]`. AI may **only** move an entry `POSTED → NEEDS_REVIEW`. AI **cannot** downgrade to `SKIPPED`, modify amounts, change accounts, create journal lines, or change fiscal state. Ship a `NullAnomalyDetector`. **Settings — `ConfigEditor`:** `rich` forms over YAML (`ClaveProdServ→Category→Account`, `FormaPago→banco`, retención accounts, discount/FX/PUE/payroll switches, `files_diot`, active `DiotLayoutProfile`/`SignatureProfile`/`IncrementalDownloadPolicy` versions). Validated on save; `mapping_version` bumped for traceability.

## 11. Build order (with incremental schema)
- **M1 — DOWNLOAD.** Wrap `satcfdi` as `SatcfdiGateway` behind the ports (§5, verify API, pin version). `CredentialVault` + `DownloadCursor` + `IncrementalDownloadPolicy`; independent CFDI + Metadata jobs; immutable `source/` + manifests + UUID/hash conflict rule. **Ledger schema:** `download_jobs`, `download_cursors`, source records. Integration test = full chain (auth→request→verify→download→ZIP→SHA-256→manifest→extracted XML), against a *FIEL de pruebas*.
- **M2 — PROCESS/parse (`fiscal/`).** `lxml`→pydantic (`Decimal`, original currency, `pago20`/`nomina12`/retenciones-root, concept `ClaveProdServ`, tax list). Append-only `fiscal_events` + projection. **Schema:** `documents`, `fiscal_events`, `metadata_snapshots`.
- **M3 — ACCOUNTING (`contabilidad/`).** Rules 4.1–4.15 → `ProposedJournalEntry` → `PostingEligibilityValidator`. **Schema:** `journal_entries`, `journal_lines` (immutable), `posting_snapshot`.
- **M4 — EXCEL (`export/`).** Read-only projections → 9 sheets (`CFDIs_Raw, Asientos, Auxiliar_Clientes, Auxiliar_Proveedores, Resumen_IVA, Catalogo_Sin_Asiento, Requiere_Revision, Reconciliacion, Politicas_Aplicadas`); only `POSTED` feeds sub-ledgers/IVA.
- **M5 — DIOT (`diot/`).** Per §9.
- **M6 — CLI + Settings.** The two flows + `ConfigEditor` + logging. `ai/` stub anytime.
  *M2–M5 are offline and fully testable on sample XMLs, independent of M1.*

## 12. Testing
Mock only at boundaries (SAT gateway, HTTP, disk). **Adapter contract tests:** one `SatGateway` contract suite run against `FakeSatGateway`, `SatcfdiGateway`, and any future impl (proves swappability). Crypto: assert round-trip+structure, never a raw signature. **SAT golden XML fixtures** (v1.5): `auth/solicitud_emitidos/solicitud_recibidos/verification/download` to protect the adapter contract. **Accounting:** golden entries per fixture (all types, PUE→ASSUMED_PUE, discount net & contra, exento/IEPS, multi-doc REP, partial payment, FX, credit note `01` multi-UUID, substitution `04`); a **temporal cancellation fixture** (T0 Vigente→POSTED, T1 Cancelado→4.15 NEEDS_REVIEW); a **"posted entry never mutates"** test (original identity/hash unchanged; new compensating entry created); `hypothesis` invariants (balance; no `POSTED` from `Cancelado`-at-posting; `Σ ImpPagado == Pago.Monto`; `ImpSaldoAnt−ImpPagado==ImpSaldoInsoluto`; conservation). **Mutation tests** corrupt: Total/Subtotal/IVA/TipoCambio/UUID/ImpPagado **and** metadata status/cancellation date/folio_sustitución/currency/tax factor/tax rate/payment relation ⇒ reject or `NEEDS_REVIEW`, never silent posting. Assert no `float` in fiscal/accounting amounts. Keep unit tests offline/deterministic (`freezegun`, fakes). **DoD:** covered · `make check` green · coverage not decreased · typed+docstringed · no secret touched · boundaries respected · small conventional-commit PR.

## 13. Git/CI & Do/Don't
Trunk-based; Conventional Commits. CI runs lint+types+unit on 3.11/3.12 against the **locked** `satcfdi`, and **never calls SAT**. `.gitignore` blocks `*.cer *.key *.pem *.pfx *.zip .env`.
**Do:** test deterministic behavior first · keep boundaries · Decimal via `MoneyPolicy` · uncertainty → `NEEDS_REVIEW` via the validator · resolve FIEL password only via `CredentialVault` · keep `satcfdi` inside `infrastructure` (pinned) · treat DIOT/SAT/overlap layouts as versioned profiles · manifest every package · ask the human when unspecified.
**Don't:** import `satcfdi`/SOAP/XML/`requests`/`sqlite3`/`sleep`/`now`/`openpyxl` into domain/application/`contabilidad` · copy the §5 pseudocode verbatim · let a rule self-assign `POSTED` · auto-post a cancellation reversal · mutate a posted entry · silently replace a conflicting XML · classify a perspective mismatch as `SKIPPED` · use `impuestos_dr[0]` · call Banxico from accounting · invent unspecified rules · leak secrets in `repr`/logs.

## 14. References
`satcfdi` (pinned adapter lib) · **phpcfdi/sat-ws-descarga-masiva** (envelope/v1.5 reference) · `cfdiclient` (alt) · SAT PDFs in `/docs`. For accounting semantics this file is authoritative; for unclear **fiscal** rules, ask the human.

> **README scope boundary:** This tool does not compute a complete tax return. It derives fiscal/accounting movements and a DIOT draft from the CFDI/payment information available to it. Bank movements, opening balances, non-CFDI transactions and tax adjustments are incorporated separately. The DIOT file is a draft for upload/signature on the SAT platform.