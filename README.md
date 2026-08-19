# sat-descarga-masiva

Hexagonal Python client for the SAT (Mexico) **Descarga Masiva** SOAP service:
**Autenticación → Solicitud → Verificación → Descarga** (CFDI + Retenciones).

> ⚠️ Handles your **e.firma (FIEL)** — never commit a `.cer`/`.key`/password (see `.gitignore`, `AGENT.md` §6).

## Quickstart
```bash
make setup && source .venv/bin/activate
make hooks
make check     # ruff + mypy(strict) + unit tests -> green
```

- **`AGENT.md`** — TDD-first operating guide + build order.
- **`ARCHITECTURE.md`** — hexagonal design, ports, policies, SAT flow, error taxonomy.
- **`docs/`** — drop the SAT PDFs here.

Layers: `domain ← application (ports/policies/use_cases) ← infrastructure`, thin `facade`.
Dependency rule: no SAT/SOAP/XML/HTTP/crypto outside `infrastructure`.
