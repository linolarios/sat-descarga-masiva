# AGENT.md — Building `sat-descarga-masiva` (v1.6, hexagonal)

You are an AI coding agent working **with a human** on a Python client for the SAT (Mexico) **Descarga Masiva** SOAP service — **Autenticación → Solicitud → Verificación → Descarga** (CFDI + Retenciones). Read this fully, then `ARCHITECTURE.md`.

**Mission:** a small, well-typed, thoroughly tested package that is the `SatGateway` ingestion port for a larger accounting tool. It knows **nothing** about accounting.

---

## 0. Golden rules

1. **TDD is mandatory.** No production code without a failing test. Red → Green → Refactor. (§4)
2. **The dependency rule is law.** `facade → application → domain`; `infrastructure` implements ports. `domain` imports no `requests`/`lxml`/`cryptography`/SOAP/URLs.
3. **No SAT protocol code outside `infrastructure.sat`.** No status-code literals, XML element names, `SOAPAction`, `requests.post`, `time.sleep`, or `datetime.now()` in `application`/`domain`. If you need "now" or a delay, inject `Clock`/`BackoffStrategy`.
4. **Never touch real secrets.** No `.cer`/`.key`/password committed, logged, or in `repr`/exceptions. Unit tests use a generated self-signed FIEL; integration uses a SAT *FIEL de pruebas* from env.
5. **Never call the live SAT in unit tests or CI.** Fixtures only. Live calls behind `@pytest.mark.integration`, opt-in.
6. **Signatures are canonicalization-sensitive, not "byte-exact."** Build XML deterministically; test canonical output + signature verification, not raw byte equality. When unsure, diff against **phpcfdi/sat-ws-descarga-masiva**.
7. **Interfaces at boundaries only.** Don't wrap `Rfc`/`RequestId` in factories. Patterns must solve a concrete problem (see `ARCHITECTURE.md §17`).
8. **Ask the human on fiscal/legal/ambiguous calls.** Small PRs; leave clear TODOs.

---

## 1. Stack & commands

Python 3.11+. `uv` (or `venv`+`pip`). Runtime: `cryptography`, `lxml`, `signxml`, `requests`, `pydantic`. Dev: `pytest`, `pytest-cov`, `pytest-mock`, `responses`, `freezegun`, `hypothesis`, `ruff`, `mypy`, `pre-commit`.

```bash
make setup      # venv + install
make check      # ruff + ruff format --check + mypy(strict, src) + unit tests  ← commit gate
make test       # unit only (no network)
make itest      # integration (needs FIEL de pruebas in env)
make hooks      # pre-commit install
```

---

## 2. Build order (bottom-up; each = one small green PR)

**Domain (no deps):**
1. `domain/model/value_objects.py` — `Rfc`, `RequestId`, `PackageId`, `DateRange`. *(seeded & green)*
2. `domain/enums/*` — `RequestState`, `ServiceType`, `RequestType`, `Direction`, `DocumentStatus`, `SatStatusCode`+`ErrorClassification`. *(seeded & green)*
3. `domain/model/token.py` — `AccessToken` (repr-safe). *(seeded & green)*
4. `domain/errors.py`. *(seeded)*

**Application (domain + ports):**
5. `application/policies/backoff.py` — `PollingPolicy` + `ExponentialBackoff` (+`ImmediateBackoff` for tests). *(seeded & green)*
6. `application/policies/sat_status.py` — classify code → `ErrorClassification`. *(seeded & green)*
7. `application/policies/partition.py` — `QueryPartitionStrategy` (binary date split).
8. `application/specifications/*` — query specs incl. **Recibidos-XML ⇒ VIGENTE** invariant.
9. `application/ports/*` — Protocols (seeded). Keep them small (ISP).
10. `application/use_cases/execute_download.py` — orchestrates auth→submit→persist→poll→download via ports. *(xfail test seeded — make it green with fakes, no network/sleep.)*

**Infrastructure (implements ports):**
11. `infrastructure/credentials/fiel.py` — `CryptographyFielLoader → FielIdentity` (`sign()`, `rfc`, repr-safe). *(seeded & green)*
12. `infrastructure/sat/signing/` — `XmlSigner` + `SignatureProfile` (`SAT_V15` seeded). Test = sign→verify round-trip + canonical form.
13. `infrastructure/sat/soap/` + `xml/builders` — envelope + request builders (deterministic).
14. `infrastructure/sat/xml/parsers` + `mappers` — ACL: bytes → DTO → domain. **Fixture ready:** `tests/fixtures/verifica_response.xml` → assert `COMPLETED`, `5000`, 6 packages. *(xfail seeded.)*
15. `infrastructure/http/requests_client.py` — `HttpClient` adapter; map HTTP status per policy (**404 ≠ auto-retry**).
16. `infrastructure/sat/gateways/*` — wire builder+signer+http+parser into the four gateways.
17. `infrastructure/persistence/*` — InMemory + SQLite `TokenStore`/`RequestRepository`; `PackageSink`.
18. `facade/client.py` — thin API delegating to `ExecuteDownloadUseCase`.

---

## 3. TDD workflow

RED (smallest failing test naming the behavior, spec values inline) → GREEN (minimum code) → REFACTOR (green) → commit. Test **behavior**; mock only at ports. For crypto, assert **round-trip + structure**, never a hard-coded signature string. Keep unit tests offline/deterministic (`freezegun`, `FakeClock`, `ImmediateBackoff`). A bug fix starts with a failing test.

**Definition of Done:** behavior covered; `make check` green; coverage not decreased; public API typed + docstringed; no secret touched; the dependency rule respected; small conventional-commit PR.

---

## 4. Signing (the part that breaks)

Two shapes: **Auth** = WS-Security header (`Timestamp` + `BinarySecurityToken` + enveloped `Signature` over the Timestamp); **Solicitud/Verificación/Descarga** = enveloped `Signature` over the body (`URI=""`, enveloped-signature transform). Both use the `SignatureProfile` (`rsa-sha1`, `sha1`, `exc-c14n`). If auth returns `302`/`305`, canonicalization/order is off — diff against phpcfdi before debugging anything else. **v1.5** changed two auth `Id`/`URI` prefixes — confirm current values.

---

## 5. Error & policy semantics

- Expected SAT outcomes → **Result values**; technical failures → **typed exceptions** (`ARCHITECTURE.md §6`).
- Classify **SAT code** and **HTTP status separately**: `5003→PARTITION`, `5011→QUOTA_WAIT`, `5005→DUPLICATE`; HTTP `408/429/5xx→retry`, `401→refresh-once`, `403→fail`, `404→config check` (not blind retry).
- Retry/partition/backoff are **policies around gateways**, never inside `HttpClient`.

---

## 6. Security

`.gitignore` blocks `*.cer *.key *.pem *.pfx *.zip .env`. Password from env/`keyring`. `FielIdentity` keeps the private key internal and is `repr`-safe. If you ever see a real credential in the tree, **stop and tell the human**. Unit FIEL is generated in `conftest.py` (test RFC in OID 2.5.4.45). Integration reads `SAT_FIEL_CER/KEY/PASSWORD` (a *FIEL de pruebas*).

---

## 7. References (study, don't guess)

**phpcfdi/sat-ws-descarga-masiva** (canonical envelopes + v1.5 + validation invariants) · **cfdiclient** (working Python) · **satcfdi** (semantics) · the SAT PDFs in `/docs`. It's acceptable to wrap `cfdiclient` behind a gateway port to reach end-to-end green, then replace it with our own impl behind the *same* port — tests don't change.

---

## 8. Git / CI

Trunk-based, Conventional Commits (`feat/fix/test/refactor/docs/chore`). Every PR: green `make check`, tests included, no secrets, one milestone. CI runs lint+types+unit on 3.11/3.12 and **never calls SAT**.

## 9. Do / Don't

**Do:** test first · keep the dependency rule · inject `Clock`/`BackoffStrategy` · map SAT vs HTTP status separately · use spec fixtures · profiles for signing · ask the human on fiscal calls.
**Don't:** put protocol literals/`sleep`/`now`/`requests` in application or domain · assert raw signatures · call live SAT in CI · create an interface per class · hardcode `72h` · auto-retry HTTP 404 · leak secrets in `repr`/logs.
