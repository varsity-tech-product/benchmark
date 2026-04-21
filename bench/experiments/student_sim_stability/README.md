# Student Simulator Stability Experiment

This experiment evaluates whether `StudentSimulator` preserves persona behavior
across tasks, repeated runs, student models, and tutor-temperature perturbations.

## Scope

- **Tutor**: `openai/gpt-4.1-nano` (lightweight, via OpenRouter)
- **Student models under test**:
  - `openai/gpt-5.4`
  - `anthropic/claude-sonnet-4-6`
  - `google/gemini-3.1-pro-preview`
- **Judge**: `anthropic/claude-sonnet-4-6` (via OpenRouter)
- 6 tasks × 2 personas per task, 3 repeats, tutor temperatures `0.0` and `1.0`
- 8 tutor turns per conversation

Total: 216 live trials + 18 control trials = 234 conversations.

## Prerequisites

```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-...
```

## Quick Start (one command)

From `bench/`:

```bash
python -m experiments.student_sim_stability.run all -w 6
```

This runs the full pipeline: generate conversations → render judge prompts →
run judge evaluations → aggregate results → validate → generate HTML report.

## Step-by-Step Pipeline

Each stage can also be run independently:

```bash
# Preview experiment scale
python -m experiments.student_sim_stability.run dry-run

# Generate conversations (tutor + student via OpenRouter)
python -m experiments.student_sim_stability.run generate -w 6

# Render judge prompt files from conversations
python -m experiments.student_sim_stability.run render-judges --dimension all --clean

# Run judge evaluations (OpenRouter)
python -m experiments.student_sim_stability.run judge --dimension all --workers 6

# Aggregate judge outputs into report input JSON
python -m experiments.student_sim_stability.run aggregate --strict

# Validate artifact counts
python -m experiments.student_sim_stability.run validate

# Generate HTML report
python -m experiments.student_sim_stability.run report
```

## Files

| File | Purpose |
|------|---------|
| `config.py` | Experiment matrix, model config, output paths |
| `runner.py` | Conversation generation (tutor + student via OpenRouter) |
| `evaluator.py` | Judge prompt templates (D1–D4 + control) |
| `render_judge_prompts.py` | Renders judge prompts from conversation files |
| `judge_with_openrouter.py` | Executes judge prompts via OpenRouter |
| `aggregate_judge_outputs.py` | Joins judge outputs with metadata |
| `validate_results.py` | Validates artifact counts and aggregation shape |
| `report.py` | Generates HTML report with embedded charts |
| `run.py` | CLI entry point for all pipeline stages |

## Evaluation Dimensions

| Dimension | What it measures | Granularity |
|-----------|-----------------|-------------|
| D1 | Persona adherence per student message | 288 sampled evals by default |
| D2 | Cross-run reproducibility (same config, 3 repeats) | 72 eval groups |
| D3 | Cross-model consistency (3 models, anonymized) | 72 eval groups |
| D4 | Persona drift over conversation turns | 1 eval per conversation |
| Control | Persona vs generic student distinctiveness | 1 eval per control pair |

## Output Structure

All outputs are written to `results/` (git-ignored):

```
results/
├── conversations/          # 234 conversation JSON files
├── judge_inputs/           # rendered judge prompts + metadata
├── judge_outputs/          # judge evaluation results
├── evaluations/
│   └── all_evaluations.json
└── report/
    ├── stability_report.html
    └── stability_stats.json
```
