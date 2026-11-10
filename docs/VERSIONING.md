# Versions

The adopted model is **v1.0: Global26 + NEON/LRP**. The service code starts at **1.0.0**. API paths use **/v1** for the response contract. These are separate version numbers.

| Change | Model version | Required review |
| --- | --- | --- |
| Major feature additions/removals, original source identities, regime meaning or distance geometry | 2.0 | Dated economic cases, stability, overlap, causality and label mapping |
| Small compatible model or attribution change | 1.1 | New immutable bundle, exact change description, changed-date comparison and interpretation |
| Operational fix preserving model and explanation outputs | Unchanged | Service patch increment, relevant regression checks |
| Breaking API response change | Independent | New API path version and migration guide |

Any refit changes the model artifact and requires a new model version, even with identical feature names. The scale of the change determines major versus minor version. Never overwrite an existing bundle or revise its published historical records.

## Bundle contract

`models/<major.minor>/` contains four files:

1. `model.json`: source identities, calendars, ordered feature recipes, fixed preprocessing, centers, labels, NEON beta and evaluation metadata.
2. `reference.npz`: sorted training feature values and within-class margin/distance distributions, with no executable pickle content.
3. `golden.json`: fixed feature inputs, expected assignments, distances and NEON relevance.
4. `manifest.json`: SHA256 of every bundle file and original research-artifact provenance.

Feature engine schema 1 supports return, realized volatility, level, correlation, return spread and PC1 share, with optional trailing rank. Existing operators permit model-only replacement. An unsupported operator requires service code and schema support before activation.

## Promotion

Keep raw regime decisions and explanation changes visible. The next candidate is judged using the accepted current-regime criteria: contemporaneous direction, participation, leadership, co-movement, stress and concrete counterexamples. Record correlation and family overlap. Switching frequency, future-volatility prediction and portfolio returns are separate evaluation questions.

2023-2024 and examined 2026 dates are exposed development evidence. Reserve a new period before claiming an independent evaluation. The live service now records source observation timestamps and immutable snapshots for future review. Historical publication vintages remain unverified.

Activation validates hashes and golden examples, registers an immutable version/hash pair, requires a replacement result at least as recent as the active model's latest published date, and atomically changes `runtime/active.json`. Each API request reads the active bundle. Prior versions and records remain available. Rollback uses the same validation and freshness gate.
