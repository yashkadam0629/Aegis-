# Data dictionary

All demonstration data are **synthetic**. No real customer information is used. Currency is INR (₹).

## 1. Data sources and provenance

| Source | Used? | Notes |
|---|---|---|
| AEGIS synthetic generator (`app/synthetic/generator.py`) | **Yes** | Deterministic (seed 42), configurable, ten scenario families plus data-quality defects. All reported results come from this source. |
| PaySim, other public datasets | **No** | Not downloaded or used in this repository. PaySim lacks verified laundering labels and real account-to-account relationships for several types, so it would not support the graph claims here. The ingestion layer auto-maps PaySim-style columns (`nameOrig`, `nameDest`, `amount`, …) and could load it via CSV import; PaySim's `step` (hours) would need converting to timestamps first. |
| User CSV upload | Yes (tested with synthetic CSVs) | Treated as untrusted input; see `security.md`. |

## 2. Canonical schema (database)

**datasets** — `dataset_id` PK, `name`, `kind` (synthetic/upload), `source_dataset`, `dataset_version` (content hash), `n_accounts`, `n_transactions`, `min_ts`, `max_ts`, `has_ground_truth`, `meta`.

**accounts** — PK (`dataset_id`, `account_id`); `account_type` (individual, business, merchant, payment_aggregator, beneficiary, unknown), `created_at`, `account_status`, `customer_segment`, `inferred` (true when the account was created from a transaction reference because no accounts file was supplied). *Beneficiaries are accounts with role "payee"; there is no separate beneficiary table.*

**transactions** — PK (`dataset_id`, `transaction_id`); `source_account_id`, `destination_account_id`, `transaction_timestamp` (naive UTC), `amount` (> 0), `currency`, `transaction_type` (P2P_TRANSFER, MERCHANT_PAYMENT, BUSINESS_TRANSFER, BILL_PAYMENT, MERCHANT_SETTLEMENT, UNKNOWN), `channel`, `transaction_status`, `source_dataset`, `ingestion_timestamp`. Indexes on (dataset, source, time), (dataset, destination, time), (dataset, time).

**import_reports** — one row per import with the full data-quality report (JSON) and a path to the rejected-rows CSV.

**alerts** — `alert_id` (deterministic hash), `entity_type/id`, `alert_type`, `risk_score`, `risk_band`, `model_score`, `rule_score`, `n_transactions`, `n_accounts`, `gross_value`, `unique_value` (incremental), `exposed_value`, evidence window, `investigation_status`, `model_version`, `feature_version`, `detail` (JSON: evidence summary, detections with evidence and paths, risk breakdown, alternative scores, exposure definition, SHAP explanation, recommended steps, limitations). **alert_transactions** links alerts to their evidence transactions.

**investigations**, **investigation_alerts**, **investigation_transactions**, **investigation_notes**, **audit_log** — case workflow. `audit_log` stores actor, timestamp, action, field, old value, new value for every change.

**model_runs** — `model_run_id`, `model_name`, `model_version`, `dataset_id`, `dataset_version`, `feature_version`, `training_timestamp`, `evaluation_metrics` (JSON), `configuration`, `artifact_reference` (joblib path), `is_active`.

**pipeline_runs** — per analysis run: window, status, stage timings, scoring latency, missing-feature rates, score histogram, drift report, alert counts.

**txn_scores** — model risk / anomaly percentile / rule score for transactions in the analysed window.

**gt_scenarios**, **gt_txn_labels** — *hidden ground truth* (simulated scenario membership). Read only by training and evaluation code; never exposed by API endpoints.

## 3. Validation and import rules (applied in order)

| Rule | Handling |
|---|---|
| Missing required field (id, source, destination, timestamp, amount) | Rejected (`missing_required`) |
| Unparseable timestamp | Rejected (`invalid_timestamp`); parsed as ISO-8601, normalised to naive UTC |
| Amount not finite or ≤ 0 | Rejected (`invalid_amount`) |
| Currency not in the supported ISO list (blank → INR) | Rejected (`invalid_currency`) |
| Source = destination | Rejected (`self_transfer`) |
| Duplicate `transaction_id`, same content | First kept; later copies counted (`exact_duplicates_dropped`) |
| Duplicate `transaction_id`, different content | First kept; later rows rejected (`duplicate_conflicting`) |
| Account missing from supplied accounts file | Rejected (`unresolved_account`); without an accounts file accounts are inferred |
| Timestamp before account creation | Accepted, counted in `potentially_inconsistent_records` |
| Out-of-order rows | Counted; data is sorted by time on load |

Rejected rows (with source row number and reason) are saved to a downloadable CSV; cells starting with `= + - @` are prefixed with `'` so the file is safe in spreadsheets.

## 4. Synthetic generator

Parameters (`GeneratorConfig`): seed, accounts, days, background transactions, activity dispersion, favourite counterparties per account (network density) and share, new-account share, dormant share, scenario counts, template variants, evasion level, missing/duplicate/out-of-order rates, structuring reference amount. Presets: `quick()`, default, `sized(n)`.

| Scenario | Code | Label | Simulated behaviour |
|---|---|---|---|
| Layered chain | A | suspicious | 4–7 accounts forward 90–99% of value with short, jittered delays |
| Circular flow | B | suspicious | 3–5 account loop returning value to the origin |
| Shared beneficiary | C | suspicious | 6–15 senders pay one account within 12–48 h |
| Coordinated group | D | suspicious | 5–9 accounts repeatedly pay 2–3 common payees in shared time windows |
| Transaction burst | E | suspicious | 25–60 outgoing payments in a few hours |
| Fan-in / fan-out | F | suspicious | 15–40 counterparties converge on / spread from a hub |
| Dormant reactivation | G | suspicious | Previously quiet account sends 10–25 large payments in ≤ 2 days |
| Structuring-like | H | suspicious | 5–10 payments at 86–99.5% of a reference amount |
| **Benign look-alikes** | I | **not suspicious** | payroll, marketplace settlements, group bills, recurring business chains, seasonal merchant surges |
| Combined | J | suspicious | dormant account + chain + shared beneficiary |

Each scenario has a **template variant (0–3)** controlling hop delays, value retention and spread; variants are spread over time and used for the held-out-template evaluation. About 35% of scenario participants are recently opened accounts (a deliberate, disclosed simplification). Data defects injected into background rows only: missing values, negative amounts, invalid currency, exact duplicates, out-of-order rows.

## 5. Model features (`feature_version = fv1`)

All features for transaction *i* use only transactions processed before *i*. Zero-denominator policy: ratios add +1 smoothing; statistics with too little history are NaN.

**A – transaction:** `amount`, `log_amount`, `hour`, `dow`, `is_weekend`, `type_code`, `channel_code`, `near_threshold` (80–100% of the reference amount), `round_amount`.

**B – account behaviour (source account):** counts and values of outgoing/incoming transactions in 1h/24h/7d/30d windows (`s_out_cnt_*`, `s_out_val_*`, `s_in_cnt_*`, `s_in_val_*`), unique outgoing counterparties 24h/7d, `s_dormancy_h`, `s_age_d`, `s_amt_z` (log-amount z-score vs own history, ≥5 prior), `s_burst_ratio` (24h count vs 30-day daily average), `s_med_gap_h` (median of the last ≤10 inter-arrival gaps), `s_inout_ratio_24h`, `s_new_cp`, `s_hist_out_n`, `s_near_thr_cnt_7d`, `s_avg_amt_30d`, `amount_vs_avg30`.

**C – local graph:** destination fan-in (`d_in_cnt_24h`, `d_uniq_senders_24h/7d`, `d_in_val_24h`, `d_burst_ratio`, `d_new_sender`), destination age/dormancy, `recip_cnt`, `cycle_close2/3`, `chain_len`, `s_uniq_senders_24h`, `pass_through_ratio`, `d_out_cnt_24h`, `d_forward_ratio`.

Feature groups: A = transaction; B = A + behaviour; C = A + graph; D = all.
