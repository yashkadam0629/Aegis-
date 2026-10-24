# Known limitations and roadmap

## Analytical limitations
1. **Synthetic labels.** Every metric measures recovery of simulated scenarios. Real laundering/fraud is adaptive, rarer (~0.01–1% prevalence vs ~2.4% here), noisier and mostly unlabelled. Do not quote these numbers as detection rates.
2. **Template leakage is reduced, not removed.** Scenarios are parametric; a held-out-variant test and purging of boundary-straddling scenarios are provided, but similar generator logic in train and test remains.
3. **Amount is a shortcut.** Simulated suspicious payments are larger than background payments, so transaction-only models are unrealistically strong baselines; account age/novelty features may proxy generator choices.
4. **Small test scenario counts.** The test window contains ~19 suspicious scenarios; scenario-level recall moves in ~5-point steps and group comparisons (A/B/C/D) have wide uncertainty. Bootstrap intervals are over transactions, not scenarios, and are therefore optimistic.
5. **Calibration is conditional on synthetic prevalence.** Scores are shown as risk scores; isotonic calibration does not make them real-world probabilities.
6. **Risk-engine weights are operational choices**, evaluated but not statistically fitted; bands are not regulatory categories.
7. **Cost figures are assumptions** (25 analyst-minutes, ₹600/h, 10% loss rate on missed value). No savings, losses prevented or ROI are claimed.
8. **Exposure is a heuristic** (largest hop for path patterns). It does not know which units of money moved where; "flow tracing" is approximate.
9. **Detector thresholds are heuristics** (value-retention band 0.55–1.25 for cycles, 0.65–1.05 for chains, 24 h hop gap, ≥5 unique counterparties). Real adversaries who split amounts, add noise hops or use slower timing will evade fixed bands (see the evasion stress test, which shows only the effect of the generator's evasion knobs).
10. **Global graph metrics are not model inputs** (to avoid look-ahead). Snapshot-based PageRank/community features are unexplored.
11. **Community detection** (Louvain) groups densely connected accounts; communities are not organisations.
12. **No fairness/subgroup analysis**, no entity resolution, no cross-institution view.

## Engineering limitations
* PostgreSQL, Docker images and docker-compose were written but **not run** in the authoring environment (neither was available); only SQLite + local processes were exercised.
* Neo4j and Kafka/Redpanda are not implemented (documented design decisions D2, D7).
* Jobs are in-process threads: they do not survive a server restart and are not horizontally scalable.
* Feature generation is a single-threaded Python sweep (≈15 s per 100k transactions here); the feature cache is keyed by dataset version.
* The in-memory transaction index is rebuilt per process and per dataset version; very large datasets need an out-of-core or database-backed index.
* Authentication is a shared key + unauthenticated analyst header.
* The frontend bundle is a single ~1.2 MB chunk (ECharts + Cytoscape); code-splitting would help.
* Screenshots in `docs/screenshots/` were taken at reduced resolution from the running application.

## Roadmap (ordered by expected value)
1. Real reviewer feedback loop: store analyst dispositions as labels, retrain, analyse disagreement; compare model vs reviewer outcomes.
2. Investigation-queue optimisation (expected exposure ÷ estimated effort) once costs are validated.
3. Snapshot graph embeddings / temporal motif features evaluated against current local features.
4. PostgreSQL CI job + Docker build in CI; code-splitting; role-based auth.
5. Counterfactual explanations ("risk without graph evidence"); alert-level SHAP aggregation.
6. Optional broker-backed streaming adapter behind the existing `FeatureState` interface.
7. PaySim/public-dataset adapters with explicit documentation of what each dataset can and cannot support.
