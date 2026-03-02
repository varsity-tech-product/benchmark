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
| `D10_historical_data_fetch` | Implemented | Track A optional-tools (hybrid) | Tutoring eval + code-run checks (`code_execution_attempted`, `code_runs_without_fatal_error`) |
| `D11_realtime_data_fetch` | Implemented | Track A optional-tools (hybrid) | Tutoring eval + code-run checks (`code_execution_attempted`, `code_runs_without_fatal_error`) |

## 3. Track A Evaluation Policy (Now Active for D10/D11)

For tasks tagged `track_a_optional_tools`:

- No expected MCP tool list/order is required.
- Agent can solve directly from materials.
- Tool usage is optional.
- Useful tool usage receives bonus.
- Tool misuse (distractors/repeated failures/spam) receives penalty.
- Quant result checks for D10/D11 use a hybrid rubric:
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
  - `bench/evaluation/test_scripts/D10_historical_data_fetch.py`
  - `bench/evaluation/test_scripts/D11_realtime_data_fetch.py`

## 5. Proposal/Classification Alignment

- `task_classification.md` includes D10/D11 as Tier-1 front gate tasks.
- `quant_task_proposal.md` includes D10/D11 as ingestion prerequisites before D01-D09.
- Proposal keeps original 87-task catalog framing; classification now reflects 89 ordered tasks including D10/D11.

## 6. Remaining Data Foundation Gaps

Not yet implemented as task JSON + eval script in codebase:

- `D02` Missing Data Detection & Handling
- `D03` Data Type Conversion & Validation (classification task, distinct from D11)
- `D04` OHLCV Summary Statistics
- `D05` Return Computation
- `D06` Tick Data Aggregation
- `D07` Broken Data Feed Diagnosis
- `D08` Alternative Data Integration
- `D09` Feature Engineering Pipeline

## 7. Known Runtime Constraint

Default sandbox is network-restricted (`--network none`), so true live API execution requires either:
- local fallback mode without Docker networking restrictions, or
- a network-enabled sandbox profile for ingestion tasks.
