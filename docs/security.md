# Security, privacy and responsible use

AEGIS is a **portfolio prototype**. It has **not** had a security review and must not be exposed to the internet or loaded with real customer data in its current state.

## Implemented safeguards

| Area | Control | Where |
|---|---|---|
| Configuration | All settings from environment variables; `.env.example` has placeholders only; no credentials in code | `core/config.py` |
| Input validation | Pydantic models for bodies; typed/bounded query parameters (page size ≤ 500, hops ≤ 6, amounts ≥ 0, regex-restricted enums); sort columns whitelisted | `api/` |
| SQL injection | SQLAlchemy ORM / bound parameters everywhere; a test sends a classic injection string | `tests/test_api.py` |
| Uploads | `.csv` only; size limit (`AEGIS_MAX_UPLOAD_MB`, default 50) enforced while streaming; parsed with pandas as strings and validated – never executed, no formula evaluation; mapping must be a JSON object | `api/data_routes.py` |
| Spreadsheet injection | Cells starting with `= + - @` are prefixed with `'` in downloadable error reports | `ingestion/pipeline.py` |
| Query limits | Subgraph node/edge caps, per-node neighbour caps, trace timeouts and path caps, cycle-search time budget and result cap | `graph/`, `api/` |
| Errors | Unhandled exceptions return a generic 500 without internals; details are logged server-side | `main.py` |
| Auth (development only) | Optional shared `X-API-Key` (constant-time compare) on all routes except `/api/health`; analyst identity is an unauthenticated `X-Analyst` header recorded in the audit trail | `api/common.py` |
| Auditability | Every investigation change is stored with actor, time, field, old and new values; closed cases are read-only | `investigations/service.py` |
| Label quarantine | Ground-truth tables are read only by training/evaluation code; test asserts graph/risk/feature/API code never touches them | `tests/test_graph.py` |
| Data minimisation | Synthetic data only; no names, addresses or identifiers beyond opaque account IDs | generator |
| Containers | Non-root user, health checks, secrets via environment (Dockerfiles are provided but **were not built or run** in the authoring environment – Docker was unavailable) | `docker/` |

## Not implemented / assumptions

* No real authentication, authorisation, roles, rate limiting, TLS termination or CSRF/CORS hardening beyond a configurable origin list. The analyst header can be forged.
* The API key is a single shared secret, suitable only for local use.
* Uploaded files and the SQLite database are stored unencrypted on local disk.
* PostgreSQL support is by SQLAlchemy portability and has not been exercised here.
* No external AI service is called. Any future LLM summarisation must be opt-in, use retrieved evidence only and never send financial records without explicit configuration and user authorisation.

## Retention and deletion

* Datasets, derived alerts, scores, investigations and audit rows persist until deleted. `DELETE /api/datasets/{id}` removes the dataset and all derived rows (alerts, scores, labels, investigations, import reports). Rejected-row CSVs in `data/reports/` and cached features in `artifacts/features/` must be removed by deleting those directories; model artifacts in `artifacts/models/` are not dataset-specific deletions.
* Suggested retention for a real deployment is a policy decision outside this prototype.

## Responsible-use statement

Alerts are *investigative leads*. Wording throughout the UI distinguishes an **unusual transaction**, a **suspicious pattern**, a **connected network (community)**, a **model-generated risk score** and an **analyst disposition**. None of these is a finding of wrongdoing; dispositions are workflow outcomes, not legal determinations. Risk bands are operational categories, not regulatory classifications. Synthetic results must never be presented as real-world detections.
