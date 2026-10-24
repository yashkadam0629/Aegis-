# Model card — AEGIS transaction risk model

- **Run / version:** `MR-59202d67` / `D-lightgbm-20261024171619`  · feature version `fv1` · risk-config `risk-v1`
- **Model:** D_combined/lightgbm (LightGBM gradient-boosted trees) wrapped with an isotonic calibrator fitted on the validation period, plus an unsupervised IsolationForest anomaly signal (no labels).
- **Trained on:** synthetic dataset `DS-d8b2882d` (version `39b89a30b2a6`), 2026-01-01 → 2026-03-13; validation to 2026-03-31; tested 2026-03-31 → 2026-04-30.
- **Intended use:** research and investigation-support prototype that ranks transactions and patterns for analyst review. **Not** a certified AML system, not a basis for automated adverse action, and makes no legal determinations.
- **Out of scope:** real customer decisions, regulatory filings, any use on real data without independent validation, fairness assessment and a security review.

## Output semantics
The model emits a *risk score* in [0,1]. After isotonic calibration it is monotone with the raw score and better calibrated **on synthetic prevalence only**; it is not a probability of crime. Risk bands (Critical ≥0.80, High ≥0.60, Medium ≥0.40, Low ≥0.20) are operational categories.

## Inputs
49 point-in-time features in three groups: transaction (amount, time, type, channel, near-reference-amount), account behaviour (windowed counts/values, velocity, dormancy, z-scores, new-counterparty) and local graph (destination fan-in, reciprocity, 2/3-hop cycle closure, pass-through chain position, forwarding ratio). See `docs/data_dictionary.md`.

## Measured performance (test period, synthetic labels)
- PR-AUC 0.930 (prevalence 1.53%); recall at 1% review capacity 64.5%; rules baseline PR-AUC 0.095.
- Full tables with bootstrap intervals, held-out template results, thresholds and calibration: [`evaluation_report.md`](evaluation_report.md).

## Top features (normalised split gain)
- `amount` — 0.072
- `s_age_d` — 0.062
- `d_age_d` — 0.062
- `d_dormancy_h` — 0.049
- `s_dormancy_h` — 0.045
- `d_in_val_24h` — 0.041
- `s_out_val_24h` — 0.040
- `d_burst_ratio` — 0.039

## Explainability
Per-alert SHAP (TreeExplainer) contributions for the highest-scoring transaction are stored with each alert. Attributions describe model behaviour; they are not proof of causality or intent.

## Known limitations and risks
- Labels are synthetic scenario labels; metrics measure recovery of simulated patterns, not real-world fraud detection.
- Synthetic prevalence (~2-3% of transactions) is far higher than real-world prevalence; precision is not transferable.
- Scenario templates are parametric; hold-out by template variant reduces but does not remove similarity between train and test patterns.
- Point-in-time graph features are local (bounded neighbourhood); global metrics such as PageRank/communities are not used in the supervised model to avoid look-ahead.
- Amount alone separates many simulated scenarios (the transaction-only model is a strong baseline) — an artefact of synthetic amount distributions.
- Account-age and counterparty-novelty features may act as proxies for synthetic generation choices (e.g. ~35% of scenario participants are recently opened accounts).
- No fairness or subgroup analysis has been performed; synthetic customer segments carry no demographic meaning.
- Drift monitoring is distributional (PSI); it cannot detect accuracy loss without labels. Promotion of a new model is manual.
