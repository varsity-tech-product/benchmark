# Data Foundation Status

**Last updated:** 2026-03-02

## 1. Current Tier-1 Order (Logical Sequence)

Data Foundation is currently ordered as:

`D10 -> D11 -> D01 -> D03 -> D02 -> D04 -> D05 -> D06 -> D09 -> D07 -> D08`

Source of truth:
- `bench/*MD/task_classification.md`

## 2. Implemented Task Files (Current)

Available task JSONs in `bench/tasks/layer2/data_analysis`:

| Task | Status | Eval Mode | Notes |
|---|---|---|---|
| `D01_load_inspect_ohlcv` | Implemented | Legacy tool-expected | Uses expected MCP tools + artifact-style result checks |
| `D02_missing_data_detection_handling` | Implemented | Track A optional-tools (hybrid) | Persona-adapted tutoring + concept coverage + runnable Python checks |
| `D03_data_type_conversion_validation` | Implemented | Track A optional-tools (hybrid) | Data typing/classification focus (distinct from D11 realtime ingestion) |
| `D04_ohlcv_summary_statistics` | Implemented | Track A optional-tools (hybrid) | OHLCV descriptive/distribution diagnostics + runnable Python checks |
| `D05_return_computation` | Implemented | Track A optional-tools (hybrid) | Simple vs log returns, compounding interpretation, runnable Python checks |
| `D06_tick_data_aggregation` | Implemented | Track A optional-tools (hybrid) | Tick-to-bar aggregation + timestamp/microstructure caveats |
| `D07_broken_data_feed_diagnosis` | Implemented | Track A optional-tools (hybrid) | Multi-issue feed diagnosis with remediation checklist framing |
| `D08_alternative_data_integration` | Implemented | Track A optional-tools (hybrid) | Alt-data alignment/lag/IC framing + runnable Python checks |
| `D09_feature_engineering_pipeline` | Implemented | Track A optional-tools (hybrid) | Feature construction + multicollinearity + anti-leakage framing |
| `D10_historical_data_fetch` | Implemented | Track A optional-tools (hybrid) | Tutoring eval + code-run checks (`code_execution_attempted`, `code_runs_without_fatal_error`) |
| `D11_realtime_data_fetch` | Implemented | Track A optional-tools (hybrid) | Tutoring eval + code-run checks (`code_execution_attempted`, `code_runs_without_fatal_error`) |

## 3. Track A Evaluation Policy (Now Active for D02-D11, D01 Legacy)

For tasks tagged `track_a_optional_tools`:

- No expected MCP tool list/order is required.
- Agent can solve directly from materials.
- Tool usage is optional.
- Useful tool usage receives bonus.
- Tool misuse (distractors/repeated failures/spam) receives penalty.
- Quant result checks for Track A data tasks use a hybrid rubric:
  - `persona_level_inferred`
  - `level_adaptation_present`
  - `quant_concepts_covered`
  - `code_execution_attempted`
  - `code_runs_without_fatal_error`

## 4. Supporting Materials Added

- Docs:
  - `bench/docs/reference/data_fetch_historical.md`
  - `bench/docs/reference/data_fetch_realtime.md`
- Eval scripts:
  - `bench/evaluation/test_scripts/D02_missing_data_detection_handling.py`
  - `bench/evaluation/test_scripts/D03_data_type_conversion_validation.py`
  - `bench/evaluation/test_scripts/D04_ohlcv_summary_statistics.py`
  - `bench/evaluation/test_scripts/D05_return_computation.py`
  - `bench/evaluation/test_scripts/D06_tick_data_aggregation.py`
  - `bench/evaluation/test_scripts/D07_broken_data_feed_diagnosis.py`
  - `bench/evaluation/test_scripts/D08_alternative_data_integration.py`
  - `bench/evaluation/test_scripts/D09_feature_engineering_pipeline.py`
  - `bench/evaluation/test_scripts/D10_historical_data_fetch.py`
  - `bench/evaluation/test_scripts/D11_realtime_data_fetch.py`
- Shared helper:
  - `bench/evaluation/test_scripts/_track_a_hybrid_eval.py`
- New frozen data fixtures:
  - `bench/data/frozen/tick_data_sample.csv`
  - `bench/data/frozen/sentiment_data.csv`

## 5. Proposal/Classification Alignment

- `task_classification.md` includes D10/D11 as Tier-1 front gate tasks and D01-D09 downstream sequence.
- `quant_task_proposal.md` includes D10/D11 as ingestion prerequisites before D01-D09.
- Proposal keeps original 87-task catalog framing; classification reflects 89 ordered tasks including D10/D11.

## 6. Remaining Data Foundation Gaps

No remaining gaps in the current Tier-1 Data Foundation catalog (`D01-D11`).

## 7. Known Runtime Constraint

Default sandbox is network-restricted (`--network none`) unless explicitly enabled by task config.

Current exception:
- `D10_historical_data_fetch` and `D11_realtime_data_fetch` set `environment.network_enabled: true`
- These two tasks run with network-enabled Docker sandbox and support MCP `search_web` as a core tool.

All other Data Foundation tasks remain offline by default for reproducibility.
