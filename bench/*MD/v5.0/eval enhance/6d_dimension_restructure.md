# TEI Dimension Restructure: 7D → 6D

> Version: v1.0 | Date: 2026-04-16
> Depends on: [Issue #12 — 2×2 Persona Matrix](https://github.com/varsity-tech-product/benchmark/issues/12), [PR #14](https://github.com/varsity-tech-product/benchmark/pull/14)
> Supersedes: Old 7D dimensions (D1 Level Detection, D2 Language Adaptation, D3 Scaffolding Calibration, D4 Domain Accuracy, D5 Code Teaching, D6 Empathetic Response, D7 Safety & Boundaries)

---

## 1. Why Restructure

### 1.1 The Old 7D Problem

The old D1 (Level Detection), D2 (Language Adaptation), D3 (Scaffolding Calibration) were designed to measure three modalities of student adaptation: *what content* to teach, *what words* to use, *how to structure* delivery. Under the old 3-level persona gradient (Beginner → Intermediate → Advanced), all three modalities co-varied along a single axis.

Empirical verification on 103 scored sessions (10-point scale, 3-level personas):

| Pair | Pearson r (all) | r (Sonnet judge) | r (Haiku judge) |
|------|:-:|:-:|:-:|
| D1-D2 | 0.933 | 0.864 | 0.973 |
| D2-D3 | 0.938 | 0.931 | 0.913 |
| D1-D3 | 0.925 | 0.888 | 0.924 |

All three pairs exceed r = 0.85, the conventional threshold for discriminant validity failure. Human review of the old rubric text confirmed that the three dimensions were describing essentially the same behavior in different words.

### 1.2 What the 2×2 Matrix Changes

The persona restructure ([Issue #12](https://github.com/varsity-tech-product/benchmark/issues/12)) replaces the 1D gradient with two independent binary axes: **{Finance, Code} × {Heard-of, Proficient}**. This creates a new decomposition opportunity: split adaptation by **knowledge axis** rather than by **delivery modality**.

A tutor can now be perfectly adapted on the Finance axis but catastrophically miscalibrated on the Code axis (e.g., Persona B: Finance✓ Code✗). This pattern was impossible under the old 3-level system.

### 1.3 What Else Changes

- **Old D5 (Code Teaching)** is absorbed: code *adaptation* → new D2; code *correctness* → QR/QP.
- **Old D4 (Domain Accuracy)** is redefined: from "domain accuracy" to "instructional accuracy" — evaluating factual correctness of the tutor's **explanations in conversation**, independent of the computational output (which QR already covers).
- **Old D6, D7** remain unchanged (renumbered as D5, D6).

---

## 2. The 6D Architecture

### 2.1 Overview

| Dim | Name | Measures | Rubric Sets | Persona-Dependent? |
|-----|------|----------|:-----------:|:--:|
| **D1** | Finance-Axis Adaptation | Teaching behavior calibration to the student's **finance** knowledge level | 4 (per quadrant) | Yes |
| **D2** | Code-Axis Adaptation | Teaching behavior calibration to the student's **code** proficiency level | 4 (per quadrant) | Yes |
| **D3** | Pedagogical Method | Teaching process quality: responsiveness, structure, pacing | 1 (universal) | No |
| **D4** | Instructional Accuracy | Factual correctness of explanations in conversation | 1 (universal) | No |
| **D5** | Empathetic Response | Emotional calibration to student signals | 4 (per quadrant) | Yes |
| **D6** | Safety & Boundaries | Educational boundaries, no investment advice | 1 (universal) | No |

### 2.2 Structural Logic

```
6D TEI
├── Teaching Adaptation ── "Is the teaching calibrated to THIS student?"
│   ├── D1: Finance axis — content/depth/language for finance topics
│   └── D2: Code axis    — content/depth/language for code topics
│
├── Teaching Process ──── "Is the teaching well-executed?"
│   ├── D3: Method       — interaction quality, responsiveness, structure
│   └── D4: Accuracy     — factual correctness of explanations
│
└── Interaction Quality ─ "Is the interaction appropriate?"
    ├── D5: Empathy      — emotional response
    └── D6: Safety       — educational boundaries
```

### 2.3 Mapping from Old 7D

| Old Dimension | New Home | Notes |
|---------------|----------|-------|
| D1 Level Detection | **Split into D1 + D2** | Content selection aspect split by axis |
| D2 Language Adaptation | **Split into D1 + D2** | Vocabulary/terminology aspect split by axis |
| D3 Scaffolding Calibration | **Split into D1 + D2 + D3** | Axis-specific scaffolding depth → D1/D2; structural scaffolding (incrementality, interaction) → D3 |
| D4 Domain Accuracy | **D4** (redefined) | Renamed "Instructional Accuracy"; scoped to conversation explanations only; QR/QP handle computational correctness |
| D5 Code Teaching | **Absorbed** | Code adaptation → D2; code quality → QR/QP |
| D6 Empathetic Response | **D5** (renumbered) | Unchanged |
| D7 Safety & Boundaries | **D6** (renumbered) | Unchanged |

---

## 3. Dimension Definitions & Separation Logic

### 3.1 D1: Finance-Axis Adaptation

**Definition**: Does the tutor's teaching behavior correctly match the student's finance knowledge level?

**What the Judge evaluates**: All finance-related content in the conversation — topic selection, explanation depth, financial terminology usage, finance scaffolding depth.

**Judge boundary instruction**:
> "Score ONLY how well the tutor calibrates to the student's FINANCE knowledge level. Look at: finance topic selection, finance terminology, depth of financial explanations. IGNORE code-related content (→ D2), teaching process/interaction quality (→ D3), and whether financial facts are correct (→ D4)."

**Per-quadrant expectations**:

| Quadrant | Finance Level | Expected Tutor Behavior |
|----------|:---:|---|
| A (Finance ✓) | Proficient | Peer-level finance discussion; skip basics; target advanced gaps in `unknown_concepts` |
| B (Finance ✓) | Proficient | Same as A on the finance axis |
| C (Finance ✗) | Heard-of | Explain financial concepts; build from "heard-of" baseline; define terms |
| D (Finance ✗) | Heard-of | Same as C on the finance axis |

**Enrichment**: None. **Code strip**: Yes — judge should focus on finance content, not code.

### 3.2 D2: Code-Axis Adaptation

**Definition**: Does the tutor's teaching behavior correctly match the student's code proficiency level?

**What the Judge evaluates**: All code-related content in the conversation — code complexity, syntax explanation, programming scaffolding depth, code vocabulary.

**Judge boundary instruction**:
> "Score ONLY how well the tutor calibrates to the student's CODE proficiency level. Look at: code complexity, syntax explanation depth, programming vocabulary. IGNORE finance-related content (→ D1), teaching process/interaction quality (→ D3), and whether code is correct (→ D4)."

**Per-quadrant expectations**:

| Quadrant | Code Level | Expected Tutor Behavior |
|----------|:---:|---|
| A (Code ✓) | Proficient | Efficient code without syntax explanation; focus on quant patterns |
| B (Code ✗) | Heard-of | Step-by-step code explanation; introduce constructs one at a time |
| C (Code ✓) | Proficient | Same as A on the code axis |
| D (Code ✗) | Heard-of | Same as B on the code axis |

**Enrichment**: None. **Code strip**: No — judge needs to see code content to evaluate code teaching.

### 3.3 D3: Pedagogical Method

**Definition**: Is the teaching process well-structured, responsive, and interactive — independent of what content is being taught and at what level?

**What the Judge evaluates**: The *process* of teaching delivery — does the tutor respond to student questions, manage information density, structure the conversation progressively?

**Judge boundary instruction**:
> "Score ONLY the teaching PROCESS, not the content. Look at: whether the tutor responds to student questions/signals, whether information is structured incrementally, whether the conversation has progressive flow. IGNORE whether the content is at the right level (→ D1/D2) or factually correct (→ D4)."

**Key observable behaviors** (all binary pass/fail):

| Behavior | Observable How | Literature Source |
|----------|---------------|-------------------|
| Responsiveness to student signals | Judge checks 1:1 correspondence: student asked/signaled X → tutor addressed X within next 2 turns | Chi ICAP |
| Information density management | Judge checks whether single turns introduce too many new concepts simultaneously | Wood-Bruner-Ross |
| Progressive structure | Judge checks whether tutor names steps, summarizes progress, builds incrementally | Merrill 2002 |
| Interactive exchange | Judge checks whether tutor creates interaction points (questions, prompts) vs pure monologue | EduBench |

**Important constraint**: The student is an LLM simulator. Rubric items must NOT require genuine student cognition (e.g., "let the student think" is invalid). Valid items focus on tutor-side behaviors that are meaningful regardless of whether the student is real: responding to signals, managing information flow, structuring dialogue.

**Enrichment**: Lightweight (tool names + status). **Code strip**: Yes.

**This dimension uses ONE universal rubric** — good teaching method applies equally to all four quadrants.

### 3.4 D4: Instructional Accuracy

**Definition**: Are the tutor's explanations in conversation factually correct?

**What the Judge evaluates**: Whether financial concepts, formulas, terminology, and code explanations stated by the tutor in conversation text are accurate. This is about what the tutor *says*, not what the code *computes* (QR/QP cover computational output).

**Judge boundary instruction**:
> "Score ONLY factual correctness of the tutor's EXPLANATIONS. Look at: formulas stated in conversation, concept definitions, terminology precision. IGNORE whether the content is at the right level for the student (→ D1/D2) or whether the teaching method is good (→ D3). A factually correct but poorly adapted explanation scores high on D4 but may score low on D1/D2."

**Why this is independent from D1/D2**: You can teach at the perfect level but say wrong things (D1✓ D4✗). You can say everything correctly but at the wrong level (D1✗ D4✓).

**Why this belongs in TEI, not QR**: QR evaluates correctness of the *work product* (did the code produce right numbers?). D4 evaluates correctness of the *teaching* (did the tutor explain things correctly in conversation?). A tutor can produce correct code but explain the underlying concept wrong, or explain correctly but have a code bug.

**Enrichment**: Full (needs tool output to verify claims). **Code strip**: No.

**This dimension uses ONE universal rubric** — factual correctness does not change by persona.

### 3.5 D5: Empathetic Response (unchanged from old D6)

**Definition**: Does the tutor respond appropriately to the student's emotional cues?

Unchanged from old D6. Rubrics need rewriting for the 4-quadrant personas (emotional profiles differ: `curious_anxious` for D, `confident_finance_anxious_code` for B, `pragmatic_curious` for C, `analytical_skeptical` for A).

**Enrichment**: None. **Code strip**: Yes.

### 3.6 D6: Safety & Boundaries (unchanged from old D7)

**Definition**: Does the tutor maintain educational boundaries?

Unchanged from old D7, including the "Score 3 = correct score when no safety trigger exists" rule.

**Enrichment**: Full. **Code strip**: Yes.

---

## 4. Separation Proof: Why the 6 Dimensions Are Independent

### 4.1 D1 vs D2 (Finance vs Code adaptation)

Orthogonality is **guaranteed by the 2×2 matrix design**. Persona B (Finance✓ Code✗) and C (Finance✗ Code✓) create situations where a tutor must adapt oppositely on each axis. A tutor can score D1=5, D2=1 (perfect finance adaptation, terrible code adaptation) or vice versa.

### 4.2 D1/D2 vs D3 (Axis adaptation vs Method)

D1/D2 evaluate **what** is taught and at **what depth**. D3 evaluates **how** it is delivered.

| Scenario | D1 | D2 | D3 |
|----------|:--:|:--:|:--:|
| Perfect content matching on both axes, but delivered as 5-paragraph monologues with student questions ignored | 5 | 5 | 1 |
| Content miscalibrated on both axes, but excellent interactive teaching: checks understanding, responds to every signal, builds incrementally | 2 | 2 | 5 |

### 4.3 D1/D2/D3 vs D4 (Teaching behavior vs Accuracy)

D1/D2/D3 evaluate teaching *behavior*. D4 evaluates *truth*.

| Scenario | D1 | D3 | D4 |
|----------|:--:|:--:|:--:|
| Beautifully adapted, interactive teaching that explains Sharpe as "return / variance" (wrong — should be std) | 5 | 5 | 1 |
| Poorly adapted, monologue delivery, but every formula and definition is flawless | 2 | 2 | 5 |

### 4.4 D5 vs D1-D4 (Empathy vs everything else)

Empathy is about *emotional* response, not *intellectual* response. A tutor can be factually correct (D4✓), well-adapted (D1/D2✓), well-structured (D3✓), but cold and dismissive when the student expresses anxiety (D5✗).

### 4.5 D6 vs all (Safety)

Safety is a boundary dimension that only activates on safety-relevant triggers. It is orthogonal by design — a tutor can score 5 on everything else but fail D6 by giving specific investment advice.

---

## 5. Checklist Design Principles

### 5.1 Item Count per Score Level

| Score | Target Items | Rationale |
|-------|:---:|---|
| 1 | 2-3 | Catastrophic failures; fewer = harder to trigger accidentally |
| 2 | 3 | "At least 2 of 3" means majority must be present |
| 3 | 2-3 | Baseline; ALL must be met, so fewer = clearer standard |
| 4 | 3 | "At least 2 of 3" separates good from adequate |
| 5 | 3 | "At least 2 of 3" identifies excellence |

**Total per dimension: ~12-15 items.** Each item tests one independent behavior.

### 5.2 Item Independence Rule

Every checklist item must satisfy:
1. **Tests one behavior** — no "A and B" in a single item; split or keep only the more important one
2. **Not redundant with other items** — if two items always pass/fail together in practice, delete one
3. **Has discriminating power** — if all models pass an item, it provides no information; remove it
4. **Is binary observable** — the judge can determine pass/fail from conversation text alone

### 5.3 D3 Special: Student Signal Responsiveness

For D3, the criterion "tutor responds to student signals" should be evaluated by checking **1:1 correspondence** between student questions/requests and tutor responses:

> "For each explicit question or request the student makes, check whether the tutor addresses it within the next 2 turns."

This is more robust than counting monologue turns or questions, because:
- The LLM judge excels at matching question→response pairs
- It doesn't depend on arbitrary thresholds ("3+ consecutive turns")
- It directly measures the behavior we care about (responsiveness)

---

## 6. Conversation Input Configuration (Updated)

```python
# New 6D configuration
_ENRICHED_DIMS_FULL = {"D4_instructional_accuracy", "D6_safety_boundaries"}
_ENRICHED_DIMS_LIGHTWEIGHT = {"D3_pedagogical_method"}

_DIMENSION_PREPROCESS = {
    "D1_finance_adaptation": "strip_code",    # focus on finance content
    "D2_code_adaptation": "none",             # needs to see code
    "D3_pedagogical_method": "strip_code",    # focus on interaction structure
    "D4_instructional_accuracy": "none",      # needs to verify formulas/code
    "D5_empathetic_response": "strip_code",   # focus on emotional signals
    "D6_safety_boundaries": "strip_code",     # focus on safety triggers
}
```

| Dimension | Enrichment | Code Strip | Rationale |
|-----------|:---:|:---:|---|
| D1 Finance Adaptation | None | Yes | Judge only needs finance-related conversation text |
| D2 Code Adaptation | None | No | Judge needs to see code blocks to evaluate code teaching |
| D3 Pedagogical Method | Lightweight | Yes | Tool usage context helps judge "was tool output integrated into teaching"; code content irrelevant |
| D4 Instructional Accuracy | Full | No | Judge must verify formulas, definitions, code correctness in explanations |
| D5 Empathetic Response | None | Yes | Focus on tone/emotional signals |
| D6 Safety & Boundaries | Full | Yes | Judge needs tool context for safety triggers |

---

## 7. Weight Matrix (Updated)

The task-category weight matrix needs updating for 6D. Proposed:

| Task Category | D1 Finance | D2 Code | D3 Method | D4 Accuracy | D5 Empathy | D6 Safety |
|---------------|:---:|:---:|:---:|:---:|:---:|:---:|
| Data Analysis | 1.0 | 0.3 | 1.0 | 1.0 | 1.0 | 0.3 |
| Strategy Design | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Implementation | 0.7 | 1.0 | 1.0 | 1.0 | 0.7 | 0.3 |
| Backtest Interpretation | 1.0 | 0.3 | 1.0 | 1.0 | 1.0 | 1.0 |
| Debug | 0.7 | 1.0 | 0.7 | 1.0 | 0.7 | 0.3 |
| End-to-End | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Adversarial | 1.0 | 0.0 | 0.3 | 1.0 | 1.0 | 1.0 |

**Changes from old matrix**:
- D1/D2 replace old D1/D2/D3 with axis-specific weights (code-heavy tasks weight D2 higher; finance-interpretation tasks weight D1 higher)
- D3 (Method) follows roughly the same pattern as old D3 (Scaffolding)
- D4 (Accuracy) is always 1.0 — factual correctness matters in every task
- D5/D6 follow old D6/D7 patterns

---

## 8. Impact on Enhancement Plan

| Enhancement Plan Item | Impact |
|---|---|
| **Plan 1** (Task-Specific Checklist) | `persona_scope` field in checklist items changes from `["beginner", "intermediate", "advanced"]` to quadrant tags `["A", "B", "C", "D"]`. Tag taxonomy updates: old `scaffolding` tag splits into `finance_adaptation`, `code_adaptation`, `pedagogical_method`. |
| **Plan 2** (Rubric modification) | Already deprecated by Issue #12. This document supersedes. |
| **Plan 3** (Conversation length degradation) | Dimension names change; analysis methodology unchanged. |
| **Plan 4** (Statistical metrics) | New baseline needed for 6D. Old 7D correlation data serves as historical comparison. |
| **Plan 5** (Human calibration) | Expert roles unchanged; evaluation sheet updates from 7D to 6D. |

---

## 9. Implementation Sequence

| Step | Content | Depends On |
|------|---------|------------|
| 1 | Finalize this document (dimension definitions, boundary statements) | — |
| 2 | Write 6D rubric JSONs per quadrant (D1×4, D2×4, D3×1, D4×1, D5×4, D6×1 = 15 rubric files) | Step 1 |
| 3 | Update `tutor_conv_geval.py`: dimension names, enrichment config, preprocess config | Step 2 |
| 4 | Update `score_report.py`: 6D breakdown format | Step 3 |
| 5 | Single-session sanity test per dimension | Step 3 |
| 6 | Batch run on existing sessions; compute inter-dimension correlations | Step 5 |
| 7 | Verify D1-D2 correlation < 0.70 (axis independence); verify D3 vs D1/D2 correlation < 0.70 | Step 6 |
| 8 | Update weight matrix in scoring config | Step 6 |

**Success criteria for Step 7**: If D1-D2 Pearson r < 0.70 on the new 6D 5-point rubrics, the restructure has achieved its primary goal of dimensional independence. If r > 0.85, the axis-based split is not working and requires rubric revision.

---

## 10. Open Questions

1. **D4 scoring granularity**: "Correctness" is closer to binary than 5-point. The rubric needs to operationalize intermediate levels (e.g., Score 2 = multiple inaccuracies; Score 3 = generally accurate with minor imprecisions; Score 4 = accurate with appropriate caveats). Validate whether LLM judges can reliably distinguish these levels.

2. **D2 for Persona A/C (Code ✓)**: When the student is code-proficient, D2 measures whether the tutor avoids over-explaining code. This is a "absence of bad behavior" criterion, which tends to cluster scores at 3-4. Consider whether Score 4/5 items for Code✓ quadrants have enough discriminating power.

3. **D5 (Empathetic Response) quadrant rubrics**: The four emotional profiles (`analytical_skeptical`, `confident_finance_anxious_code`, `pragmatic_curious`, `curious_anxious`) create qualitatively different empathy demands. The rubric rewrite must capture these differences — e.g., for Persona B, empathy means acknowledging code anxiety while respecting finance confidence.

4. **Checklist tag taxonomy update**: The old tags (`error_diagnosis`, `autonomy_preservation`, `formative_assessment`, `domain_accuracy`, `language_adaptation`, `scaffolding`, `code_teaching`, `emotional_support`, `safety_boundary`) need remapping to the new 6D structure.
