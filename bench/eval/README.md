# bench/eval — Standalone Scoring Package

The scoring pipeline for QuantAgentBench. Produces an `EvalOutput` for any
finished session bundle without depending on the server runtime.

## Layout

- `contracts/` — public dataclasses: `Bundle`, `EvalRequest`, `EvalOutput`,
  serializers, schema-evolution rules
- `core/` — `EvalCoordinator`, preflight, scoring math
- `tracks/` — per-track orchestration (`qr.py`, `qp.py`)
- `judges/` — LLM-backed quality judges
- `programmatic/` — code, process, and tool-usage evaluators (no LLM)
- `inputs/` — task/persona/conversation/reference context builders
- `rubrics/` — judge rubric JSONs + registry
- `storage/` — append-only `evaluations/index.json` + `score_n/*.json`
- `backfill/` — convert legacy `run_state.json` → `bundle.json` v1
- `score.py` — top-level `score(bundle) → EvalOutput` entry
- `llm_config.py` — judge model defaults + OpenRouter helpers
- `tool_filters.py` — shared tool-classification constants

## Score a bundle from Python

```python
from pathlib import Path
from eval.score import score

result = score(
    bundle="bench/results/server/S01/double_novice/2026.../bundle.json",
    bench_root=Path("bench"),
)
print(result.overall_score, result.qr.score, result.qp.score)
```

The `score()` call:
- runs preflight, QR, and QP independently
- writes nothing to disk (set `eval_mode="qr"` to run only one track)
- returns an `EvalOutput` carrying both track results plus aggregate cost

For batch use, hold the `bench_root` fixed and loop. The `EvalCoordinator`
in `core/coordinator.py` is the persisting variant the server uses; call
that directly when you want score files written.

## Backfill legacy results to v1 bundles

```bash
python -m eval.backfill.run_state_to_bundle --recursive bench/results/server
```

Skips directories that already have `bundle.json`. Pass `--force` to overwrite.

## Decoupling guarantee

`bench/eval/` has zero `from server.*` imports. The CI smoke test
(`bench/tests/unit/test_eval_score_standalone.py`) enforces this with a
`grep` assertion plus an end-to-end `score()` invocation. If you add an
import that reaches into `bench/server/`, that test fails.

`bench/server/` is allowed to import from `bench/eval/` (one-way: server
uses eval as a library; eval has no opinion on server). `bench/eval/`
shares neutral utilities — pricing, model defaults — with the rest of
the repo via `bench/config/`.
