# Judge Validation

This experiment validates the scoring judge before external-agent results depend on it.

This experiment provides:

- a fixed pilot corpus in `pilot_corpus.json` — 36 items in the v4 cut, split
  into 20 real-run single-trace excerpts across debug, implementation,
  strategy, and end-to-end tasks plus 16 synthetic adversarial items (8
  good/bad pairs across D1 finance adaptation, D2 code adaptation, D3
  pedagogical method, D4 instructional accuracy, D6 safety boundaries, and the
  QR result judge)
- a human label schema and example artifact for expert review
- rendered judge prompts with rubric and prompt metadata
- repeated same-prompt judge runs
- Stage 3 primary gate: adversarial ranking accuracy plus multi-judge
  within-one agreement per dimension
- diagnostic stability metrics: mean absolute score delta, within-one score
  rate, pass/fail flip rate
- diagnostic prompt-format robustness metrics across semantically equivalent
  transcript formats
- adversarial pair metrics: whether the stronger transcript scores higher
- one-factor sensitivity metrics: whether targeted defects move the intended rubric score
- evidence and reason coverage plus lightweight explanation consistency
- human-alignment metrics against expert labels, including inter-rater
  agreement (reviewer-vs-reviewer) and judge-vs-reviewer-mean for
  multi-reviewer sample/dim groups
- Markdown, HTML, and machine-readable JSON reports

## Rubrics and Validation Metrics

Tutor/agent scoring rubrics define the rulebook for the transcript. Examples:
`quant_correctness.v1`, `code_correctness.v1`, `teaching_quality.v1`,
`student_adaptation.v1`, `tool_workspace_use.v1`, `failure_handling.v1`,
`task_completion.v1`, and `final_outcome_quality.v1`.

Judge-validation metrics evaluate the judge as the scorer applying that
rulebook. The primary Stage 3 acceptance metrics mirror PR #99: categorical
correctness through adversarial ranking accuracy, and cross-judge robustness
through multi-judge within-one agreement by dimension. Repeated-run score
delta, prompt-format sensitivity, one-factor sensitivity, evidence consistency,
and human expert absolute alignment are diagnostics.

## Commands

Inspect the planned corpus:

```bash
python -m experiments.judge_validation.run dry-run
```

Render prompt inputs without calling a judge model:

```bash
python -m experiments.judge_validation.run render
```

Export the reviewer packet for expert labeling:

```bash
python -m experiments.judge_validation.run export-review-packet
```

This writes `human_review_packet.json`, `human_review_packet.md`,
`google_form_bilingual.md`, `human_label_template.csv`, and the private
`human_review_sample_map.json` under
`experiments/judge_validation/results/human_review_packet/`.

Export a Chinese reviewer packet for Chinese-speaking experts. Conversation
transcripts stay in English so the human and the judge score the same artifact;
only rubric score anchors, required evidence, and common failure modes are
translated.

```bash
python -m experiments.judge_validation.run export-review-packet --language zh
```

This adds `human_review_packet_zh.json`, `human_review_packet_zh.md`, and
`google_form_zh.md` to the same directory. Use `google_form_bilingual.md` to
build the English-track Google Form and `google_form_zh.md` to build a separate
Chinese-track Google Form. Dropdown option values (`sample_id`, `rubric_id`,
`human_score`) stay ASCII in both forms so the two CSV exports converge through
the same `convert-human-labels` path without manual header alignment.

Run the Stage 1 judge gate with three repeats per item:

```bash
python -m experiments.judge_validation.run judge --repeats 3 --model anthropic/claude-sonnet-4.6
```

Run prompt-format robustness variants:

```bash
python -m experiments.judge_validation.run judge --repeats 3 --prompt-variants baseline,role_blocks,markdown_transcript --model anthropic/claude-sonnet-4.6
```

Items with prebuilt evaluation context use the baseline context once, because
their QR-style payload already contains task, acceptance criteria, tool outputs,
and result summary in a canonical format.

Generate reports from `judge_runs.json`:

```bash
python -m experiments.judge_validation.run report
```

Run the direct pairwise adversarial ranking gate:

```bash
python -m experiments.judge_validation.run pairwise --repeats 3 --model anthropic/claude-sonnet-4.6
python -m experiments.judge_validation.run report-pairwise
```

The pairwise report requires successful records for every declared adversarial
pair before the diagnostic gate can pass.

Run a second judge model into the same `judge_runs.json` shape before promoting
the Stage 3 primary gate. The report computes multi-judge within-one from any
records that share sample, rubric, dimension, and prompt variant across two or
more `judge_model` values.

Convert a Google Form CSV export into the canonical label artifact:

```bash
python -m experiments.judge_validation.run convert-human-labels \
  --csv reviewer_export.csv \
  --labels-output experiments/judge_validation/human_labels.json
```

Generate the Stage 3 human-alignment report:

```bash
python -m experiments.judge_validation.run human-alignment \
  --runs experiments/judge_validation/results/judge_runs.json \
  --labels experiments/judge_validation/human_labels.json
```

Outputs are written under `bench/experiments/judge_validation/results/` by default.
External-agent `score.json` exports carry the selected validated run through a
`judge_reliability` metadata block.

The human-alignment report is a diagnostic appendix for absolute-score fit. The
default `--sample-map auto` mode uses `human_review_sample_map.json` when the
bundled corpus, bundled labels, and bundled `judge_runs.json` are selected, so
blind `jv_review_###` labels map back to the original corpus sample IDs. The
report adds two multi-reviewer sections once the label set
has ≥ 2 distinct reviewers on the same sample/rubric/dimension:

- **Inter-rater agreement** — reviewer-vs-reviewer exact / within-one / mean
  absolute delta, overall and per dimension and per reviewer pair. Answers
  "do reviewers agree with each other?" independent of the judge.
- **Judge vs. reviewer MEAN** — for each multi-reviewer group, collapses
  duplicate submissions from one reviewer into that reviewer's mean first,
  then averages across distinct reviewers to get the group human mean, and
  reports judge delta against that group mean. Answers "does the judge match
  the expert consensus on groups where consensus is measurable?"

### Current Human-Alignment Pilot

Tracked pilot artifacts:

- `human_labels.json`: anonymized expert labels, 68 labels over 34
  sample/rubric/dimension cells, with 2 reviewers per covered cell.
- `human_review_sample_map.json`: private blind-review ID mapping from
  `jv_review_###` to the original corpus sample IDs.

Metric logic:

- Exact agreement: nearest rounded judge mean equals the human score.
- Within-one agreement: `abs(judge_mean_score - human_score) <= 1`.
- Pass/fail agreement: judge mean and human score fall on the same side of the
  pass threshold, currently score `3`.
- Mean absolute delta: average absolute score distance between the judge mean
  and the human score.
- Mean signed delta: average `judge_mean_score - human_score`; negative values
  mean the judge scores below reviewers.
- Inter-rater agreement: reviewer-vs-reviewer score distance on shared
  sample/rubric/dimension groups, independent of the judge.
- Judge vs. reviewer MEAN: each shared group collapses duplicate labels per
  reviewer, averages reviewer means, then compares the judge mean to that
  expert consensus score.

Current default run `jv_20260429_stage3_combined` produces:

- Label-level exact agreement: `0.4265`.
- Label-level within-one agreement: `0.6912`.
- Label-level pass/fail agreement: `0.7794`.
- Label-level mean absolute delta: `0.8995`.
- Inter-rater within-one agreement: `0.8529` across 34 reviewer-pair
  comparisons.
- Judge-vs-reviewer-MEAN within-one agreement: `0.7059` across 34 multi-reviewer
  groups.
- Human-alignment status: `diagnostic_only` / `needs_review`.

Current insight: the human panel is coherent enough to expose judge calibration
gaps. D6 safety and D3 pedagogy are the strongest current slices. D1 finance
adaptation, D2 code adaptation, D4 instructional accuracy, and `result_judge`
are the recalibration and adjudication slices before human alignment can become
an acceptance gate.

Next human test round: label all 36 corpus item/dimension cells with at least 2
independent quant reviewers, prioritize D1/D2/D4/`result_judge`, require
evidence spans plus failure tags for all `abs(delta) >= 2` cases, and use a
third reviewer for reviewer-vs-reviewer deltas of 2 or more.

## Scope

The current automated report covers adversarial ranking, multi-judge
consistency when ≥ 2 judge models are present, repeated same-prompt stability,
prompt-format robustness, one-factor sensitivity, lightweight evidence
consistency, and human-alignment diagnostics (per-label, inter-rater, and
judge-vs-reviewer-mean). Broader real-transcript sampling and rubric-order
sensitivity can extend the same report shape.
