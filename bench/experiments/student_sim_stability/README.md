# Student Simulator Stability Experiment

This experiment evaluates whether `StudentSimulator` preserves persona behavior
across tasks, repeated runs, student models, and tutor-temperature perturbations.

Issue #83 version: static contracts/rubrics/policies live under `resources/`,
and new outputs are written under `results/issue83/`. Final validation is strict:
missing control outputs, fallback aggregates, untagged user turns, and
template-like D4 duplicate score clusters are hard failures.

## Scope

- **Tutor**: configured by
  `server.config.llm_config.STUDENT_SIM_STABILITY_TUTOR_MODEL`
- **Student models under test**:
  - `openai/gpt-5.4`
  - `anthropic/claude-sonnet-4-6`
  - `google/gemini-3.1-pro-preview`
- **Judge panel**: configured by
  `server.config.llm_config.STUDENT_SIM_STABILITY_JUDGE_MODELS`; primary judge
  outputs are mirrored to `judge_outputs/` for aggregation
- 6 tasks × 2 personas per task, 3 repeats, tutor temperatures `0.0` and `1.0`
- 8 tutor turns per conversation
- 7 generated student-model turns per conversation; the initial opening is a
  fixture/control stimulus and is excluded from student-behavior metrics

Total: 216 live trials + 36 control trials = 252 conversations.

## Prerequisites

```bash
pip install -r requirements.txt
# Either export OPENROUTER_API_KEY or put it in bench/.env / repo-root .env.
export OPENROUTER_API_KEY=sk-or-...
```

## Quick Start (one command)

From `bench/`:

```bash
python -m experiments.student_sim_stability.cli all -w 6
```

This runs the full issue83 pipeline: controlled probes → scripted dialogues →
generate live/control conversations → render judge prompts → run the configured
judge panel → compute judge agreement → aggregate primary-judge results → audit
→ validate → generate HTML report.

## Step-by-Step Pipeline

Each stage can also be run independently:

```bash
# Preview experiment scale
python -m experiments.student_sim_stability.cli dry-run

# Cheap issue83 pilot: controlled probes, scripted dialogues, 10 live trials,
# matching controls, judge panel, aggregate, audit, report, and strict validation
python -m experiments.student_sim_stability.cli pilot

# Generate conversations (tutor + student via OpenRouter)
python -m experiments.student_sim_stability.cli generate -w 6

# Run controlled probes and scripted dialogues before full live robustness.
# Omit --model to run all configured student models.
python -m experiments.student_sim_stability.cli probes --model openai/gpt-5.4
python -m experiments.student_sim_stability.cli scripted --model openai/gpt-5.4

# Render judge prompt files from conversations
python -m experiments.student_sim_stability.cli render-judges --dimension all --clean

# Run judge evaluations (OpenRouter). Use --all-models for issue83 agreement.
python -m experiments.student_sim_stability.cli judge --dimension all --workers 6 --all-models
python -m experiments.student_sim_stability.cli judge-agreement

# Aggregate judge outputs into report input JSON
python -m experiments.student_sim_stability.cli aggregate --strict --profile full

# Initialize human-alignment label artifacts and write data-quality audit
python -m experiments.student_sim_stability.cli human-alignment
python -m experiments.student_sim_stability.cli audit --profile full

# Generate HTML report
python -m experiments.student_sim_stability.cli report --profile full

# Validate artifact counts and required report/audit artifacts
python -m experiments.student_sim_stability.cli validate --strict --profile full
```

## Layout

| Path | Purpose |
|------|---------|
| `cli.py` | CLI entry point for all pipeline stages |
| `core/config.py` | Experiment matrix and output paths; model lists are re-exported from `server.config.llm_config` |
| `core/paths.py` | Single source of truth for experiment, resource, and bench-root paths |
| `core/contracts.py` | Loads experiment-private persona contracts |
| `core/rubrics.py` | Loads named rubric IDs, versions, and required score keys |
| `core/artifacts.py` | Snapshots static contracts, rubrics, policies, and runtime metadata into a results directory |
| `pipeline/runner.py` | Conversation generation (tutor + student via OpenRouter) |
| `pipeline/probes.py` | Single-turn targeted persona probe generation |
| `pipeline/scripted_dialogues.py` | Deterministic multi-turn tutor scripts |
| `pipeline/render_judge_prompts.py` | Renders judge prompts from conversation files using rubric prompt artifacts |
| `pipeline/judge.py` | Executes judge prompts via OpenRouter |
| `pipeline/aggregate.py` | Joins judge outputs with metadata |
| `analysis/evaluator.py` | Legacy/programmatic evaluator helpers |
| `analysis/judge_agreement.py` | Computes score agreement across configured judge models |
| `analysis/validate.py` | Validates artifact counts and aggregation shape |
| `analysis/report.py` | Generates HTML report with embedded charts |
| `analysis/data_quality.py` | Writes strict issue83 data-quality audit artifacts |
| `analysis/human_alignment.py` | Initializes human label artifacts and computes agreement when labels are filled |
| `resources/contracts/` | Experiment-private copied persona, emotional-profile, tutor, and simulator contracts |
| `resources/rubrics/` | Experiment-private D1-D4/control/P1/B1 rubric definitions and prompt templates |
| `resources/policies/` | No-fallback, opener, control, judge, and model-comparison policies |
| `resources/model_metadata_schema.json` | Static model metadata schema reference |
| `docs/internal/` | Internal notes and historical fix reports |
| `results/` | Git-ignored generated experiment outputs, including smoke runs |

## Evaluation Dimensions

| Dimension | What it measures | Granularity |
|-----------|-----------------|-------------|
| D1 | Persona adherence per generated student message | 252 sampled evals by default |
| D2 | Cross-run reproducibility (same config, 3 repeats) | 72 eval groups |
| D3 | Cross-model consistency (3 models, anonymized) | 72 eval groups |
| D4 | Persona drift over conversation turns | 252 evals before aggregation, 216 live rows in the report aggregate |
| Control | Persona vs generic student distinctiveness | 36 eval pairs |
| P1 | Targeted single-turn persona probes | 60 evals in full mode |
| B1 | Blind persona identification from scripted dialogues | 24 evals in full mode |

## Score Path

Every reported score follows the same audit path:

```text
persona contract -> generated conversation or probe -> rendered judge prompt/context
-> judge JSON score -> aggregate metric -> chart/table -> interpretation
```

| Stage | Artifact | Validation role |
|-------|----------|-----------------|
| Persona contract | `contracts_snapshot/personas/*.json` | Stable source for expected knowledge, emotion, behavior, questions, confusion, and failure modes |
| Generated transcript | `conversations/`, `probes/responses/`, `scripted/conversations/` | Contains explicit turn `source`; only `student_model` turns are scored |
| Judge prompt/context | `judge_inputs/*.json` | Stores `rubric_id`, `rubric_version`, persona contract version, source file, and rendered prompt |
| Judge score | `judge_outputs/*.json` and `judge_outputs_by_model/` | Stores judge model, temperature, input hash, rubric version, score fields, and failure taxonomy |
| Aggregate metric | `evaluations/all_evaluations.json` and `report/stability_stats.json` | Applies the rubric aggregation formula for each dimension |
| Report visual | `report/stability_report.html` | Shows rubric definition, inputs, score fields, scale, aggregation, chart/table, and action guidance |

## Output Structure

All new issue #83 outputs are written to `results/issue83/` (git-ignored):

```
results/issue83/
├── conversations/          # 252 conversation JSON files
├── contracts_snapshot/     # refreshed copy for this run
├── rubrics_snapshot/
├── policies_snapshot/
├── probes/
├── scripted/
├── human_alignment/
├── judge_inputs/           # rendered judge prompts + metadata
├── judge_outputs/          # primary judge evaluation results
├── judge_outputs_by_model/ # all configured judge-model outputs
├── evaluations/
│   └── all_evaluations.json
└── report/
    ├── stability_report.html
    ├── stability_stats.json
    ├── judge_agreement.json
    ├── data_quality_audit.json
    └── data_quality_audit.md
```

Conversation turn source values:

- `fixture_opening`: persona/task opening for live conversations; excluded from
  metrics.
- `control_neutral_opening`: neutral opening for control conversations; excluded
  from metrics.
- `student_model`: generated student simulator turn; included in student metrics.
- `tutor_model`: generated tutor turn.
