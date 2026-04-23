# Judge Validation

Stage 1 validates the scoring judge before external-agent results depend on it.

This experiment provides:

- a fixed pilot corpus in `pilot_corpus.json`
- rendered judge prompts with rubric and prompt metadata
- repeated same-prompt judge runs
- stability metrics: mean absolute score delta, within-one score rate, pass/fail flip rate
- adversarial pair metrics: whether the stronger transcript scores higher
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

Generate reports from `judge_runs.json`:

```bash
python -m experiments.judge_validation.run report
```

Outputs are written under `bench/experiments/judge_validation/results/` by default.

## Scope

This stage covers repeated same-prompt stability and obvious good-vs-bad adversarial ranking. Later stages add rubric-order sensitivity, prompt variants, multi-judge comparison, and human quant expert alignment.
