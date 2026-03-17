# B-Series Scoring Audit Checklist

**Date**: 2026-03-13
**Agent**: openai / gpt-5.2
**Tasks**: B01--B06 (backtest category), 3 personas each = 18 evaluations
**Eval model**: anthropic/claude-sonnet-4.6 via OAuth

---

## 1. Summary Table

| Task | Persona | OAS | QR | QP | Tutor | Key Issues |
|------|---------|------|------|------|-------|------------|
| B01 | beginner | 0.654 | 0.799 | 0.669 | 0.462 | Tutor 7D fallback low |
| B01 | intermediate | 0.784 | 0.804 | 0.673 | 0.891 | OK |
| B01 | advanced | 0.639 | 0.349 | 0.650 | 0.957 | Judge mischaracterizes advanced tutoring |
| B02 | beginner | 0.674 | 0.804 | 0.716 | 0.467 | Tutor 7D fallback low |
| B02 | intermediate | 0.653 | 0.544 | 0.586 | 0.838 | Code goes bankrupt; eval_script blind |
| B02 | advanced | 0.685 | 0.660 | 0.698 | 0.710 | `strategy_isolated` false negative |
| B03 | beginner | 0.659 | 0.689 | 0.714 | 0.567 | Tutor 7D fallback low |
| B03 | intermediate | 0.767 | 0.692 | 0.714 | 0.924 | OK |
| B03 | advanced | 0.559 | 0.657 | 0.634 | 0.362 | Content repetition (justified low) |
| B04 | beginner | 0.657 | 0.687 | 0.731 | 0.543 | Tutor 7D fallback low; CSV col match |
| B04 | intermediate | 0.748 | 0.615 | 0.746 | 0.914 | CSV col match |
| B04 | advanced | 0.773 | 0.697 | 0.737 | 0.905 | CSV col match |
| B05 | beginner | 0.665 | 0.778 | 0.697 | 0.505 | Tutor 7D fallback low |
| B05 | intermediate | 0.796 | 0.755 | 0.719 | 0.938 | OK |
| B05 | advanced | 0.742 | 0.609 | 0.688 | 0.962 | Judge misreads negative-equity strategy |
| B06 | beginner | 0.589 | 0.663 | 0.738 | 0.319 | Tutor 7D fallback severely low |
| B06 | intermediate | 0.722 | 0.619 | 0.682 | 0.900 | OK |
| B06 | advanced | 0.783 | 0.768 | 0.671 | 0.929 | OK |

---

## 2. Systemic Issues

### 2.1 Eval Script: Heredoc Detection -- FIXED

**Status**: B02--B06 eval scripts now include `_tool_log_code_text()` which extracts Python
code from `shell_exec` command strings, `shell_exec` result strings, and `file_write` content.
All structural checks (layer detection, replay patterns, model presence) use
`all_code = code_text + "\n" + tool_code`, falling back from workspace `.py` to tool-log code.

**Remaining gap -- B04 `per_asset_accounting_present` CSV column matching**:
The CSV fallback in `workspace_has_csv_column_group()` requires exact column names from
fixed lists: `["asset", "symbol"]`, `["position", "qty"]`, `["pnl", "realized_pnl", "unrealized_pnl"]`.
Agents commonly use prefixed naming (`BTCUSDT_qty`, `BTCUSDT_realized_pnl`, `pnl_btc$`),
which does not match. The code-path fallback (`has_regex(all_code, ...)`) partially compensates
by checking for `positions? = {` patterns, but agents using flat dict keys or DataFrame columns
without dict-of-dicts patterns still fail this check.

**Impact**: The code-path regex patterns in `per_asset_accounting_present` now cover most
heredoc cases. The CSV column matching remains overly strict for prefix-named columns.

**Recommendation**: Add substring/prefix matching to `workspace_has_csv_column_group()`:
any column containing `qty` or `position` alongside any column containing `pnl` should satisfy
the group requirement.

### 2.2 Tutor 7D Beginner Fallback Scoring -- OPEN

**Problem**: All 6 beginner personas show anomalously low Tutor 7D scores (0.319--0.567)
despite strong trace evidence of level-appropriate tutoring. All report "21 dimension
evaluation(s) used fallback recovery."

| Task | beginner Tutor | intermediate Tutor | advanced Tutor |
|------|---------------|-------------------|---------------|
| B01 | **0.462** | 0.891 | 0.957 |
| B02 | **0.467** | 0.838 | 0.710 |
| B03 | **0.567** | 0.924 | 0.362* |
| B04 | **0.543** | 0.914 | 0.905 |
| B05 | **0.505** | 0.938 | 0.962 |
| B06 | **0.319** | 0.900 | 0.929 |

*B03 advanced is genuinely low due to content repetition.

Dimensions most affected: D1 (level_detection), D2 (language_adaptation),
D3 (scaffolding_calibration), D6 (empathetic_response) -- typically 0.20--0.40 range.

**Root cause analysis**: The fallback mechanism in `tutor_conv_geval.py` has two layers:
1. **Layer 1**: Regex extraction of score from malformed JSON/prose (`_extract_score_from_prose`)
2. **Layer 2**: Reconstruct prompt and call `a_generate()` with robust JSON parser (`_fallback_direct_eval`)

When Layer 2's `_extract_json_from_response` returns `{}` (complete parse failure),
the score defaults to `parsed.get("score", 5)` = 5/10 = 0.50. This "default to 5" bias
pulls scores toward 0.50 when fallback extraction fails completely.

**Why beginner is disproportionately affected**: Beginner conversations are typically longer
(more scaffolding, more analogies, more turn-by-turn explanation), producing larger prompts
for the judge LLM. Longer prompts increase the probability of:
- The LLM returning verbose non-JSON reasoning before the score
- Token limit truncation corrupting the JSON structure
- Both retry attempts failing, falling through to the default-5 path

Intermediate and advanced conversations, being more concise, parse successfully more often.

**Estimated impact**: Beginner OAS underscored by 0.05--0.12 per task.

**Recommendation**: Replace `parsed.get("score", 5)` with `None` and exclude failed
evaluations from averaging rather than injecting a default score. Alternatively, increase
`_MAX_RETRIES` from 2 to 4 for longer conversations.

### 2.3 Code Eval Layer C SKIP = 0 -- OPEN

When no reference data exists, Layer C (Output Verification, weight 50%) contributes 0.
Even perfect Layer A + Layer B yields code_eval = 0.50 maximum.

**Impact**: All 18 evaluations affected. QR drag ~0.05--0.10.

**Recommendation**: When Layer C is SKIP, redistribute its weight to Layer A and Layer B
proportionally (e.g., A=30%, B=70% of the remaining weight), rather than treating it as 0.

### 2.4 Step Efficiency action_economy = 0.0 -- OPEN

17/18 evaluations receive action_economy = 0.0. Without a reference step count, the
programmatic ratio-based thresholds (1.3x/1.6x/2.2x/3.0x) cannot be computed, so the
evaluator should fall back to LLM-only judging. However, the fallback appears to still
produce 0.0, suggesting it may be using an implicit expected step count (e.g., from the
task's `agent_max_steps` field) rather than a reference trace.

Multi-turn conversations where the student drives escalation (5--9 turns) naturally produce
higher step counts. Penalizing the agent for responding to student requests is misaligned
with the tutoring objective.

**Impact**: step_efficiency scores capped at 0.225--0.525.

**Recommendation**: When no reference exists, action_economy should use the LLM judge
with explicit context about conversation turn count. Alternatively, normalize step count
by the number of student turns.

### 2.5 Process Alignment = 0.0 -- BY DESIGN

When no reference trace is available, this metric is skipped (scored 0.0). Weight = 0.10
in QP aggregate. This is documented design behavior, not a bug.

---

## 3. Task-Specific Issues

### 3.1 B01 advanced_quant: Result Judge vs Tutor 7D Contradiction

- **Result Judge**: 0.161 -- claims "no tutoring content whatsoever"
- **Tutor 7D**: 0.957 -- rates tutoring as near-perfect

**Evidence**: Turn 2 contains extensive metric interpretation (Sharpe/Sortino/Calmar/MDD
with concrete benchmarks, overfitting checklist, illustration). Later turns escalate to
walk-forward grid analysis at the student's explicit request.

**Diagnosis**: The judge applied a "basic interpretation" template to an advanced conversation.
Advanced-level metric interpretation (e.g., Lo-adjusted Sharpe, rank stability across
parameter surfaces) is valid tutoring content, not a deviation from the task.

**QR impact**: The 0.161 judge score triggers heavy dampening (factor=0.012), pushing
QR from ~0.65 to 0.349. Estimated correct QR: 0.55--0.65.

**Recommendation**: Add calibration rule to judge prompt: for advanced personas, deep
quantitative analysis of metrics IS the expected form of "interpretation."

### 3.2 B02 intermediate: Functionally Broken Code Gets eval_script = 1.0

The agent's backtest code goes bankrupt (-99% to -100% return) on every execution, but
eval_script = 1.0 because all 7 structural checks (data/engine/strategy layers, sequential
replay, strategy isolation, performance summary, architecture rationale) pass.

**Diagnosis**: The eval script checks structural presence, not functional correctness.
The QR dampening mechanism correctly reduces eval_script weight (from 30% to 14%) due to
the large divergence with the judge (0.456), but the structural eval remains blind to
this failure mode.

**Impact**: QR inflated by ~0.05--0.08 despite dampening.

**Recommendation**: Add a functional correctness check: if performance summary shows
total_return < -90% or equity < 1% of initial, cap eval_script score at 0.50.

### 3.3 B02 advanced_quant: `strategy_isolated_from_data_io` False Negative

The `DualMovingAverageCrossStrategy` accesses data only through `ctx.history()` and
`ctx.account`, but imports `from data_replay import Bar` for type hinting. The eval
script's `strategy_segments()` + `strategy_isolated_from_data_io()` detects `import` /
`from ... import` as data I/O, causing a false negative.

**Recommendation**: Exclude `from X import Y` where Y is a type/class name (e.g.,
`Bar`, `Tick`, `Signal`) from the bad_terms check. Or add exception for imports that
do not include `pandas`, `read_csv`, `open(`.

### 3.4 B04 All Personas: CSV Column Naming Too Strict

All 3 personas fail `per_asset_accounting_present` on the CSV fallback path because agents
use prefixed column names (`BTCUSDT_qty`, `BTCUSDT_realized_pnl`, `pnl_btc$`) instead of
the expected exact names (`asset`, `symbol`, `position`, `qty`, `pnl`, `realized_pnl`).

The code-path regex (`positions? = {`) partially compensates but misses agents using
DataFrame columns or flat dict patterns.

**Impact**: Combined with other check failures, eval_script = 0.35 for all 3 personas
when the estimated correct score is ~0.85--0.90.

**Recommendation**: Use substring matching in `workspace_has_csv_column_group()`:
a column name containing any of the group keywords (e.g., column `btcusdt_qty` matches
group `["position", "qty"]`) should satisfy the requirement.

### 3.5 B05 advanced_quant: Judge Misinterprets Execution Cost Model

The agent built a slippage stress-test framework (OCO stop-loss + Donchian breakout on 5m
bars). The strategy's negative equity (-9.6M USDT) is a valid outcome demonstrating that
the execution model works correctly (stop-loss slippage dominates returns). The judge
scored 0.5556, interpreting the negative equity as "broken simulation."

**Impact**: QR dampened from ~0.80 to 0.609.

**Recommendation**: Add calibration rule to judge prompt: "In execution simulation tasks,
negative returns from a cost-dominated strategy demonstrate the model is working, not that
the simulation is broken."

### 3.6 B03 advanced_quant: Content Repetition -- Correctly Penalized

The agent repeated ~100-line GatedDataView architecture descriptions in Turns 4, 6, and 8.
The student complained twice ("You just repeated the earlier architecture"). Tutor 7D = 0.362
and step_efficiency = 0.225 are justified penalties.

D4 (domain_accuracy) = 0.43 may be slightly too low -- the technical content itself was
correct; the problem was repetition, not inaccuracy. Estimated correct D4: ~0.65--0.75.

---

## 4. Scores Verified as Accurate

| Dimension | Coverage | Notes |
|-----------|----------|-------|
| Code Eval Layer B (execution) | 18/18 | Faithfully reflects shell_exec success/failure rates |
| LLM Result Judge | 12/18 | Reasonable for most; anomalous for B01-adv, B05-adv, B02-int |
| Tool Usage | 18/18 | Correct distractor detection, convenient tool bonuses |
| Role Adherence | 18/18 | 0.83--1.0, matches trace behavior |
| Topic Adherence | 18/18 | 1.0 for all, matches trace behavior |
| Tutor 7D (intermediate) | 6/6 | 0.838--0.938, well-calibrated |
| Tutor 7D (advanced) | 5/6 | 0.905--0.962, except B03 (justified low) |
| QR Dampening Mechanism | 18/18 | Correctly reduces programmatic weight when diverging from judge |
| Process Reasonableness | 18/18 | Generally reasonable, slight low bias for clean executions |
| Code Process | 18/18 | Programmatic + LLM-judged split works well |

---

## 5. Priority Fix List

| Priority | Issue | Fix | Impact |
|----------|-------|-----|--------|
| P0 | Tutor 7D beginner fallback default-to-5 | Replace `parsed.get("score", 5)` with `None`; exclude from average | 6/18, Tutor +0.15--0.30 |
| P0 | B04 CSV column substring matching | Use `any(kw in col for kw in group)` instead of exact match | 3/18, eval_script +0.20 |
| P1 | B01/B05 Judge calibration for advanced | Add prompt rules: advanced analysis = valid tutoring; negative equity in cost model != broken | 2/18, QR +0.15--0.25 |
| P1 | B02 functional correctness check | Cap eval_script at 0.50 when total_return < -90% | 1/18, QR -0.05 (correct direction) |
| P2 | Layer C SKIP weight redistribution | Redistribute to A+B when no reference | 18/18, code_eval +0.10--0.15 |
| P2 | Step efficiency action_economy normalization | Normalize by turn count or use LLM fallback | 17/18, step_eff +0.10--0.20 |
| P3 | B02-adv `strategy_isolated` type import | Exclude type-only imports from bad_terms | 1/18, eval_script +0.15 |

---

## 6. Appendix: Fallback Recovery Mechanism

The Tutor 7D evaluation uses DeepEval's `ConversationalGEval` which expects the LLM
judge to return `{"score": N, "reason": "..."}` JSON. When parsing fails, the defense
chain activates:

```
Normal path: metric.a_measure(tc) -> trimAndLoadJson -> score
                |
                v (ValueError: invalid JSON)
Retry x2:      Re-run a_measure() after 1s sleep
                |
                v (all retries exhausted)
Layer 1:        _extract_score_from_prose(raw_text)
                Regex for "score": N or "N out of 10" patterns
                |
                v (no extractable score)
Layer 2:        _fallback_direct_eval(metric, tc, model_name)
                Reconstruct Phase 2 prompt, call a_generate(),
                robust JSON parser with markdown/prose stripping
                |
                v (parse still returns {})
Default:        score = parsed.get("score", 5)  -> 5/10 = 0.50
                Tagged as [FALLBACK-DIRECT]
```

The `default-to-5` at the bottom of the chain systematically pulls scores toward 0.50.
When all 21 evaluations (7 dims x 3 shuffles) hit fallback, dimensions that genuinely
deserve 0.80--0.90 are dragged down. Beginner personas trigger more fallbacks due to
longer conversation prompts that increase the probability of LLM JSON parse failures.
