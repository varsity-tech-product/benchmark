# LLM Judge Accuracy & Reliability Improvement Plan

> **Date**: 2026-04-01
> **Scope**: `bench/evaluation/` scoring pipeline
> **DeepEval version**: v3.8.4
> **Current eval models**: `anthropic/claude-haiku-4-5-20251001` (single model, OAuth)

---

## 1. Background: How DeepEval's Logprobs Mechanism Works

### 1.1 The G-Eval Algorithm (DeepEval v3.8.4)

DeepEval implements the [G-Eval paper](https://arxiv.org/pdf/2303.16634.pdf) approach to
reduce information loss when extracting discrete scores from LLM judges.

**Core function**: `calculate_weighted_summed_score()` in `deepeval/metrics/g_eval/utils.py:264-307`

```
LLM outputs {"score": 7, "reason": "..."}
    ↓
Locate token "7" in the logprobs stream
    ↓
Read top_logprobs (top 20 alternative tokens at that position)
    ↓
Filter: keep only decimal tokens with P > 1% (logprob > ln(0.01))
    ↓
Weighted average: score = Σ(token_value × P(token)) / Σ(P)
```

**Example**: LLM argmax outputs score=7, but logprobs show P(7)=0.6, P(8)=0.25, P(6)=0.15:
```
weighted_score = (7×0.6 + 8×0.25 + 6×0.15) / 1.0 = 7.1
```

This preserves the model's uncertainty distribution rather than collapsing to a single integer.

### 1.2 Where It's Used in DeepEval

| Class | Score Range | logprobs param | Fallback |
|---|---|---|---|
| `GEval` | 0-10 (configurable via Rubric) | `top_logprobs=20` (configurable) | `AttributeError` → schema-based extraction |
| `ConversationalGEval` | 0-10 fixed | `top_logprobs=20` hardcoded | Same |

Both classes call `model.a_generate_raw_response(prompt, top_logprobs=20)`. If the model
doesn't implement this method, they catch `AttributeError` and fall back to
`a_generate_with_schema_and_extract()`, which returns a plain integer score.

### 1.3 Model Support Matrix (constants.py)

**Supports logprobs** (`supports_log_probs=True`):
- GPT-4o, GPT-4o-mini, GPT-4-turbo, GPT-4.1, GPT-3.5-turbo

**Does NOT support logprobs** (`supports_log_probs=False`):
- GPT-4.5-preview, o1/o3 reasoning models

**Not registered** (falls to default → `None`):
- All non-OpenAI models (Claude, Gemini, etc.)

---

## 2. Critical Finding: Bench Currently Does NOT Use Logprobs At All

### 2.1 Evidence Chain

**Tutor 7D (ConversationalGEval path):**
1. `EVAL_DEFAULT_MODELS = ["anthropic/claude-haiku-4-5-20251001"]`
2. `EVAL_USE_OAUTH = True` → resolves to `_OAuthAnthropicModel`
3. `_OAuthAnthropicModel` does **not** implement `a_generate_raw_response`
4. ConversationalGEval calls `a_generate_raw_response` → `AttributeError` → schema fallback
5. Result: **plain integer score, no logprobs weighting**

**Custom dimensions** (role_adherence, topic_adherence, process_reasonableness, code_process):
- All use `model_obj.a_generate(prompt)` → plain text → JSON parse
- Never call `generate_raw_response`
- Code path: `custom_conv_metrics.py:55-65`, `process_reasonableness.py:145-155`

**Result Judge:**
- Uses `a_generate()` + JSON parse, never logprobs

**Conclusion**: The entire evaluation pipeline produces **argmax discrete scores**.
DeepEval's logprobs weighting mechanism is completely inactive.

### 2.2 The Monkey-Patch in tutor_conv_geval.py

The logprobs fallback patch (`_patch_gptmodel_logprobs_fallback`) handles the case where
logprobs are **rejected by the API** (e.g., reasoning models). But the current failure mode
is different: `_OAuthAnthropicModel` simply lacks the method entirely, so the `AttributeError`
is caught by DeepEval's own fallback, **before** the patch is even reached.

---

## 3. Logprobs Support Across Current-Gen Models (April 2026)

### 3.1 Research Results

| Provider / Model | Logprobs Support | Status |
|---|---|---|
| OpenAI GPT-4o / GPT-4o-mini | **Yes** | Stable, working |
| OpenAI GPT-4.1 family | **Yes** | Stable, working |
| OpenAI GPT-5 / GPT-5-mini | **No** | Reasoning models, logprobs unavailable |
| OpenAI GPT-5.1 | **Partial** | Basic logprobs may work, mixed reports |
| **OpenAI GPT-5.2** | **No (broken)** | HTTP 500 errors on logprobs requests |
| **OpenAI GPT-5.4** | **No (broken)** | Same 500 errors as GPT-5.2 |
| **Anthropic Claude (all)** | **No** | Not supported, no equivalent API feature |
| Google Gemini 2.0/2.5 | **Yes** | Working (native Vertex AI API only) |
| Google Gemini 3.0/3.1 | **No (disabled)** | "Not supported for this model" error |

**Sources**:
- [OpenAI Community: logprobs deprecated for GPT-5?](https://community.openai.com/t/logprobs-deprecated-for-gpt-5-models/1355427)
- [OpenAI Community: GPT-5.2 logprobs removed?](https://community.openai.com/t/gpt-5-2-logprobs-support-removed/1378114)
- [Google AI Forum: logprobs disabled for Gemini 3/3.1](https://discuss.ai.google.dev/t/were-logprobs-disabled-for-gemini-3-3-1-in-vertex-api/132426)

### 3.2 Implication

**Logprobs as a reliability strategy is effectively dead for current-generation models.**

The only models with stable logprobs are the older GPT-4o/GPT-4.1 family. These are
capable judges but increasingly outdated. Building a reliability strategy around logprobs
means locking into legacy models — a poor long-term bet.

---

## 4. Alternative Approaches for Claude (and Any Non-Logprobs Model)

Since logprobs are unavailable for Claude and broken for GPT-5.x, we need
**logprobs-free** strategies to improve judge accuracy and reliability.

### 4.1 Multi-Sample Voting (Recommended — High Priority)

**Concept**: Sample the same judge prompt N times with temperature > 0, then aggregate.

This is the **statistical equivalent** of logprobs weighting — both recover the model's
output distribution, but multi-sample does it via repeated draws rather than reading the
probability table directly.

**Implementation sketch**:
```python
async def _call_llm_with_voting(model, prompt: str, n_samples: int = 3, temperature: float = 0.3) -> dict:
    """Call LLM N times and aggregate scores via trimmed mean."""
    tasks = [_call_llm_single(model, prompt, temperature=temperature) for _ in range(n_samples)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    scores = [r["score"] for r in results if isinstance(r, dict) and "score" in r]
    if not scores:
        return await _call_llm(model, prompt)  # fallback to single call

    # Trimmed mean: drop highest and lowest if n >= 3
    if len(scores) >= 3:
        scores_sorted = sorted(scores)
        trimmed = scores_sorted[1:-1]  # drop extremes
        final_score = sum(trimmed) / len(trimmed)
    else:
        final_score = sum(scores) / len(scores)

    return {
        "score": final_score,
        "reason": results[0].get("reason", ""),  # use first reason
        "_vote_spread": max(scores) - min(scores),
        "_vote_scores": scores,
        "_eval_cost": sum(r.get("_eval_cost", 0) for r in results if isinstance(r, dict)),
    }
```

**Trade-offs**:
- Cost: 3x per dimension (same as adding 2 more eval models)
- Benefit: recovers model uncertainty WITHOUT logprobs
- The `_vote_spread` metadata serves as a built-in confidence signal
- Works with ANY model (Claude, GPT-5.x, Gemini, open-source)

**Where to apply**: Custom dimensions that currently use single `_call_llm()` calls:
- `process_reasonableness.py` — 3 sub-dimensions
- `custom_conv_metrics.py` — role_adherence, topic_adherence
- `code_process.py` — LLM-judged sub-dimensions
- `result_judge.py` — completeness/correctness

**Comparison with logprobs**:

| Property | Logprobs Weighting | Multi-Sample Voting |
|---|---|---|
| Info source | Token probability distribution | Empirical sample distribution |
| Model requirement | Must support logprobs API | Any model, any API |
| Cost per call | 1x | Nx (typically 3x) |
| Precision | Higher (continuous distribution) | Lower (discrete samples) |
| Confidence signal | Entropy of distribution | Vote spread / std dev |
| Future-proof | No (dying feature) | Yes (universal) |

### 4.2 Self-Consistency with Chain-of-Thought (Medium Priority)

**Concept**: Ask the judge to reason step-by-step, sample multiple reasoning chains,
then take the majority score. Inspired by [Wang et al. 2023 "Self-Consistency"](https://arxiv.org/abs/2203.11171).

Different from plain multi-sample voting: the model may reach different scores via
genuinely different reasoning paths, not just sampling noise. This catches cases where
the score depends on which aspects the judge focuses on first.

**Implementation**: Already partially implemented via the 3-pass shuffled evaluation
in `tutor_conv_geval.py`. Could be extended to custom dimensions by varying the
sub-dimension order in the prompt.

### 4.3 Calibrated Confidence Elicitation (Medium Priority)

**Concept**: Modify judge prompts to also output a confidence level, then weight
scores by confidence during aggregation.

```json
{
  "score": 7,
  "confidence": "high",
  "reason": "Clear evidence of ..."
}
```

**Weighting scheme**:
- `"high"` → weight 1.0
- `"medium"` → weight 0.7
- `"low"` → weight 0.4

When aggregating across models or samples, confidence-weighted mean replaces simple mean.

**Caveat**: LLMs are notoriously poorly calibrated in self-reported confidence.
Must validate that the confidence signal actually correlates with accuracy before
relying on it. Run a calibration study on a held-out set first.

### 4.4 Anchor-Based Scoring (Low Priority, High Effort)

**Concept**: Instead of asking "rate this 1-10", provide concrete anchor examples
for scores 2, 5, and 8 (from reference evaluations), then ask the judge to place
the candidate relative to anchors.

This reduces the arbitrary mapping from quality → number and should improve
inter-model agreement. However, it requires curating anchor examples per dimension,
which is labor-intensive.

---

## 5. Restoring Multi-Model Evaluation (Highest Priority)

### 5.1 Current Problem

`llm_config.py:24-29` has **three models commented out**:
```python
EVAL_DEFAULT_MODELS: list[str] = [
    "anthropic/claude-haiku-4-5-20251001",
    # "anthropic/claude-sonnet-4.6",
    # "openai/gpt-5.2",
    # "anthropic/claude-opus-4.6",
]
```

This means ALL multi-model averaging mechanisms are **degenerate** — every "average
across models" is just a single model's score. The divergence dampening in QR blending
is also inactive (no divergence to detect with one model).

### 5.2 Recommendation

Restore at least 2 models. Suggested configuration:
```python
EVAL_DEFAULT_MODELS: list[str] = [
    "anthropic/claude-haiku-4-5-20251001",   # fast, cheap
    "anthropic/claude-sonnet-4.6",            # strong reasoning
]
```

Or for maximum diversity (catches model-family-specific biases):
```python
EVAL_DEFAULT_MODELS: list[str] = [
    "anthropic/claude-haiku-4-5-20251001",
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-4o",                          # different model family + logprobs capable
]
```

Adding GPT-4o (not 5.x) would also enable logprobs for that model's scores specifically.

---

## 6. Post-Evaluation Consistency Audit (High Priority)

### 6.1 Design

Add a post-eval audit step that flags unreliable scores without changing them.

```python
def audit_judge_consistency(eval_results: dict) -> dict:
    """Post-evaluation consistency audit. Returns confidence metadata."""
    flags = []

    # 1. Cross-model divergence (per dimension)
    for dim, per_model in eval_results.get("_per_model", {}).items():
        scores = [v for v in per_model.values() if v is not None]
        if len(scores) >= 2:
            spread = max(scores) - min(scores)
            if spread > 0.3:
                flags.append({
                    "type": "cross_model_divergence",
                    "dimension": dim,
                    "spread": round(spread, 3),
                    "confidence": "low",
                })

    # 2. Result-Process mismatch
    qr = eval_results.get("qr_score", 0)
    qp = eval_results.get("qp_score", 0)
    if abs(qr - qp) > 0.4:
        flags.append({
            "type": "result_process_mismatch",
            "qr": qr, "qp": qp,
            "confidence": "low",
        })

    # 3. Sub-dimension contradiction (e.g., persona=9 but boundary=3)
    for dim_group in ["role_adherence", "topic_adherence"]:
        subs = eval_results.get(f"{dim_group}_sub_scores", {})
        if len(subs) >= 2:
            vals = list(subs.values())
            if max(vals) - min(vals) > 0.5:
                flags.append({
                    "type": "sub_dimension_contradiction",
                    "dimension": dim_group,
                    "sub_scores": subs,
                })

    confidence = "high" if len(flags) == 0 else ("medium" if len(flags) <= 2 else "low")
    return {"flags": flags, "overall_confidence": confidence}
```

### 6.2 Integration Point

Call after all scoring completes, before report generation. Add confidence
metadata to `score_report.py` output.

### 6.3 Prerequisite

Requires >= 2 eval models to detect cross-model divergence. Without this,
only sub-dimension contradiction and result-process mismatch checks work.

---

## 7. Increasing Pass Count (Low Priority)

### 7.1 Current State

Tutor 7D uses 3 shuffled passes. Custom dimensions use 1 pass.

### 7.2 Analysis

| Change | Cost Impact | Std Error Reduction |
|---|---|---|
| Tutor: 3 → 5 passes | +67% tutor eval cost | -23% (÷√(5/3)) |
| Custom dims: 1 → 3 passes | +200% custom dim cost | -42% (÷√3) |

### 7.3 Recommendation

**Do not increase pass count blindly.** First measure the actual variance:
1. Run 5-10 tasks with 5 passes each
2. Compute per-dimension std dev across passes
3. If std dev < 0.5 (on 10-point scale): passes are sufficient, variance is low
4. If std dev > 1.5: prompt quality is the issue, not sample count

**Alternative**: Multi-sample voting (§4.1) achieves the same effect with
more flexibility (variable temperature, trimmed mean, confidence metadata).

---

## 8. Priority Roadmap

| Priority | Action | Prerequisite | Effort | Cost Impact |
|---|---|---|---|---|
| **P0** | Restore multi-model eval (uncomment models in llm_config.py) | None | 1 line | +1-2x eval cost |
| **P1** | Add consistency audit (§6) | P0 | ~80 lines | Zero (post-processing) |
| **P2** | Multi-sample voting for custom dims (§4.1) | None | ~100 lines | +2x custom dim cost |
| **P3** | Add GPT-4o to eval models (enables logprobs for 1 model) | None | Config change | +cost of GPT-4o calls |
| **P4** | Calibrated confidence elicitation (§4.3) | Calibration study | ~60 lines + study | Zero API cost |
| **P5** | Increase pass count | Variance analysis first | 1 line | +67% tutor cost |

### Key Insight

**Logprobs as a strategy is dead for current-gen models.** Neither Claude, GPT-5.x, nor
Gemini 3.x support it. The only viable logprobs targets are legacy GPT-4o/GPT-4.1.

The **model-agnostic** alternatives — multi-model ensemble, multi-sample voting, and
consistency audit — are both more robust and more future-proof. These should be the
primary investment for judge reliability.

---

## Appendix: TrustJudge Framework Analysis

[TrustJudge](https://github.com/TrustJudge/TrustJudge) (ICLR 2026) addresses
LLM-as-judge inconsistencies via logprobs-based scoring. While the theoretical
contribution is sound, practical integration is **not recommended** because:

1. **Scenario mismatch**: Designed for pairwise comparison of candidate answers,
   not single-agent absolute scoring
2. **Logprobs dependency**: Core innovation requires logprobs, which are unavailable
   for our eval models
3. **CLI pipeline**: Not a library; would require substantial refactoring
4. **Heavy dependencies**: vLLM + multi-GPU, incompatible with API-based eval

However, the **conceptual insight** — that discrete scores lose information and
should be treated as distributions — directly informs our multi-sample voting
approach (§4.1), which achieves the same goal without logprobs.
