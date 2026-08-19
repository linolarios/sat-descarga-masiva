# Architecture — `sat-descarga-masiva` v1.6

**Style:** Hexagonal (Ports & Adapters) + SOLID.
**Primary patterns:** Ports & Adapters · Facade · Application Service (Use Case) · State Machine · Strategy (backoff, partition, error classification) · Builder (SOAP/XML) · Factory (CFDI/Retenciones) · Specification (query validation) · Repository (resume/token) · Adapter (HTTP/SOAP) · Anti-Corruption Layer (SAT XML → domain) · Value Objects · Policy Objects.

A Python client for the SAT (Mexico) **Descarga Masiva** SOAP service — **Autenticación → Solicitud → Verificación → Descarga**, for CFDI and Retenciones. It is the **ingestion / `SatGateway` port** for a larger accounting tool and knows nothing about accounting.

> **Guiding restraint:** patterns solve concrete problems here (SOAP/XML complexity, async polling, SAT protocol variability, error handling, testability, resumability). We use interfaces at **architectural boundaries only** — not one interface per class.

---

## 1. Bounded context

This package's whole vocabulary is: authenticate → request → verify → download → return an **immutable package**. It must never contain `IVA`, `AR`, `AP`, `JournalEntry`, `POSTED`, etc. The accounting system consumes `Package` bytes; nothing here depends on it.

## 2. The dependency rule (enforced)

```
facade ──▶ application ──▶ domain
                ▲
        ports  │  (Protocols)
                │
        infrastructure ── implements ports
```

- `domain` depends on **nothing** (no `requests`, `lxml`, `cryptography`, SOAP, URLs).
- `application` depends on `domain` + its **ports**, never on `infrastructure`.
- `infrastructure` implements ports and is the **only** place SAT/SOAP/XML/HTTP/crypto live.
- **Rule (§58):** *no SAT protocol code outside `infrastructure.sat`* — no status-code literals, XML element names, `SOAPAction`, `requests.post`, `time.sleep`, or `datetime.now()` in `application`/`domain`.

```
                    ┌──────────────────────────────┐
                    │  facade.DescargaMasivaClient  │  (thin public API)
                    └───────────────┬───────────────┘
                                    ▼
                    ┌──────────────────────────────┐
                    │   application.use_cases        │  ExecuteDownloadUseCase, ...
                    │   application.ports (Protocols)│
                    │   application.policies         │  backoff, retry, partition, sat_status
                    └───────────────┬───────────────┘
                                    ▼
                    ┌──────────────────────────────┐
                    │            domain             │  value objects, enums, errors
                    └───────────────────────────────┘
                                    ▲  (adapters implement ports)
        ┌───────────────┬──────────┴────────┬────────────────┬───────────────┐
        ▼               ▼                   ▼                ▼               ▼
  SAT SOAP adapters  FIEL loader      HTTP adapter     Token/Request     Package sink
  (gateways/soap/                     (requests)        repositories      (fs/memory)
   xml/signing)
```

## 3. Package layout

```
src/sat_descarga_masiva/
├── domain/
│   ├── model/          value_objects (Rfc, RequestId, PackageId, DateRange), token, query, package, verification
│   ├── enums/          request_state, service_type, request_type, direction, document_status, sat_status_code
│   └── errors.py
├── application/
│   ├── ports/          authentication/request/verification/download gateways, http_client, clock,
│   │                   token_store, request_repository, package_sink, xml_signer, fiel_loader
│   ├── policies/       backoff, retry, partition, sat_status (classifier)
│   ├── specifications/ query specifications (Recibidos→VIGENTE, date range, historical limit)
│   └── use_cases/      authenticate, submit_request, verify_request, download_package, execute_download
├── infrastructure/
│   ├── sat/
│   │   ├── endpoints.py         frozen SatEndpoints + resolver (Factory)
│   │   ├── protocol.py          SatProtocolVersion, SoapAction registry
│   │   ├── gateways/            SOAP implementations of the application ports
│   │   ├── soap/                envelope/header builders, NamespaceRegistry
│   │   ├── xml/                 builders/ mappers/ parsers/  (ACL: bytes ↔ typed DTO)
│   │   └── signing/             XmlSigner + SignatureProfile + canonicalizer + key_info
│   ├── credentials/fiel.py      CryptographyFielLoader → FielIdentity (repr-safe)
│   ├── http/requests_client.py  RequestsHttpClient (implements HttpClient)
│   └── persistence/             in-memory + sqlite/filesystem stores
├── facade/client.py
└── config/settings.py
```

## 4. Domain model (value objects & enums)

Typed value objects replace stringly-typed params (§27):

```python
Rfc(value)  # validated
RequestId(value)  # UUID-validated
PackageId(value)
DateRange(start, end)  # start <= end enforced
AccessToken(value, created_at, expires_at)  # repr-safe; is_valid(now)
```

Enums are **stable protocol states**; SAT **codes are open** (a new code must not crash the client) — §16:

```python
class RequestState(IntEnum):  # SAT EstadoSolicitud
    ACCEPTED = 1
    PROCESSING = 2
    COMPLETED = 3
    ERROR = 4
    REJECTED = 5
    EXPIRED = 6


@dataclass(frozen=True)
class SatStatusCode:  # open value object, not a closed Enum
    value: str

    @property
    def classification(self) -> ErrorClassification: ...  # unknown → UNKNOWN
```

## 5. State machine & polling (§6, §7, §51)

Interpret SAT `EstadoSolicitud` into a **decision**, never scatter `if estado == 3`:

```
SAT state ─▶ StateInterpreter ─▶ Decision ∈ {POLL, DOWNLOAD, FAIL, RESTART}
```

Polling is a **Strategy** driven by a policy and a **`Clock`** (deterministic tests, no real sleep):

```python
@dataclass(frozen=True)
class PollingPolicy:
    initial_delay: timedelta
    max_delay: timedelta
    multiplier: float = 2.0
    timeout: timedelta = timedelta(hours=24)  # configurable
```

**Correction (§51):** `72 h` is **not** a hardcoded expiry constant. `EXPIRED` is simply SAT state `6`; how long to keep polling is `PollingPolicy.timeout`, chosen by the caller. There is no reliable fixed completion time.

## 6. Error handling — Result vs Exception (§35, §36)

- **Expected SAT protocol outcomes** (accepted/rejected/duplicate/expired) are **Result values**, not exceptions.
- **Technical failures** (transport, XML parse, signature, timeout, unexpected response) are a **typed exception hierarchy**: `SatClientError → {AuthenticationError, RequestValidationError, SoapError, XmlParseError, SignatureError, TransportError, TimeoutError, UnexpectedSatResponseError}`.

Two classification axes stay **separate** (§52):
- **SAT application code** → `ErrorClassification ∈ {OK, RETRY, PARTITION, QUOTA_WAIT, DUPLICATE, NOT_FOUND, AUTHENTICATION, FATAL, UNKNOWN}` (e.g., `5003→PARTITION`, `5011→QUOTA_WAIT`, `5005→DUPLICATE`).
- **HTTP transport status** → its own retry policy (`408/429/5xx→retry`, `400→fail`, `401→refresh-token-once`, `403→fail`, **`404→configuration/endpoint check, not auto-retry`**).

## 7. Retry / backoff / partition (§37–40)

Retry is a **policy around the gateway**, not baked into HTTP (POST isn't blindly repeatable):

```
use case ─▶ RetryPolicy ─▶ Gateway ─▶ HttpClient
```

- `5003 → QueryPartitionStrategy.partition(query)` — the *algorithm* (binary vs adaptive) is an implementation detail, not a domain contract (§40).
- `5011 → QuotaWaitPolicy` — wait for the next permitted window; shrinking the range does not help.

## 8. Query validation — Specification (§18, §50)

Composable specifications validate a `DownloadQuery` before any SOAP is built:

```
DateRangeSpec · HistoricalLimitSpec · DownloadTypeSpec · DocumentStatusSpec · DirectionSpec
```

**Invariant (§50, per the phpcfdi reference — confirm before prod):**
```
service=CFDI ∧ direction=RECIBIDOS ∧ request_type=XML  ⇒  document_status = VIGENTE
```
This belongs in a Specification, **not** buried in the SOAP adapter.

## 9. SOAP / XML architecture — Builder + ACL (§11–14, §24–26)

Pipeline, with XML construction separated from signing:

```
DownloadQuery ─▶ XmlRequestBuilder ─▶ unsigned XML ─▶ canonicalize ─▶ Signature ─▶ signed XML ─▶ SOAP envelope ─▶ HTTP
```

- **Builders** construct XML (operation-specific: Auth/Request/Verify/Download). Domain objects stay XML-unaware.
- **Anti-Corruption Layer:** responses go `bytes ─▶ Parser ─▶ SAT DTO ─▶ Mapper ─▶ domain`. **No `lxml`/`Element`/namespace dicts ever cross into `application`/`domain`** (§25).
- **`NamespaceRegistry`** and a **`SoapAction`/`OperationRegistry`** replace magic strings (§14, §53).

## 10. XML signing — Profile as a Strategy (§30–32, §55)

Signing is polymorphic and profile-driven:

```python
@dataclass(frozen=True)
class SignatureProfile:
    signature_algorithm: str  # xmldsig#rsa-sha1
    digest_algorithm: str  # xmldsig#sha1
    canonicalization_algorithm: str  # xml-exc-c14n#
    transforms: tuple[str, ...]


class XmlSigner(Protocol):
    def sign(self, document: XmlDocument, profile: SignatureProfile) -> SignedXml: ...
```

**Correction (§32):** we do **not** say "signature over exact bytes." The signature depends on the exact XML structure, namespace declarations, reference URI, transforms and **canonicalization** required by the SAT profile — so XML construction must be **deterministic**. Tests verify *canonical output + signature validation*, not raw byte equality. If SAT moves `rsa-sha1 → rsa-sha256`, you change a **profile**, not signing code.

## 11. SAT protocol version (§54)

Endpoints, SOAPActions, and signature profile depend on a version object, because SAT has already changed this service once:

```python
SatProtocol.V1_5   →   EndpointResolver · SoapActionResolver · SignatureProfileResolver
```

## 12. Credentials & security (§33–34, §56)

- Loading is a port: `FielLoader.load(source) -> FielIdentity`; the `cryptography` impl is infrastructure (tests use a fake).
- `FielIdentity` is **repr-safe** (`repr=False`) and exposes `rfc`, `certificate` (public), `cer_base64`, and `sign(data)` — **the private key never leaves the object**.
- **Never** put private key, password, token, cert bytes, or the `Authorization` header into `repr`, `str`, logs, exceptions, or telemetry.

## 13. Persistence & resumability (§10, §41–43)

Ports so the client is storage-agnostic:
- `TokenStore` (InMemory / SQLite) — token acquisition/refresh via an `AccessTokenProvider`.
- `RequestRepository` — persist `IdSolicitud` so a killed run resumes (InMemory / SQLite).
- `PackageSink` — the SAT client **returns `Package` bytes**; it does not write disk. Storage (`FileSystem` / `Memory` / `S3`) is chosen by the caller and feeds the accounting tool's immutable Source layer (§41–42).

## 14. Endpoints — Factory (§20–22)

Immutable, resolved by service family; never in the domain:

```python
@dataclass(frozen=True)
class SatEndpoints:
    authentication: str
    request: str
    verification: str
    download: str


CFDI = SatEndpoints(...)
RETENCIONES = SatEndpoints(...)


def endpoints_for(service: ServiceType) -> SatEndpoints: ...
```

Production hosts (v1.5, verify against the phpcfdi reference if signatures fail): `cfdidescargamasivasolicitud.clouda.sat.gob.mx` (+ `cfdidescargamasiva…` for download); Retenciones on `retendescargamasiva…` hosts.

## 15. Public API (§57)

Tiny surface; everything else is internal:

```python
client = DescargaMasivaClient(config)
result = client.execute(query)  # full workflow
# or explicit:
rid = client.submit(query)
v = client.verify(rid)
pkg = client.download(pkg_id)
```

## 16. Testing strategy

The workflow is testable with **no network, sleep, FIEL, SAT, or filesystem** — inject fakes for every port (`FakeHttpClient`, `FakeClock`, `ImmediateBackoff`, `InMemoryTokenStore/RequestRepository`, `MemoryPackageSink`, `FakeFielLoader`). Golden fixtures (e.g., the spec's `VerificaSolicitudDescarga` response) drive parser/mapper tests; signing tests assert canonical output + verification round-trip.

## 17. Anti-overengineering guardrails (§29, §45)

Interfaces belong at boundaries that actually get substituted: `HttpClient, *Gateway, TokenStore, RequestRepository, PackageSink, Clock, BackoffStrategy, QueryPartitionStrategy, XmlSigner, FielLoader`. **Do not** wrap simple immutable values (`RequestId`, `Rfc`, `PackageId`) in factories/builders. SOLID here means *one reason to change + dependencies point at abstractions where substitution matters* — not one interface per class.
