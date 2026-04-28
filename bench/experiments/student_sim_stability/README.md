# Student Simulator Stability

A benchmark that asks: **does an LLM-driven student simulator preserve a
specified persona across tasks, repeated runs, student-model swaps, and
tutor-temperature perturbations?**

The companion paper (the student-simulator-stability project) supports three independent claims:

| Claim | Statement |
|---|---|
| **A — Stability** | The simulator preserves persona behavior under task / repeat / model / tutor-temperature perturbations. |
| **B — Model selection** | Of the three candidate student backbones (GPT-5.4, Sonnet 4.6, Gemini 3.1 Pro), one is selected for the production benchmark on a D1+D2+D3 composite. |
| **C — Metric calibration** | The LLM-judge panel agrees with a human quant expert within ±1 point on a 39-sample alignment study. |

Every reported number traces through the audit path
`persona contract → generated transcript → rendered judge prompt → judge JSON
→ aggregate → chart/table`. All seven stages live on disk and can be inspected.

---

## What is in this repository

```
bench/experiments/student_sim_stability/
├── cli.py                   # one entry point, all pipeline stages
├── core/                    # config, paths, contracts, rubrics, snapshots, numerics
├── pipeline/                # generate → render → judge → aggregate
├── analysis/                # report (HTML/PDF/CSV/TeX), audit, validate, human-alignment
│   └── components/          # per-chart / per-table Component framework
├── judge_qualification/     # gate that qualifies the judge panel before the full run
├── resources/               # rubrics, prompt templates, persona contracts, policies
├── docs/HUMAN_ALIGNMENT.md  # bilingual operator reference for human evaluators
└── results/                # canonical runs (entire tree is git-ignored — see below)
```

The whole `results/` tree (both the **raw artifacts** —
`conversations/`, `probes/`, `judge_inputs/`, `judge_outputs/`,
`judge_outputs_by_model/` — and the **aggregated outputs** —
`evaluations/`, `report/`, `human_alignment/`, `*_snapshot/`) is git-ignored
and hosted on HuggingFace following the repo's `data/` convention. To
re-render the report or inspect the audit locally, fetch the canonical
`results/main/` and `results/judge_qualification/` trees from HuggingFace
into this path first.

---

## Quick start

### Prerequisites

```bash
pip install -r requirements.txt
# Put your OpenRouter key in bench/.env or repo-root .env, or export it:
export OPENROUTER_API_KEY=sk-or-...
```

### Re-render the report from the shipped canonical run (no LLM cost)

```bash
cd bench
python -m experiments.student_sim_stability.cli report
# Open results/main/report/stability_report.html
```

### Re-run the full pipeline from scratch (uses OpenRouter budget)

```bash
cd bench

# Step 0: qualify the judge panel against the fixed golden corpus
python -m experiments.student_sim_stability.cli judge-qualification render
python -m experiments.student_sim_stability.cli judge-qualification judge
python -m experiments.student_sim_stability.cli judge-qualification report

# Step 1-7: probes → generate → render → judge → aggregate → audit → report
python -m experiments.student_sim_stability.cli all -w 6
```

`cli all` only runs once `judge-qualification report` reports `ok=true`, so the
gate must pass before the full pipeline can proceed.

### Bundle figures / tables for paper inclusion

After `cli report`, every chart/table component dumps its `.html`, `.csv`,
`.tex`, and `.pdf` siblings under `results/main/report/components/`. To copy
them into a paper-asset directory with a sha256 manifest:

```bash
python -m experiments.student_sim_stability.cli paper-export --target ./paper_assets
```

---

## Experiment scope

| Axis | Value |
|---|---|
| Tutor model | `STUDENT_SIM_STABILITY_TUTOR_MODEL` (configured in `server.config.llm_config`) |
| Student models under test | `openai/gpt-5.4`, `anthropic/claude-sonnet-4-6`, `google/gemini-3.1-pro-preview` |
| Judge panel | `STUDENT_SIM_STABILITY_JUDGE_MODELS` (multi-judge; primary judge mirrored to `judge_outputs/`) |
| Tasks × personas | 6 tasks × 2 personas/task |
| Repeats × tutor temperatures | 3 repeats × {0.0, 1.0} |
| Conversation turns | 8 tutor + 7 generated student turns; the opening is a fixture, excluded from metrics |
| Total trials | 216 live + 36 control = **252 conversations** |

`cli dry-run` prints the live count from the current config without touching
the network.

---

## Evaluation dimensions

| Dimension | What it measures | Granularity |
|---|---|---|
| **D1** | Persona adherence per generated student message | 252 sampled evals |
| **D2** | Cross-run reproducibility (same config, 3 repeats) | 72 eval groups |
| **D3** | Persona drift over conversation turns | 252 evals before aggregation, 216 live rows in the report |
| **Control / C1** | Persona vs generic-student distinctiveness | 36 eval pairs |
| **P1** | Targeted single-turn persona probes | 60 evals |
| **B1** | Blind persona identification from anonymized live conversations | 24 evals |

The 7 axes of the failure taxonomy (`knowledge_leak`, `under_competence`,
`emotional_mismatch`, `generic_student_behavior`, `co_teacher_drift`,
`task_forgetting`, `persona_contract_contradiction`) are emitted by every
applicable judge call and aggregated under `report/failure_taxonomy_stats.json`.

---

## Pipeline stages

`cli all` runs the seven stages below; each is also runnable independently.

```bash
cli dry-run                    # print experiment scale
cli probes                     # P1 targeted persona probes (per student model)
cli generate -w 6              # live + control conversations via OpenRouter
cli render-judges --clean --dimension all
cli judge --dimension all --workers 6 --all-models
cli judge-agreement            # multi-judge agreement on shared evals
cli aggregate --strict         # primary-judge → all_evaluations.json
cli aggregate-multi-judge      # 5-view aggregate (sonnet, gpt54, gemini, panel_2, panel_3)
cli human-alignment            # initialize 39-sample manifest + label CSV
cli audit                      # data-quality audit (no fallback, no template clusters, ...)
cli report                     # render stability_report.html + per-component dumps
cli validate --strict          # final artifact-count and shape checks
```

---

## Output structure

The whole `results/` tree is git-ignored and hosted on HuggingFace; nothing
under it is tracked in this repository.

```
results/main/                # full pipeline run, fetched from HF
├── contracts_snapshot/      # frozen copy of resources/contracts/ for this run
├── rubrics_snapshot/        # frozen copy of resources/rubrics/ for this run
├── policies_snapshot/       # frozen copy of resources/policies/ for this run
├── conversations/           # 252 conversation JSON files
├── probes/                  # P1 probe responses
├── judge_inputs/            # rendered judge prompts + metadata
├── judge_outputs/           # primary judge results
├── judge_outputs_by_model/  # per-judge-model results
├── evaluations/
│   ├── all_evaluations.json
│   └── multi_judge_aggregates.json
├── human_alignment/
│   ├── sample_manifest.json
│   ├── human_label_template.csv
│   └── agreement_report.json (after `cli human-alignment --compute`)
└── report/
    ├── stability_report.html
    ├── stability_stats.json
    ├── judge_agreement.json
    ├── data_quality_audit.{json,md}
    ├── failure_taxonomy_stats.json
    ├── failure_cases_candidates.json
    └── components/         # per-component .html / .csv / .tex / .pdf dumps

results/judge_qualification/ # gate run, fetched from HF, same shape minus
                             # human_alignment/
```

Every conversation turn carries an explicit `source`:

| Source | Counted in student metrics? |
|---|---|
| `fixture_opening` | No — task fixture for live conversations |
| `control_neutral_opening` | No — neutral opener for control conversations |
| `student_model` | **Yes** — generated student simulator turn |
| `tutor_model` | No — generated tutor turn (`Context only` in the D3 prompt) |

---

## Reproducing each claim

| Claim | What to read |
|---|---|
| **A — Stability** | `report/stability_report.html` §1 (D1 / D2 / D3 charts and tables); raw aggregates in `evaluations/all_evaluations.json` |
| **B — Model selection** | `report/stability_report.html` §2 (composite ranking); `report/components/d1_by_model*.{tex,csv}`, `d2_by_model*.{tex,csv}`, `d3_drift.{tex,csv}` |
| **C — Metric calibration** | `human_alignment/agreement_report.json`; `report/stability_report.html` §3 (human-vs-LLM table); 39-sample labeling protocol in `docs/HUMAN_ALIGNMENT.md` |

If a number in the report is unfamiliar, the chart card links back to
`judge_outputs/<eval_id>.json` for that exact judge call. The whole
`results/` tree must be fetched from HuggingFace first since none of it is
tracked in git.

---

## Working with the human-alignment study

39 samples, stratified by (dimension, persona). The full bilingual reference
— workflow, persona contracts, per-dimension scoring rules — is in
[`docs/HUMAN_ALIGNMENT.md`](docs/HUMAN_ALIGNMENT.md).

```bash
cli human-alignment              # initialize sample manifest + label CSV
# ... a human grader fills in human_label_template.csv ...
cli human-alignment --compute    # compute agreement against the LLM panel
```

Output: `human_alignment/agreement_report.json` and a top-15 disagreement
breakdown.

---

## Layout reference

| Path | Purpose |
|---|---|
| `cli.py` | CLI entry point for every pipeline stage |
| `core/config.py` | Experiment matrix; model lists re-exported from `server.config.llm_config` |
| `core/paths.py` | Single source of truth for experiment, resource, and bench-root paths |
| `core/contracts.py` | Loads experiment-private persona contracts |
| `core/rubrics.py` | Rubric IDs, versions, required score keys |
| `core/artifacts.py` | Snapshots contracts/rubrics/policies and runtime metadata into the results dir |
| `core/numerics.py` | Stdlib-only `safe_mean` / `safe_std` / `bootstrap_mean_ci` |
| `pipeline/runner.py` | Live conversation generation (tutor + student via OpenRouter) |
| `pipeline/probes.py` | Single-turn targeted persona probe generation |
| `pipeline/render_judge_prompts.py` | Renders judge prompts from conversation files |
| `pipeline/judge.py` | Executes judge prompts via OpenRouter (with sha256 resume-skip) |
| `pipeline/aggregate.py` | Joins primary-judge outputs with metadata |
| `pipeline/aggregate_multi_judge.py` | 5-view aggregate from `judge_outputs_by_model/` |
| `analysis/report.py` | HTML report with embedded charts and Component dumps |
| `analysis/components/` | Per-chart / per-table / per-section Component framework (HTML/CSV/TeX/PDF) |
| `analysis/data_quality.py` | Strict data-quality audit |
| `analysis/validate.py` | Final artifact-count and shape checks |
| `analysis/judge_agreement.py` | Per-pair judge agreement on shared evals |
| `analysis/human_alignment.py` | Sample manifest + human-vs-LLM agreement |
| `analysis/failure_case_picker.py` | Selects representative failure cases for the report appendix |
| `judge_qualification/` | Pre-experiment gate: render → judge → report on the fixed golden corpus |
| `resources/contracts/` | Persona, emotional-profile, tutor, and simulator contracts (immutable per run) |
| `resources/rubrics/` | D1-D3 / control / P1 / B1 rubric JSON + prompt templates |
| `resources/policies/` | No-fallback, opener, control, judge, model-comparison policies |
| `docs/HUMAN_ALIGNMENT.md` | Bilingual reference for the human evaluator |

---

## Editing rubric prompts safely

Editing any prompt template under `resources/rubrics/prompts/*.txt` **must**
be paired with a `RUBRIC_VERSION` bump in `core/rubrics.py`. The judge layer
pins each output to `(input_sha256, judge_model, rubric_id, rubric_version)`;
without a version bump, stale outputs silently survive a re-run because the
SHA-256 of the prompt has changed but the version pin still matches.

---

## Citation

A `CITATION.cff` will be added when the paper is public.

## License

See the repository root `LICENSE` file.
