# No Fallback Policy

Student-simulator-stability validation data must fail loudly when an expected
artifact is missing or invalid. The final report must not convert missing data
into apparently valid metrics.

Hard failures:

- No control-from-S2 fallback.
- No missing control judge inputs or outputs.
- No fixture opening counted as generated student behavior.
- No untagged `role=user` turns.
- No duplicated/template S2 score clusters accepted as judged data.
- No report generated from incomplete aggregate dimensions.

The expected final result directory is `results/main/`.
