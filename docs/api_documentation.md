# API documentation

Interactive OpenAPI docs: **`/docs`** (Swagger UI) and **`/openapi.json`** when the backend is running (`http://localhost:8000/docs`).
Base path `/api`. Optional header `X-API-Key` (when `AEGIS_API_KEY` is set) and `X-Analyst` (recorded in audit trail). Authentication here is a **local-development control only**. Errors are JSON `{ "detail": "…" }` with 401/404/409/413/415/422/500 as appropriate.

Pagination: `page` (≥1) and `page_size` (1–500, default 50) → `{items, total, page, page_size}`.
All `dataset_id` parameters default to the most recent dataset. Ground-truth labels are never returned by any endpoint.

| Area | Method & path | Notes |
|---|---|---|
| System | `GET /health` | database check + disclaimer |
| Datasets | `GET /datasets` · `GET /datasets/{id}/stats` · `DELETE /datasets/{id}` · `POST /datasets/synthetic?quick=&seed=` (job) | delete removes all derived rows |
| Imports | `POST /imports/inspect` (schema + suggested mapping) · `POST /imports` (multipart: `file`, `name`, `mapping` JSON, optional `accounts_file`) · `GET /imports` · `GET /imports/{id}` · `GET /imports/{id}/errors` (CSV) | 413 over size limit, 415 non-CSV |
| Transactions | `GET /transactions` (`q`, `account`, `direction`, `min_amount`, `max_amount`, `transaction_type`, `date_from`, `date_to`, `sort`, `order`) · `GET /transactions/{id}` | sort whitelist |
| Accounts | `GET /accounts?q=` · `GET /accounts/{id}` · `GET /accounts/{id}/transactions` | detail includes counterparties, alerts, investigations |
| Graph | `GET /graph/subgraph` (`account_id`, `hops≤3`, `min_amount`, `date_from/to`, `max_nodes≤400`) · `GET /graph/edge` · `GET /graph/trace` (`direction`, `max_hops≤6`, `min_amount`, `max_gap_hours`, `max_paths≤200`, `min_flow_fraction`) · `GET /graph/cycles` · `GET /graph/communities` · `GET /graph/node-features` | trace results are **approximate flow tracing**; subgraph reports `truncated` |
| Alerts | `GET /alerts` (`band`, `alert_type`, `status`, `entity`, `min_score`, `sort`, `order`) · `GET /alerts/{id}` · `GET /alerts/{id}/explanation` | detail includes evidence transactions, risk breakdown, exposure, SHAP factors, limitations |
| Investigations | `POST /investigations` · `GET /investigations` · `GET /investigations/{id}` · `PATCH /investigations/{id}` · `POST /investigations/{id}/notes` · `POST /investigations/{id}/alerts` · `POST /investigations/{id}/transactions` · `GET /investigations/{id}/history` · `GET /investigations/meta` | state machine New→In Review→Escalated/Resolved→Closed; 409 on invalid transitions |
| Models | `GET /models` · `GET /models/active` · `GET /models/{id}/metrics` · `POST /models/train` (job) · `POST /models/{id}/activate` | promotion is manual |
| Pipeline & jobs | `POST /pipeline/run` (job) · `GET /jobs` · `GET /jobs/{id}` | jobs run in-process |
| Monitoring | `GET /monitoring/summary` | runs, latency, missing features, score histogram, PSI drift, backlog, turnaround |
| Dashboard | `GET /overview` · `GET /insights` | KPI definitions are returned with the numbers |
| Evaluation | `GET /evaluation/business-impact` · `POST /evaluation/business-impact` (job) | aggregate synthetic-benchmark results only |
| Streaming | `POST /streaming/simulate?n_events=&batch_size=&failure_rate=` | in-process simulation with measured throughput/latency and hardware description |

## Example

```bash
curl "http://localhost:8000/api/alerts?band=Critical&page_size=3"
curl "http://localhost:8000/api/graph/trace?account_id=AC000212&direction=forward&max_hops=4&min_flow_fraction=0.5"
curl -X POST http://localhost:8000/api/investigations -H "Content-Type: application/json" -H "X-Analyst: dana" -d '{"alert_id":"AL-E91701AD17","priority":"High"}'
```
