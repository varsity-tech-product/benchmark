# Judge Validation

This experiment validates the scoring judge before external-agent results depend on it.

This experiment provides:

- a fixed pilot corpus in `pilot_corpus.json` — 34 items in the v3 cut, split
  into 20 real-run single-trace excerpts across debug, implementation,
  strategy, and end-to-end tasks plus 14 synthetic adversarial items (7
  good/bad pairs across D1 finance adaptation, D2 code adaptation, D3
  pedagogical method, D4 instructional accuracy, D6 safety boundaries, and the
  QR result judge)
- a human label schema and example artifact for expert review
- rendered judge prompts with rubric and prompt metadata
- repeated same-prompt judge runs
- stability metrics: mean absolute score delta, within-one score rate, pass/fail flip rate
- prompt-format robustness metrics across semantically equivalent transcript formats
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
rulebook. They include repeated-run score delta, within-one agreement,
pass/fail flip rate, adversarial ranking pass rate, prompt-format sensitivity,
one-factor sensitivity pass rate, evidence consistency, and human expert
alignment.

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
python -m experiments.judge_validation.run judge --repeats 3 --model anthropic/claude-sonnet-4-6
```

Run prompt-format robustness variants:

```bash
python -m experiments.judge_validation.run judge --repeats 3 --prompt-variants baseline,role_blocks,markdown_transcript --model anthropic/claude-sonnet-4-6
```

Items with prebuilt evaluation context use the baseline context once, because
their QR-style payload already contains task, acceptance criteria, tool outputs,
and result summary in a canonical format.

Generate reports from `judge_runs.json`:

```bash
python -m experiments.judge_validation.run report
```

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
  --labels experiments/judge_validation/human_labels.json \
  --sample-map experiments/judge_validation/results/human_review_packet/human_review_sample_map.json
```

Outputs are written under `bench/experiments/judge_validation/results/` by default.
External-agent `score.json` exports carry the selected validated run through a
`judge_reliability` metadata block.

The human-alignment report adds two multi-reviewer sections once the label set
has ≥ 2 distinct reviewers on the same sample/rubric/dimension:

- **Inter-rater agreement** — reviewer-vs-reviewer exact / within-one / mean
  absolute delta, overall and per dimension and per reviewer pair. Answers
  "do reviewers agree with each other?" independent of the judge.
- **Judge vs. reviewer MEAN** — for each multi-reviewer group, collapses
  duplicate submissions from one reviewer into that reviewer's mean first,
  then averages across distinct reviewers to get the group human mean, and
  reports judge delta against that group mean. Answers "does the judge match
  the expert consensus on groups where consensus is measurable?"

## Scope

The current automated gate covers repeated same-prompt stability, prompt-format
robustness, one-factor sensitivity, obvious good-vs-bad adversarial ranking,
lightweight evidence consistency, and human-alignment reporting (per-label,
inter-rater, and judge-vs-reviewer-mean). Broader real-transcript sampling and
rubric-order sensitivity can extend the same report shape.
