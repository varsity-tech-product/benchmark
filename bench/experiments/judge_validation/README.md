# Judge Validation

This experiment validates the scoring judge before external-agent results depend on it.

This experiment provides:

- a fixed pilot corpus in `pilot_corpus.json`
- rendered judge prompts with rubric and prompt metadata
- repeated same-prompt judge runs
- stability metrics: mean absolute score delta, within-one score rate, pass/fail flip rate
- prompt-format robustness metrics across semantically equivalent transcript formats
- adversarial pair metrics: whether the stronger transcript scores higher
- one-factor sensitivity metrics: whether targeted defects move the intended rubric score
- evidence and reason coverage plus lightweight explanation consistency
- Markdown, HTML, and machine-readable JSON reports

## Commands

Inspect the planned corpus:

```bash
python -m experiments.judge_validation.run dry-run
```

Render prompt inputs without calling a judge model:

```bash
python -m experiments.judge_validation.run render
```

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

Outputs are written under `bench/experiments/judge_validation/results/` by default.

## Scope

The current automated gate covers repeated same-prompt stability, prompt-format robustness, one-factor sensitivity, obvious good-vs-bad adversarial ranking, and lightweight evidence consistency. Later stages add broader real-transcript sampling, rubric-order sensitivity where the protocol supports it, and human quant expert alignment.
