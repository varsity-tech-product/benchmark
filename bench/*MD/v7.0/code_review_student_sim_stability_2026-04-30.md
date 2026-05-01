# Execution Plan — `bench/experiments/student_sim_stability/` cleanup

**Date**: 2026-04-30
**Branch**: `ewan`
**Status**: Wave 1 + Wave 2 + Wave 3 M2 + 2 followups landed; M3 / M4 / M5 / L4 / L5 / L14 / Followup #3 deferred. See Status table in §0.5.
**Companion**: the reserved path for the 9 executable English prompts is `bench/*MD/v7.0/code_review_student_sim_stability_codex_prompts_2026-04-30.md`. If that file does not exist, the prompts have not been persisted yet; use this plan as the source of truth and ask for the prompt file before delegating the work to a fresh Codex conversation.

This file describes WHAT to fix and WHY. Each item lists a behavior-level acceptance check (no LOC-based pass/fail). Shell snippets use either repo-root paths (`bench/...`) or bench-relative paths (`experiments/...`) depending on the stated CWD; markdown link syntax is intentionally minimal because `bench/*MD/v7.0/` is two levels below repo root.

Path-discipline note for shell snippets:

- "Repo-root path" = paths beginning with `bench/...`, valid only when CWD is `/Users/richsion/Desktop/benchmark`.
- "Bench-relative path" = paths beginning with `experiments/...`, valid only when CWD is `/Users/richsion/Desktop/benchmark/bench`.
- Mixing the two is the most common copy-paste hazard; every snippet below states which CWD it assumes and never references the other form.

---

## 0.5 Finding status (locked-in as of 2026-04-30)

Legend: ✅ landed · ⏭️ deferred (D4 gate or out-of-scope) · 🚫 not actionable (Case B / mooted) · 🌀 §6 out of scope

| ID | Status | Commit / disposition |
|---|---|---|
| H1 (delete migration scripts) | ✅ | `b2c9454` Clean up retired stability migration code |
| H2 (Phase B → CLI subcommand) | ✅ | `217d14f` Merge human alignment extension into CLI |
| H3 (`"control"` → `"S6"`) | ✅ | `38bbba0` Fix S6 human alignment sampling keys; verified per-judge `S6.{sonnet,gpt54,gemini}.n == 12` |
| H4 (hardcoded BENCH path) | ✅ | mooted by H2 (scripts deleted) |
| H5 (`_write_llm_label_snapshot` private reach) | ✅ | mooted by H2 (helper promoted, scripts deleted) |
| M1 (panel-3 SSOT) | ✅ | `e0f8e21` Centralize panel judge labels |
| M2 (pre-index hot paths) | ✅ | `f45dd1c` validate · `9debfea` aggregate · `7ae7646` aggregate_multi_judge · `605fc64` render |
| M3 (collapse 7 table modules) | ⏭️ | D4-gated; cosmetic; ~250 LOC; defer to next session |
| M4 (collapse 5 conv renderers) | ⏭️ | D4-gated; ~300 LOC; M2.4 already eliminated the per-renderer reglob, so M4's remaining benefit is structure only |
| M5 (`cli.py` argparse rewrite) | ⏭️ | D4-gated; cosmetic |
| M6 (un-concat `LEGACY_RUBRIC_ID_BY_ID`) | ✅ | bundled into `b2c9454` |
| M7 (reimplemented script helpers) | ✅ | mooted by H2 (scripts deleted) |
| L1 (literal `records = {…}` initializer) | ✅ | `bd7ef79` Apply Wave 2B cleanup fixes |
| L2 (`_per_turn_fidelity` duplicate) | ✅ | `bd7ef79` |
| L3 (`d3_control_outputs` running counter) | ✅ | `bd7ef79` |
| L4 (`_aggregate_b1` parallel accumulators) | ⏭️ | not scheduled; bundle with M3 (both touch `analysis/report.py`) |
| L5 (single-walk per dim in report aggregators) | ⏭️ | not scheduled; bundle with M3 / L4 |
| L6 (`top_examples` heapq fix) | ✅ | `bd7ef79`; intentional behavior change — surfaces 7 high-severity S5 entries previously dropped by the 50-cap |
| L7 (`_status_block` lifecycle-safe getattr) | ✅ | `bd7ef79` |
| L8 (`_load_task` exact-stem match) | ✅ | `bd7ef79` |
| L9 (dead constants `_STABILITY_DIMENSIONS` / `_VALIDITY_DIMENSIONS` / `_SYSTEM_LABELS`) | ✅ | `b2c9454` |
| L10 (extract `flatten_evaluations_by_eval_id`) | ✅ | `c4d2031` Extract human alignment eval-id flattening helper |
| L11 (module-import `load_server_env`) | 🌀 | §6 — defer until test bootstrap is profiled |
| L12 (renderer hand-rolled JSON I/O) | 🌀 | §6 — cosmetic, defer until a behavior change forces touching the file |
| L13 (narration comments) | ✅ | `eac1d09` Remove narration-only comments |
| L14 (paper export filename manifest) | ⏭️ | not scheduled; bundle with M3 / M5 |
| L15 (collapse 3 dimension-field maps) | 🚫 | Wave 2D study — **Case B**: maps look duplicated but encode panel-aggregate vs per-judge raw-score lookups; followed up with cosmetic rename `562ee4a` instead |
| Followup #1 (basename path fields) | ✅ | `286c403` Emit basenames for human_alignment artifact path fields |
| Followup #2 (`PANEL_AGG_*` rename) | ✅ | `562ee4a` Rename panel-aggregate field maps for semantic clarity |
| Followup #3 (refresh stale live `judge_inputs/`) | ⏭️ | non-blocking; the stash-trick parity pattern works around it; refresh as a one-shot when convenient |

Tally: **22 of 27 findings landed** (H 5/5; M 5/7 + 2 mooted; L 9/15 + 4 deferred + 2 §6) plus **2 of 3 followups** landed.

Aggregate baseline now: post-fix `all_evaluations.json`, `agreement_report.json` (with `n=12` per-judge S6 block), `failure_taxonomy_stats.json` (with corrected top-N) all live. `git status` shows only pre-existing unrelated entries. Stash-mode parity has been validated as a working pattern when live `judge_inputs/` has stale prompt-body text from the pre-rename era.

---

## 0. Live-state baseline (verified 2026-04-30)

These numbers anchor every "Phase B" reference in the plan. **Do not describe the final human-alignment pool as `N=157`** — 157 is the intermediate pre-S5-addendum count; the final pool is 169.

| Metric | Current value |
|---|---|
| `sample_manifest.json:sample_count` | 169 |
| Phase A samples | 39 |
| Phase B added | 118 |
| S5 P1 addendum bump | +12 (157 → 169) |
| `human_label_template.csv` rows | 169 |
| Per-dim counts | S1=36, S2=36, S3=12, S4=37, S5=36, S6=12 |

The H3 fix verifies that `agreement_metrics.per_judge_alignment.control_distinctiveness.S6` covers all three judges with **n=12** (the S6 cell), not 157 or 169.

---

## 1. Executive summary

| # | Theme | Behavior-level acceptance | Severity |
|---|---|---|---|
| A | Delete migration scripts `rename_codes_to_sj.py` and `audit_rename.py`. Keep `LEGACY_RUBRIC_ID_BY_ID` (still used by `analysis/validate.py`) | `rg -n -e rename_codes_to_sj -e audit_rename bench --glob '*.py'` returns zero hits. Smoke-imports of cli, validate, render_judge_prompts succeed | High |
| B | Merge `extend_human_alignment_phase_b.py` + `phase_b_p1_bump.py` into `analysis/human_alignment.extend_alignment_pool` exposed via flat CLI `human-alignment-extend` subcommand (see D2 for fixed flag set) | `human-alignment-extend --help` renders. `--dry-run` against a /tmp copy of `results/main` produces shortfall report and writes nothing. Two scripts gone | High |
| C | Fix `"control"` → `"S6"` in three configs: `_PER_JUDGE_ALIGNMENT_SPECS`, `DEFAULT_SAMPLE_PLAN`, `PHASE_B_TARGET` (latter is mooted if B lands first) | After re-running `human-alignment --compute`, JSON path `agreement_metrics.per_judge_alignment.control_distinctiveness.S6` contains all three judge labels, each with `n > 0`. Stale-artifact note in §3 | High |
| D | Single source of truth for the panel-3 judges; `JUDGE_MODELS` in `core/config.py` is canonical | Targeted grep (see M1 acceptance) returns zero raw-string panel-judge ids in `.py` outside `core/config.py` and any test fixtures | Medium |
| E | Pre-index judge inputs / aggregate outputs / conversations across pipeline stages | Per-module behavioral targets in §2 — no global "3-5×" claim | Medium |
| F | Collapse the **7** per-table modules under `analysis/components/tables/` into a parameterised `KeyedScoreTable` (single-key, double-key, model+temperature variants must be supported) | Existing `report.py` regenerates byte-identical `report/*.tex` / `*.csv` / `*.html` for the 7 affected tables. Diff-driven parity, not LOC | Medium |
| G | Collapse the **5** conversation-based `render_*` functions (`render_d1/d2/d3/control/b1`) into one driver. **Do NOT include `render_p1`** — it reads `probes/responses`, different shape | Render to a `mktemp -d` output directory, structured-diff against `results/main/judge_inputs/` — zero substantive diff | Medium |
| H | Replace hardcoded absolute `BENCH = Path("/Users/richsion/...")` in Phase B scripts with `core.paths.BENCH_ROOT` | Mooted if B is executed (scripts deleted). Otherwise `rg -n 'Path\("/Users' bench/experiments/student_sim_stability` returns zero hits | High |

LOC reductions (~1300–1500) are estimates only — they are NOT pass/fail criteria.

---

## 2. High-severity findings

### H1. Delete migration scripts; keep `LEGACY_RUBRIC_ID_BY_ID`

`bench/experiments/student_sim_stability/scripts/rename_codes_to_sj.py` (511 lines) and `bench/experiments/student_sim_stability/scripts/audit_rename.py` (375 lines) are one-shot tools. The S/J rename has shipped (`54f9cb0`). `audit_rename.py:12` imports `rename_codes_to_sj`; `audit_rename.py:327` subprocess-invokes it — they're coupled, must be deleted together.

**Correction over earlier draft**: `core/rubrics.py:LEGACY_RUBRIC_ID_BY_ID` is **NOT dead**. `analysis/validate.py:42, 534-535` imports and uses it as a backwards-compat shim — when validating that a judge prompt embeds the rubric_id text, the validator accepts either the new id or the legacy id. Prompts rendered before the rename still embed the old code; the shim is load-bearing.

The string-concat tricks (`"D" "1_..."`) inside `LEGACY_RUBRIC_ID_BY_ID` were inserted only to dodge `audit_rename.py`'s leak detector. Once that script is gone they can be reverted to plain literals (cosmetic, finding M6).

**Acceptance**:
- `rg -n "rename_codes_to_sj|audit_rename" bench --glob '*.py'` → zero hits
- `rg -n "rename_codes_to_sj|audit_rename" bench --glob '*.md'` may return hits (docs only — non-blocking)
- `cd bench && PYTHONPATH=. python -c "import experiments.student_sim_stability.cli"` succeeds, same for `analysis.validate` and `pipeline.render_judge_prompts`

---

### H2. Phase B scripts → CLI subcommand

Two scripts duplicate ~70%:
- `bench/experiments/student_sim_stability/scripts/extend_human_alignment_phase_b.py` (228 lines)
- `bench/experiments/student_sim_stability/scripts/phase_b_p1_bump.py` (151 lines)

Both hardcode `BENCH = Path("/Users/richsion/Desktop/benchmark/bench")`, reach into the private `_write_llm_label_snapshot`, reimplement atomic-write patterns when `core/io_utils.atomic_write_json` exists, and reimplement `_stratified_sample`. Per D2 below, these become one `extend_alignment_pool` function exposed as a single new CLI subcommand `human-alignment-extend` with a fixed flag set.

**Acceptance**: see D2 below. If a companion prompt file has been persisted (§9), it should mirror D2 exactly; D2 remains authoritative if the prompt text diverges.

---

### H3. `"control"` vs `"S6"` mismatch — confirmed silent correctness bug

**Verification trail** (run yourself before editing):

- Conversion-rendering writes `"dimension": "S6"` (`pipeline/render_judge_prompts.py:453`)
- Stored inputs match (sampled `bench/experiments/student_sim_stability/results/main/judge_inputs/S6__*.json` has `"dimension": "S6"`)
- `human_label_template.csv` rows for control distinctiveness have `dimension == "S6"`
- Runtime checks at `analysis/human_alignment.py:736` and `pipeline/aggregate.py:325` test `== "S6"`

But three configs key on the legacy plan-name `"control"`:

| Where | Locate via | Effect of mismatch |
|---|---|---|
| `_PER_JUDGE_ALIGNMENT_SPECS` last tuple | `rg -n '_PER_JUDGE_ALIGNMENT_SPECS' bench/experiments/student_sim_stability/analysis/human_alignment.py` | `_compute_per_judge_alignment` reads CSV `dim == "S6"`, calls `dim_to_judge_field.get("S6")` → returns `None` → `control_distinctiveness` per-judge bucket silently empty |
| `DEFAULT_SAMPLE_PLAN["control"]` | `rg -n 'DEFAULT_SAMPLE_PLAN' bench/experiments/student_sim_stability/analysis/human_alignment.py` | `_stratified_sample` reads `dim = payload.get("dimension")` → `"S6"`; plan iteration on key `"control"` finds zero candidates |
| `PHASE_B_TARGET["control"]` | `rg -n 'PHASE_B_TARGET' bench/experiments/student_sim_stability/scripts/extend_human_alignment_phase_b.py` | Same shape; mooted if H2 lands first (script deleted) |

**Stale-artifact warning**: the CURRENT on-disk `agreement_report.json` already shows an `S6` block under `control_distinctiveness`. This is misleading — it was written before the per-judge change shipped. With current source code, `_compute_per_judge_alignment(...)` returns `null` for `control_distinctiveness`. Acceptance must therefore require **rerunning** with `--compute` after the source fix, not "look at the artifact".

**CRITICAL — name disambiguation**: there are two distinct uses of `"control"` in this codebase. Do NOT touch (b):

(a) The S6 dimension (this is the bug — these references are wrong: the three configs above)
(b) The control-phase / generic-student condition: `metadata.phase == "control"`, `S2__control__...` eval_ids, `live__` / `control__` filename prefixes in `render_judge_prompts.py`, `_aggregate_control` in `report.py`. **Load-bearing — leave alone.**

**Acceptance** (smoke-into-/tmp pattern; live run is opt-in):

```bash
# Step 1: mint a temp results copy (CWD = repo root)
cd /Users/richsion/Desktop/benchmark
export SMOKE_DIR="$(mktemp -d /tmp/sss_h3_smoke.XXXXXX)/main"
cp -R bench/experiments/student_sim_stability/results/main "$SMOKE_DIR"

# Step 2: recompute against the copy, NOT live (CWD = bench)
cd /Users/richsion/Desktop/benchmark/bench
PYTHONPATH=. python -m experiments.student_sim_stability.cli human-alignment \
    --output-dir "$SMOKE_DIR" --compute

# Step 3: inspect the per-judge block in the copy
PYTHONPATH=. python <<'PY'
import json, os
p = os.environ["SMOKE_DIR"] + "/human_alignment/agreement_report.json"
d = json.load(open(p))
block = d["agreement_metrics"]["per_judge_alignment"].get("control_distinctiveness")
print(json.dumps(block, indent=2, ensure_ascii=False))
assert block, "control_distinctiveness is null/missing"
assert "S6" in block, "no S6 key under control_distinctiveness"
expected = {"sonnet", "gpt54", "gemini"}
assert set(block["S6"]) == expected, f"judge set mismatch: {set(block['S6'])} != {expected}"
for judge in expected:
    n = block["S6"][judge].get("n", 0)
    assert n > 0, f"{judge} has n={n} (expected > 0; baseline n=12)"
print("H3 acceptance OK")
PY
```

The printed block must contain `S6` keying a dict whose keys are exactly `{sonnet, gpt54, gemini}`, each carrying `n > 0` (expected `n == 12` per the §0 baseline). The strict set-equality is intentional: a partial match (e.g. only one judge present) must fail loudly.

**Forbidden**:

- Do not run bare `human-alignment` without `--compute` — it triggers `init_human_alignment` which reinitialises `sample_manifest.json` and overwrites the manifest.
- Do not run the recompute against `results/main` during smoke. `--compute` mutates `agreement_report.json` and `disagreement_examples.md` in the supplied results dir; smoke must go to the /tmp copy.
- Do not omit the `export` on `SMOKE_DIR`. The Python heredoc is a child process and reads the variable from the environment.

**Live opt-in**: once the smoke acceptance is confirmed on the /tmp copy, the user may rerun the recompute against the live results dir to refresh the artifact. From `/Users/richsion/Desktop/benchmark/bench`, the unambiguous live command is:

```bash
cd /Users/richsion/Desktop/benchmark/bench
PYTHONPATH=. python -m experiments.student_sim_stability.cli human-alignment \
    --output-dir /Users/richsion/Desktop/benchmark/bench/experiments/student_sim_stability/results/main \
    --compute
```

(Equivalently: omit `--output-dir` to use the configured default.) Do NOT pass `--output-dir bench/experiments/...` from CWD `bench/` — `_results_dir` would resolve it under `BENCH_ROOT` and produce `bench/bench/...`. That live run is the user's call, not part of automated acceptance.

---

### H4 / H5. Hardcoded absolute paths + private API reach

Both moot once H2 lands (scripts deleted). If H2 is for some reason deferred, these become explicit findings.

---

## 3. Medium-severity findings

### M1. Single source of truth for panel-3 judges

`core/config.py:JUDGE_MODELS` is canonical. Six places independently encode the panel-3 model ids or short labels:

- `pipeline/aggregate.py:135-136` — `_PANEL_3_SECONDARY` / `_PANEL_3_TERTIARY`
- `pipeline/aggregate.py` `_merge_panel_3_scores` — repeats the triple inline 6×
- `pipeline/aggregate_multi_judge.py:44-46` — `SONNET_KEY` / `GPT54_KEY` / `GEMINI_KEY`
- `analysis/human_alignment.py` — 5+ literal iterations of `("sonnet","gpt54","gemini")`

Required design:

```python
# core/config.py (after JUDGE_MODELS line)
JUDGE_LABELS: tuple[str, str, str] = ("sonnet", "gpt54", "gemini")
if len(JUDGE_MODELS) != 3 or len(JUDGE_LABELS) != 3:
    raise RuntimeError("panel-3 SSOT invariant violated")
JUDGE_LABEL_BY_MODEL_ID: dict[str, str] = dict(zip(JUDGE_MODELS, JUDGE_LABELS))
PANEL_JUDGES: tuple[tuple[str, str], ...] = tuple(zip(JUDGE_MODELS, JUDGE_LABELS))

def judge_label(model_id: str) -> str:
    """Map a fully-qualified model id to its short panel label."""
    try:
        return JUDGE_LABEL_BY_MODEL_ID[model_id]
    except KeyError as exc:
        raise ValueError(f"unknown panel judge: {model_id!r}") from exc
```

The invariant uses an explicit `raise RuntimeError`, not `assert` — `python -O` strips asserts and would silently bypass the check on a misconfigured panel.

All on-disk dir derivations go through `safe_model_dir(model_id)`. `_merge_panel_3_scores` iterates `PANEL_JUDGES` instead of writing the three model ids inline.

**Acceptance** — split into two distinct grep policies:

(a) **Strict — model IDs and on-disk dir names must be centralised.** This grep should return only the SSOT in `core/config.py`:

```bash
rg -n 'anthropic/claude-sonnet|openai/gpt-5\.4|google/gemini|anthropic__claude-sonnet|openai__gpt-5_4|google__gemini' \
  bench/experiments/student_sim_stability --glob '*.py'
```

Allowed: `bench/experiments/student_sim_stability/core/config.py`. Anything else is a regression — fix it.

(b) **Advisory — short labels (`sonnet`/`gpt54`/`gemini`) may legitimately survive as JSON output-schema keys** in `agreement_report.json`, per-judge metric dicts, table column headers, and similar emit-side code. They should NOT be eliminated indiscriminately. The grep:

```bash
rg -n '"sonnet"|"gpt54"|"gemini"' bench/experiments/student_sim_stability --glob '*.py'
```

For each surviving hit, classify:
- ✅ **OK**: derived from `JUDGE_LABELS` / `PANEL_JUDGES` / `judge_label(...)` via iteration; or used as a documented JSON schema key (e.g. `{"sonnet": ..., "gpt54": ..., "gemini": ...}` shape that downstream readers depend on).
- ❌ **Fix**: hand-rolled iteration `for j in ("sonnet", "gpt54", "gemini")` or hard-coded dict literal where iteration via `JUDGE_LABELS` would be equivalent.

The Codex prompt for M1 will require an explicit classification list for every advisory-grep hit, not blanket removal.

---

### M2. Pre-index hot paths — module-specific behavioral targets

The earlier "3–5× JSON parse reduction" estimate was sloppy. Codex audit noted that `_validate_judge_panel_outputs` already builds `input_hash_index`. The real per-module wins:

| Module | Specific behavior change | Verifiable acceptance |
|---|---|---|
| `analysis/validate.py` | `_validate_judge_output_metadata(input_dir, output_dir)` for primary `judge_outputs/` does NOT receive `input_hash_index` and re-reads inputs. `_validate_judge_input_metadata` parses the same files independently. Build one `metadata_by_eval` at `validate()` top, pass to all consumers | A `validate()` run reads each `judge_inputs/*.json` at most **once**. Verify by counting `load_json` calls under instrumentation, or by cprofile, or by reading the diff. Preserve all check-pass/fail outputs |
| `pipeline/aggregate.py` | Per-output `secondary_path.exists()` + `load_json` is N+1 + TOCTOU. Pre-glob `secondary_by_name` / `tertiary_by_name` once, then dict-lookup | No more `if path.exists(): load_json(path)` pattern in the per-output loop |
| `pipeline/aggregate_multi_judge.py` | `_by_eval_across_judges` globs each judge dir 6× (once per dim). Glob each dir once, partition by prefix in Python | Glob count drops 18 → 3 |
| `pipeline/render_judge_prompts.py` | Each renderer does its own `glob("*.json")` over `conv_dir`. Each conversation file is opened 4–5× across one full render. Build `load_conversations(conv_dir) -> dict[str, dict]` once, pass through | Each conversation JSON is parsed at most once per all-dimension render |

Parity: see §5 Wave 3 — diff against existing artifacts for the affected stage; do NOT use `cli.py all`.

---

### M3. Collapse the **7** per-table modules

Codex audit: there are 7, not 6 (the original report missed `d1_by_model_persona.py`, which is instantiated in `report.py:_build_components`):

```
analysis/components/tables/d1_by_model.py            (single key: model)
analysis/components/tables/d1_by_model_persona.py    (composite: model__persona)
analysis/components/tables/d1_by_persona.py          (single key: persona)
analysis/components/tables/d1_by_task.py             (single key: task)
analysis/components/tables/d2_by_model.py            (single key: model)
analysis/components/tables/d2_by_model_temp.py       (composite: model__tutor_temperature)
analysis/components/tables/d3_drift.py               (single key, drift-specific columns)
```

`KeyedScoreTable` must accept:

- `key_label`, `score_label` (string)
- `key_split` (None for single-key, `"__"` for composite)
- `columns` — list of column descriptors: each `(name, kind)` where kind is `mean | ci_low | ci_high | std | n | derived`
- `row_builder` (callable) — fallback for non-uniform rows; `d3_drift.py` likely needs this

**Acceptance** (Wave 3 — defer; see D4):

```bash
# CWD = repo root: mint a temp results copy
cd /Users/richsion/Desktop/benchmark
export SMOKE_DIR="$(mktemp -d /tmp/sss_m3_smoke.XXXXXX)/main"
cp -R bench/experiments/student_sim_stability/results/main "$SMOKE_DIR"

# CWD = bench: regenerate the report INTO the copy, NOT live
cd /Users/richsion/Desktop/benchmark/bench
PYTHONPATH=. python -m experiments.student_sim_stability.cli report \
    --output-dir "$SMOKE_DIR" --skip-validate

# Compare only the 7 affected table component outputs, not the entire report tree.
set -e
for stem in \
  d1_by_model d1_by_model_persona d1_by_persona d1_by_task \
  d2_by_model d2_by_model_temp d3_drift
do
  for ext in html csv tex
  do
    diff -u \
      "experiments/student_sim_stability/results/main/report/components/${stem}.${ext}" \
      "$SMOKE_DIR/report/components/${stem}.${ext}"
  done
done
```

Diff must be empty for the 7 affected `.tex` / `.csv` / `.html` outputs. Do not require the entire report tree to be byte-identical for this M3-only refactor unless the PR explicitly claims full report parity.

---

### M4. Collapse **5** conversation-based renderers; `render_p1` excluded

`render_d1`, `render_d2`, `render_d3`, `render_control`, `render_b1` all share: `output_dir.mkdir`, conversation glob, parse filename meta, `_persona_block_kwargs`, format prompt, write JSON, count, print final.

`render_p1` reads `probes/responses` (not conversations) — DIFFERENT shape. Do NOT fold it in. Either:

(a) Limit M4 to the 5 conversation-based renderers, leave `render_p1` standalone. **Recommended.**
(b) Build two drivers: `render_from_conversations(...)` and `render_from_probe_responses(...)`.

The old executive-summary "6 functions" line was wrong; the doc now says 5.

**Acceptance** (Wave 3):

```bash
cd /Users/richsion/Desktop/benchmark/bench
SMOKE_DIR="$(mktemp -d /tmp/sss_render_smoke.XXXXXX)"
PYTHONPATH=. python -m experiments.student_sim_stability.pipeline.render_judge_prompts \
  --conv-dir results/main/conversations \
  --output-dir "$SMOKE_DIR/judge_inputs_after" \
  --dimension all \
  --s1-sample-policy live-r0-tt0
```

Then structurally diff `$SMOKE_DIR/judge_inputs_after` against `results/main/judge_inputs/` (key-set equality + per-key JSON deep-equal).

Parity scope:

- **S1, S2, S3, S4, S6** (the 5 collapsed conversation-renderers): zero substantive diff expected. Driver must preserve byte-equivalent JSON.
- **S5** (P1): zero substantive diff expected because `render_p1` is **unchanged**. The full-dimension render must still produce identical S5 outputs; if S5 differs, that means M4 accidentally touched `render_p1` or shared state — a regression.

So the diff scope is `--dimension all`, but the expectation is "zero diff everywhere", with two distinct rationales (driver preserves; render_p1 untouched).

---

### M5. `cli.py` argparse rewrite

Cosmetic. ~250-line if/elif chain → `set_defaults(func=...)`. Defer per D4.

---

### M6. Cosmetic literal cleanup in `LEGACY_RUBRIC_ID_BY_ID`

After H1 deletes the audit script, the `"D" "1_..."` etc. tricks can be reverted to plain `"D1_..."` literals. The dict itself stays (used at `validate.py:534-535`).

---

### M7. Reimplemented helpers in scripts

Mostly mooted by H2 (script deletion). Anything that survives is logged in §4.

---

## 4. Lower-severity findings (locate via symbol; do not depend on absolute line numbers)

| ID | Locate via | Fix |
|---|---|---|
| L1 | `rg -n 'records.*= \{' bench/experiments/student_sim_stability/pipeline/aggregate.py` (matches both annotated and bare initializers) | Replace literal dict initializer with `{dim: [] for dim in DIMENSION_TO_FILE}` |
| L2 | `rg -n '_per_turn_fidelity\b' bench/experiments/student_sim_stability/pipeline/aggregate.py` | Delete `_per_turn_fidelity`; replace its single call site with `_per_turn_field(scores, "persona_fidelity")` |
| L3 | `rg -n 'd3_control_outputs' bench/experiments/student_sim_stability/pipeline/aggregate.py` | Drop the running counter; derive at the end via list comprehension |
| L4 | `rg -n '_aggregate_b1' bench/experiments/student_sim_stability/analysis/report.py` | Collapse 8+ parallel accumulators into a single per-record list of dicts; group via `Counter`/`groupby` |
| L5 | `rg -n -e _aggregate_d1 -e _aggregate_d2 -e _aggregate_d3 -e _aggregate_control -e _aggregate_p1 -e _aggregate_b1 -e _aggregate_failure_taxonomy bench/experiments/student_sim_stability/analysis/report.py` | Single walk per dimension; share `score_field` cache |
| L6 | `rg -n 'top_examples' bench/experiments/student_sim_stability/analysis/report.py` | `import heapq`; rename intermediate to `candidate_examples`; key handles `None` and preserves current descending severity semantics |
| L7 | `rg -n '_status_block' bench/experiments/student_sim_stability/analysis/report.py` | Use `getattr(self, "_metadata", {}) or {}` (and `_agreement` / `_human`) — do not assume lifecycle |
| L8 | `rg -n '_load_task' bench/experiments/student_sim_stability/pipeline/render_judge_prompts.py` | Build `{stem: path}` index once; replace `task_id in f.stem` with exact equality |
| L9 | `rg -n -e _STABILITY_DIMENSIONS -e _VALIDITY_DIMENSIONS -e _SYSTEM_LABELS bench/experiments/student_sim_stability` | Delete (zero callers, verified) |
| L10 | `rg -n -e aggregate_by_eval -e by_eval_id bench/experiments/student_sim_stability/analysis/human_alignment.py` | Extract `flatten_evaluations_by_eval_id(aggregate)` |
| L11 | `rg -n load_server_env bench/experiments/student_sim_stability` | Move out of import-time into `main()`; only worth doing if test bootstrap is slow |
| L12 | various `with open(...) as f: json.load(f)` in renderers | Route through `core/io_utils.load_json` / `atomic_write_json` (cosmetic; do behind parity check) |
| L13 | per-comment list (see commit message in fix PR) | Delete WHAT/WHEN narration comments only; keep WHY |
| L14 | `rg -n -e _paper_export -e paper-export bench/experiments/student_sim_stability/cli.py` | Derive 23-filename manifest from Component registry |
| L15 | `rg -n -e _PER_JUDGE_ALIGNMENT_SPECS -e KNOWLEDGE_FIELD_BY_DIMENSION -e EMOTIONAL_FIELD_BY_DIMENSION bench/experiments/student_sim_stability/analysis/human_alignment.py` | **Study first** — if the three maps encode the same semantics, collapse. If they encode different semantics (e.g. one is per-judge field, another is primary score field), record findings and **do not** change code |

All `rg` patterns above use `-e` per pattern instead of regex alternation, since piped regexes inside markdown table cells require backslash-escaping that ripgrep does not interpret as alternation.

---

## 5. Wave plan & parity strategy

Each wave ships as a separate PR. **Never run `cli.py all` for parity** — it can trigger live LLM calls. **Never run parity commands directly against the live `results/main` directory** — every CLI subcommand below writes its outputs back into the results dir, so a failed refactor would clobber the artifact you were trying to compare against.

CLI helper: `cli.py:_add_output_arg` registers `--output-dir` on every subcommand below; pair it with a copied results directory to keep live artifacts untouched. Note `cli.py:_results_dir` resolves relative `--output-dir` paths under `BENCH_ROOT` — always pass an absolute path to avoid confusion.

Standard parity pattern (CWD progression is explicit):

```bash
# Step 1: mint a temp results copy (CWD = repo root)
cd /Users/richsion/Desktop/benchmark
export SMOKE_DIR="$(mktemp -d /tmp/sss_parity.XXXXXX)/main"
cp -R bench/experiments/student_sim_stability/results/main "$SMOKE_DIR"

# Step 2: re-run the affected stage AGAINST THE COPY (CWD = bench)
cd /Users/richsion/Desktop/benchmark/bench
PYTHONPATH=. python -m experiments.student_sim_stability.cli <STAGE> \
    --output-dir "$SMOKE_DIR" [stage-specific flags]

# Step 3: structured diff (still CWD = bench; use bench-relative `experiments/...`)
diff -r experiments/student_sim_stability/results/main "$SMOKE_DIR"
# or, for a single JSON, deep equality:
PYTHONPATH=. python -c 'import json,sys; a=json.load(open(sys.argv[1])); b=json.load(open(sys.argv[2])); assert a==b' \
    experiments/student_sim_stability/results/main/<file> "$SMOKE_DIR/<file>"
```

`SMOKE_DIR` is `export`ed so child Python processes see it via `os.environ["SMOKE_DIR"]`. Without `export`, only the parent shell sees it.

| Stage | Stage-specific flags | Files to compare |
|---|---|---|
| `aggregate` | none | `evaluations/all_evaluations.json` (deep equality) |
| `report` | `--skip-validate` (defined at `cli.py:251`) | `report/` (recursive `diff -r` against the live tree, bench-relative) |
| `render_judge_prompts` (module entrypoint) | `--conv-dir results/main/conversations --output-dir <SMOKE_DIR/judge_inputs_after> --dimension all --s1-sample-policy live-r0-tt0` | `judge_inputs/` (structured JSON diff per file) |
| `validate` | `--profile full --json` | summary + per-check status (validate is read-only — `--output-dir` only for results path) |
| `human-alignment --compute` | none (mutates `agreement_report.json` and `disagreement_examples.md` inside the output dir) | for H3 acceptance, inspect the `S6` block under `agreement_metrics.per_judge_alignment.control_distinctiveness`; for general parity, deep-equal the full report JSON |

Live mutation policy:

- **Smoke / parity runs always go through the temp-copy pattern above**, never against `results/main` directly.
- The H3 final acceptance run is the **one** exception: after the source fix is verified on a /tmp copy and the per-judge `S6` block is populated, the user may rerun `human-alignment --compute` against `results/main` to refresh the live artifact. That live run is opt-in, executed by the user, and not part of any prompt's auto-acceptance.

### Wave 1 — Deletes & merges

1. **H1 + L9 + M6**: `git rm` migration scripts; delete dead constants; un-concat `LEGACY_RUBRIC_ID_BY_ID` literals
2. **H3**: three-key rename `"control"` → `"S6"`. Acceptance: regenerate `human-alignment --compute` **against a `/tmp` results copy via `--output-dir`**, then assert `agreement_metrics.per_judge_alignment.control_distinctiveness.S6` contains exactly `{sonnet, gpt54, gemini}` with each `n > 0`. Live artifact refresh is user opt-in only after the smoke acceptance passes
3. **H2 + H4**: merge Phase B scripts into `extend_alignment_pool` + flat CLI subcommand; delete scripts; verify with `--dry-run` against `/tmp` copy
4. **L13**: comment cleanup

### Wave 2 — Correctness & SSOT

5. **M1**: panel-3 SSOT in `core/config.py` + replace all literals
6. **L1, L2, L3, L6, L7, L8**: batched small fixes
7. **L10**: extract `flatten_evaluations_by_eval_id`
8. **L15**: **study only**; collapse iff three maps confirmed identical, otherwise document and stop

### Wave 3 — Larger refactors (DEFERRED per D4)

9. **M2**: pre-index hot paths
10. **M3**: collapse 7 table modules → `KeyedScoreTable`
11. **M4**: collapse 5 conversation renderers → driver
12. **M5**: cli.py argparse rewrite

Wave 3 should be scheduled in a separate session **after** Wave 1+2 stabilise and the H3 acceptance is observed. Wave 3 prompts must include explicit user-confirmation gates — Codex must ask the user before starting any of M2/M3/M4/M5.

---

## 6. Out of scope

- L11 (module-import env-load): deferred unless test bootstrap is profiled and shows the cost
- L12 (renderer hand-rolled JSON I/O): cosmetic; defer until a behavior change forces touching the file
- All `_load_task` / `json.dumps(recent)` micro-opts: research-acceptable

---

## 7. Stats (estimates only, NOT acceptance)

| Category | Count | LOC estimate |
|---|---|---|
| High-severity (H1–H5) | 5 | ~1,270 |
| Medium-severity (M1–M7) | 7 | ~600 |
| Low-severity (L1–L15) | 15 | ~150 |
| **Total** | **27** | **~2,000** |

Acceptance is **always behavioral**: imports succeed, targeted parity diffs are zero, behavior-level checks pass. Do not gate any wave on LOC counts.

---

## 8. Decisions (locked in)

These shape the executable prompts if/when they are persisted in the companion file (§9). Reversing any decision requires rewriting the corresponding prompt text.

**D1. Migration scripts → hard-delete.** `git rm` both. No archive directory. Git history is the recovery path. Repo-root paths in the actual command:

```bash
git rm bench/experiments/student_sim_stability/scripts/rename_codes_to_sj.py
git rm bench/experiments/student_sim_stability/scripts/audit_rename.py
```

**D2. Phase B helpers → fixed CLI shape.** Add a single new flat subcommand `human-alignment-extend` with this exact flag set (no alternatives, no `--phase-b`/`--p1-bump` toggles, no "Codex picks the cleaner fit"):

```text
human-alignment-extend
  --target              SPEC               see semantics below
  --key-fields          F1,F2[,F3]         cell key composition
                                           (default: dimension,persona_id,model)
  --dimension           DIM                optional: restrict candidates to one dimension
  --seed                INT                default 2026
  --output-dir          PATH               default: experiments results dir; tests use a /tmp copy
  --dry-run                                print shortfall report; write nothing
```

`--target` semantics — locked, unambiguous:

- Without `--dimension`, `--target` MUST be the comma-form `DIM=N[,DIM=N…]` (e.g. `S1=3,S3=1,S6=1`). Each `DIM` selects the per-cell quota for that dimension. The dimension key in `--target` is independent of `--key-fields`: it always identifies which dimension's cells the quota applies to, even when `dimension` is excluded from `--key-fields`.
- With `--dimension DIM` set, `--target` may be either:
  - the scalar form `--target N` (applies to the chosen dimension), or
  - the comma form `--target DIM=N` where `DIM` MUST equal the value of `--dimension`. Mismatch is an error.
- Inside `extend_alignment_pool`, after parsing, build a single canonical `target_per_cell: dict[str, int]` keyed by dimension. Cell aggregation (counting `existing` per cell using `key_fields`) and dimension-quota lookup are independent operations.

Validation (must raise with a clear message — never silent):

- duplicate `DIM` in `--target` → reject
- `DIM` outside `{S1,S2,S3,S4,S5,S6}` → reject
- empty candidate pool for any (dimension, cell) where `target > existing` → emit a warning to stderr listing the affected cells; do not silently zero-fill
- `--dry-run` MUST NOT call `write_llm_label_snapshot`, MUST NOT write any manifest, CSV, or snapshot file

The `phase_b_p1_bump.py` use case is reproduced via:

```bash
human-alignment-extend --dimension S5 --target 3 --seed 2027 \
  --key-fields persona_id,model --dry-run
```

(Or equivalently `--target S5=3`; the `DIM=N` form is also accepted with `--dimension S5`.)

`extend_alignment_pool` in `analysis/human_alignment.py` is the single implementation; `_write_llm_label_snapshot` is promoted to public `write_llm_label_snapshot` in the same change.

**D3. `LEGACY_RUBRIC_ID_BY_ID` → keep indefinitely.** Used at `validate.py:534-535` as a backwards-compat shim. M6 is reduced to a literal-cosmetic change. Revisit only if a future cleanup pass surfaces it.

**D4. Wave 3 (M2 / M3 / M4 / M5) → deferred.** Do not execute in this Codex run. Any Wave 3 prompt must be marked OPTIONAL and must instruct Codex to ask the user before starting. Rationale: M3/M4/M5 touch test surface; running them after Wave 1+2 stabilise gives a clean baseline.

**D5. Path discipline.** Every command must state or imply its CWD and use the matching path form: repo-root paths (`bench/...`) from `/Users/richsion/Desktop/benchmark`, bench-relative paths (`experiments/...`) from `/Users/richsion/Desktop/benchmark/bench`. Never use ambiguous `scripts/X.py` paths. Markdown links to source files are intentionally minimal in this doc — `bench/*MD/v7.0/` is two levels below repo root and `[text](bench/...)` would resolve incorrectly. When the doc references a file, it uses the path verbatim in code blocks.

**D6. Verification discipline.**

- `human-alignment` is **always** invoked with `--compute` for verification. Bare `human-alignment` reinitialises sample artifacts.
- `cli.py all` is **never** used for parity — it may trigger live LLM calls.
- Parity / smoke runs always go through a `mktemp -d` /tmp copy via `--output-dir`. Live `results/main` is never the target of an automated parity run.
- The H3 final acceptance is the **one** sanctioned live mutation, opt-in by the user, and only after the smoke parity has passed against a /tmp copy.
- Acceptance evidence is JSON snippets (printed via `python -c` or `jq`), not screenshots.
- After Wave 1A lands, line numbers shift. Subsequent work must locate code via symbols + `rg`, not absolute line numbers.
- `rm -rf` on temp paths is replaced with `mktemp -d /tmp/sss_<purpose>.XXXXXX` so no fixed-path collision is possible. If a prompt does delete an existing temp dir, it must guard with `test "$X" != "/tmp"` first.

---

## 9. Companion prompt file

The 9 self-contained English Codex prompts are delivered out-of-band (typically pasted into a fresh Codex conversation, one at a time). The reserved companion-file path is:

```text
bench/*MD/v7.0/code_review_student_sim_stability_codex_prompts_2026-04-30.md
```

If the companion file does not yet exist on disk, the prompts have not been persisted; ask the maintainer for the latest set before delegating to a fresh Codex conversation. The table below is still sufficient for in-repo planning, but it is not a paste-ready prompt set by itself.

| Wave | Prompt scope | Status |
|---|---|---|
| 1A | H1 + L9 + M6 (deletes & dead-code cleanup) | Active |
| 1B | H3 (silent correctness bug; smoke against /tmp copy) | Active |
| 1C | H2 + H4 (Phase B scripts → CLI subcommand) | Active |
| 1D | L13 (comment cleanup) | Active |
| 2A | M1 (panel-3 SSOT) | Active |
| 2B | L1/L2/L3/L6/L7/L8 (small batch) | Active |
| 2C | L10 + L15 study | Active |
| 3 | M2 / M3 / M4 / M5 (deferred, gated) | **Gated by D4** |

After Waves 1A–1D land, regenerate human-alignment via the H3 smoke pattern and confirm acceptance — that is the highest-confidence signal that nothing load-bearing broke.

---

*Reviewers*: code-reuse pass, code-quality pass, efficiency pass (3 parallel agents) + 3 strict-review passes.
*Aggregator*: this report.
