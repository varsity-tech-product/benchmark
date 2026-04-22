# QuantTutorBench Task Description Review Checklist

> Version: v2.0 | Audience: Reviewers auditing task JSON files for correctness and consistency

## Core Principle

The task description exists to **give the student simulator enough information to drive natural multi-turn conversation, and to give the scoring system clear evaluation anchors.** It should not over-specify implementation details — detail checking is handled by eval scripts, code_eval, tool_usage, and other fixed-paradigm evaluators. The task description only needs to ensure macro-level direction is correct and all fields are semantically consistent.

**Causality principle:** The reasoning direction must always be: **pedagogical goal → task description → eval script.** Never infer what the task description should say from the eval script implementation. If the eval script checks something the task description doesn't require, the eval script should be adjusted — not the other way around. When a discrepancy is found between the task description and the eval script, always ask: "What does the teaching goal demand?" and fix whichever side deviates from the answer.

---

## 1. Triad Alignment: description / expected_outcome / required_capabilities

These three fields are passed in full to the student simulator and jointly form the "teaching contract."

### 1.1 Review Order

Start from `required_capabilities` — it is the anchor.

1. **required_capabilities** — Does each item represent one independently assessable capability?
2. **expected_outcome** — Does it cover every capability with concrete acceptance criteria?
3. **description** — Is it a faithful one-sentence summary of expected_outcome?

### 1.2 Alignment Rules

| Check | Rule |
|-------|------|
| description → outcome | Every requirement mentioned in description must have a corresponding detail in expected_outcome |
| capabilities → outcome | Every item in required_capabilities must have a matching description in expected_outcome |
| Reverse check | outcome and capabilities must not introduce requirements absent from description |

### 1.3 Writing Principles

- **No parenthetical details.** Parenthetical content anchors the LLM judge to specific dimensions, undermining its macro-level evaluation. Detail checking is already handled by eval scripts and other fixed-paradigm evaluators.
- **No vague evaluative phrases** in expected_outcome such as "persona-adapted instruction quality" or "demonstrates runnable code execution" — the student simulator cannot derive actionable questions from such language.
- **No internal meta-commentary.** The description is passed to the student simulator, not to human developers. Sentences like "This task is distinct from D11" or "This covers the basics before D05" are developer context that the student simulator cannot act on. Keep description purely about what the student will learn.
- **Verb choice:** Use "Fetch" / "Save" / "Apply" for practical skills; use "Explain" for conceptual teaching. Avoid the ambiguous "Understand."
- **description** should be one sentence covering "what to do." **expected_outcome** should list concrete deliverables and knowledge points. **required_capabilities** should decompose into independently scoreable dimensions.

### 1.4 Common Failures

- description mentions a requirement but no corresponding capability exists — creates an evaluation blind spot
- expected_outcome uses generic language that gives the student simulator no basis for asking targeted questions
- A capability exists in required_capabilities but the eval script has no corresponding dimension — or vice versa

---

## 2. Tool Classification

### 2.1 Mutual Exclusivity

| Check | Rule |
|-------|------|
| core ∩ convenient = ∅ | `core_mcp_tools` and `convenient_tools` must not overlap |
| core ∩ distractor = ∅ | Guaranteed by registry.py, but core list should not contain tools from the distractor pool |
| convenient ⊂ CORE_TOOLS | Every convenient tool must be registered in the CORE_TOOLS dictionary in tools.py |

### 2.2 Classification Correctness

Per tool_design_philosophy, determine each tool's category:

| Question | Category |
|----------|----------|
| Is this capability a minimum prerequisite for task completion? Not replaceable by other atomic tools? | → Atomic: place in core_mcp_tools |
| Can this be replaced by atomic tool combinations, but encapsulates a domain-idiomatic operation? | → Convenient: place in convenient_tools |
| Relevant to quantitative finance but irrelevant to the current task? | → Distractor: sampled automatically by registry |

**Key point:** `search_web` and `search_docs` are classified as convenient tools per the design philosophy. They should not appear in `core_mcp_tools`.

### 2.3 Slot Count Verification

```
core + convenient + distractor = 15
distractor = 15 - len(core_mcp_tools) - len(convenient_tools)
```

Confirm the distractor count is a positive integer and the total across all three categories is exactly 15.

---

## 3. expected_mcp_tools

This field defines "the minimum set of tools an ideal agent will almost certainly call." Missing an expected tool incurs a **-0.15** penalty in the tool_usage scoring formula.

### 3.1 Rules

| Condition | Rule |
|-----------|------|
| Default | Only include `shell_exec` — agents almost always execute code through it |
| `docs_available` is non-empty | Add `get_environment_info` — the system prompt explicitly instructs the agent to call it to discover available docs before answering |
| `docs_available` is empty | Do **not** include `get_environment_info` — no system prompt instruction exists, and agents can discover paths through tool descriptions, file_list, or shell_exec; including it risks unfair penalties |
| Convenient tools | Never include in expected_mcp_tools — agents using atomic tools to achieve the same result is normal behavior, not a deficiency |
| file_read | Include only if the task flow near-certainly requires reading a file to proceed; otherwise omit |

---

## 4. student_openings

### 4.1 Core Rule

**Each opening poses only one initial question or learning entry point.** The student simulator has an internal PACING mechanism that progressively covers all learning objectives — the opening only needs to provide a natural starting point.

### 4.2 Checks

| Check | Description |
|-------|-------------|
| Single entry point | The opening should not enumerate all learning objectives |
| Persona-appropriate tone | beginner: express confusion, ask for simple guidance; intermediate: state a clear goal; advanced: demand rigor and depth |
| No capability leakage | The opening should not recite the full required_capabilities list |

**Diagnostic:** If the opening still works as a valid conversation starter after deleting everything after the first sentence, the remaining content is likely redundant.

---

## 5. requires_code

This field controls two critical paths and must be set correctly.

### 5.1 Impact

| requires_code | QR Blending Formula | System Prompt Guidance |
|:---:|---|---|
| true | 30% programmatic + 30% code_eval + 40% LLM judge | "Present key code snippets with explanations, break code into small chunks" |
| false | 40% programmatic + 60% LLM judge | "Focus on conceptual understanding, include short code snippets only when they help clarify" |

### 5.2 Misset Consequences

- **Should be true, set to false** — code_eval is skipped entirely; the agent's code quality is never evaluated. The system prompt also steers the agent away from writing code, contradicting the task's actual requirements.
- **Should be false, set to true** — code_eval attempts to parse .py files and shell_exec outputs from a conceptual task that produces little to no code, yielding near-zero scores that drag down the overall QR.

### 5.3 Decision Criterion

Does the student need to write or execute code as a necessary part of the learning process? If the teaching cannot proceed without code execution — even if the ultimate goal is conceptual understanding — set to true. Set to false only when the task can be completed entirely through verbal explanation without any code execution.

**Cross-validation:** If the eval script checks for code artifacts in workspace or parses shell_exec execution results, requires_code should be true.

---

## 6. Environment and Metadata

| Field | Check |
|-------|-------|
| `data_files` | Tasks requiring network data fetching should have this empty; tasks using local data should list all required files |
| `network_enabled` | Should be true when data_files is empty and the task requires external API access; false otherwise |
| `docs_available` | Every listed document must actually exist in bench/docs/reference/ |
| `sandbox_image` | Must be `quant-tutor-env:v2.2` |
| `difficulty` | Should be consistent with the number and complexity of required_capabilities |
| `sample_code` | Only used for debug-category tasks; null for all others |

---

## 7. Eval Script Consistency

| Check | Description |
|-------|-------------|
| Standard signature | Should include `*, data_files: list[str] = None` keyword parameter |
| Dimension mapping | Every scoring dimension in the eval script should map to at least one required_capability |
| No orphan dimensions | No eval script dimension should check something absent from required_capabilities |
| data_files empty | When `data_files` is empty, the script must not call `verify_data_source` |
| Attribute access | Use `log.name` / `log.args` / `log.result` — never dict-style `log["name"]` |

---

## Quick Reference Checklist

```
□ required_capabilities: each item independently assessable, no parenthetical details
□ expected_outcome: covers all capabilities, no vague evaluative phrases
□ description: covers all outcome points, one-sentence summary
□ Triad: no contradictions, no omissions
□ description: no internal meta-commentary (e.g. "distinct from D11")
□ core_mcp_tools ∩ convenient_tools = ∅
□ search_web / search_docs not in core_mcp_tools
□ 15-slot count is correct
□ expected_mcp_tools follows minimum-certainty principle
□ Has docs → expected includes get_environment_info
□ No docs → expected excludes get_environment_info
□ student_openings: each has only one entry point
□ requires_code: true if learning process requires code execution, even for conceptual goals
□ requires_code: cross-validated against eval script dimensions
□ network_enabled consistent with data_files
□ docs_available files exist on disk
□ Eval script dimensions map to required_capabilities
```
