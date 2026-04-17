# QP/QR LLM Judge Prompt Audit

> Date: 2026-04-17
> Scope: All LLM-judged dimensions in QR (Quant Result) and QP (Quant Process) scoring
> Source files: `server/eval/ewan_eval/result_judge.py`, `process_reasonableness.py`, `process_metrics.py`, `code_process.py`, `custom_conv_metrics.py`
> Status: Audit only, no modifications

---

## 1. LLM-Judged Dimension Inventory

### QR (Quant Result) — 1 prompt, 2 sub-dimensions

| Sub-dimension | Scale | Weight | Source |
|---------------|-------|--------|--------|
| Completeness | 1-10 integer | 0.55 | `result_judge.py` L198-229 |
| Correctness | 1-10 integer | 0.45 | `result_judge.py` L215-229 |

QR also includes programmatic components (eval_script, code_eval Layer C) blended via dampening. The LLM judge receives task description, expected outcome, reference result, and agent result.

### QP (Quant Process) — 5 prompts, 14 sub-dimensions

| Prompt | Sub-dimensions | Scale | Dim Weight in QP |
|--------|---------------|-------|------------------|
| process_reasonableness | Problem Decomposition (0.30), Execution Soundness (0.40), Error Handling (0.30) | {0.0, 0.25, 0.5, 0.75, 1.0} | 0.20 |
| step_efficiency | Action Economy (0.40, **programmatic**), Redundancy Avoidance (0.30), Logical Sequencing (0.30) | {0.0, 0.25, 0.5, 0.75, 1.0} | 0.15 |
| code_process | Debugging Competence (1/3), Incremental Development (1/3), Code Explanation Quality (1/3) | {0.0, 0.25, 0.5, 0.75, 1.0} | 0.15 |
| role_adherence | Persona Consistency (0.50), Boundary Maintenance (0.50) | 1-10 integer | 0.10 |
| topic_adherence | Topic Relevance (0.50), Task Focus (0.50) | 1-10 integer | 0.10 |

Additionally, `tool_usage` (0.20 weight) is fully programmatic (no LLM judge).

---

## 2. Full Prompt Text

### 2.1 QR Result Judge

**File**: `result_judge.py:95-289`

```
You are evaluating the RESULT QUALITY of an AI tutoring agent's task execution.

SCORING SCALE: Rate each dimension as an INTEGER from 1 to 10.
Use the full range — avoid defaulting to middle scores (5-6) without justification.

TASK: {task_description}
CATEGORY: {category}
EXPECTED OUTCOME (acceptance criteria): {expected_outcome}
REFERENCE RESULT (expert baseline):
- Key metrics: {ref_key_results}
- Files produced: {ref_workspace_files}
- Execution trace (last steps): {ref_trace}

AGENT RESULT:
- Files produced: {agent_workspace_files}
- Key tool outputs: {agent_key_outputs}
- Agent's explanation (summary): {agent_summary}

IMPORTANT EVALUATION GUIDELINES:
1. SUBSET VARIATION IS EXPECTED: Different time periods/subsets/parameters produce
   different values. Only flag contradictions when identical inputs yield conflicting outputs.
2. SEPARATE CORRECTNESS FROM QUALITY: Judge implementation soundness, not whether
   results are impressive.
3. ACCEPT ALTERNATIVE METHODS: Different valid approaches to the same task are acceptable.
4. INTERMEDIATE OUTPUT IS NORMAL: Exploratory analysis and iteration are expected.
5. CODE MUST BE EXECUTED TO COUNT: Unexecuted code/drafts don't count as results.
   Only credit outputs from tools marked [OK].
6. [debug only] DEBUG TASKS: "Fix" means resolving the identified bug, not producing
   profitable results.

EVALUATE these TWO dimensions (integer 1-10):

1. COMPLETENESS (1-10):
   [with reference] Did agent produce ALL expected outputs compared to the reference?
   [without reference] Given the task requirements, did agent produce all expected outputs?
   10: All outputs present with full detail
    5: Core outputs partially present; several items missing
    1: No meaningful outputs produced

2. CORRECTNESS (1-10):
   10: All outputs are runnable/usable, formats match expectations, results fully actionable
    5: Core outputs present but some are unusable or in wrong format
    1: Outputs are entirely unusable or missing

Return ONLY a JSON object:
{"completeness": <integer 1-10>, "correctness": <integer 1-10>, "reason": "<brief explanation>"}
```

### 2.2 QP Process Reasonableness

**File**: `process_reasonableness.py:170-258`

```
You are evaluating the PROCESS QUALITY of an AI tutoring agent's execution.

SCORING SCALE: Use ONLY these values: {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt between two levels, select the LOWER score.

TASK: {task}
CATEGORY: {category}
AGENT EXECUTION TRACE: {agent_trace}

NEUTRALITY RULES (MUST follow):
- Custom/DIY implementations (shell_exec + file_write) are EQUALLY VALID as convenience tools
- Do NOT penalize for choosing to build functionality manually
- Not using tools when task can be answered from knowledge alone is VALID
- Evaluate the LOGIC and CORRECTNESS of the approach, not tool selection

CATEGORY-SPECIFIC CRITERIA: {category_criteria}

EVALUATE on 3 dimensions:

1. PROBLEM DECOMPOSITION (0.0-1.0):
   Did the agent break the task into logical sub-steps?
   1.0: Clear, logical decomposition; dependencies identified before acting
   0.5: Some structure but missing key sub-steps
   0.0: No decomposition, chaotic execution

2. EXECUTION SOUNDNESS (0.0-1.0):
   Were actions logically sound for achieving the goal?
   1.0: All actions well-reasoned and effective
   0.5: Some sound actions mixed with questionable choices
   0.0: Fundamentally wrong approach

3. ERROR HANDLING (0.0-1.0):
   [For code tasks: Focus on NON-CODE errors only]
   When errors occurred, did the agent correctly diagnose and fix?
   1.0: Excellent error diagnosis and recovery (or no errors occurred)
   0.5: Recovered from some errors but missed others
   0.0: No error handling or made errors worse

Return ONLY a JSON object:
{"problem_decomposition": <float>, "execution_soundness": <float>,
 "error_handling": <float>, "reason": "<brief explanation>"}
```

### 2.3 QP Step Efficiency

**File**: `process_metrics.py:189-419`

Action Economy is **programmatic** (ratio-based thresholds). The LLM judges 2 remaining sub-dimensions:

```
You are evaluating the STEP EFFICIENCY of a tool-augmented tutoring agent.

SCORING SCALE: Use ONLY these values: {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt between two levels, select the LOWER score.

TASK: {task}
REFERENCE EXECUTION (expert baseline):
- Substantive steps: {ref_step_count}
- Trace: {ref_trace_summary}
AGENT EXECUTION:
- Substantive steps: {agent_steps}
- Trace: {agent_trace}

NOTE ON TOOL TIERS:
Using convenience tools is efficient. Building equivalent functionality with
shell_exec + file_write is equally valid. Judge step count relative to the
approach taken, not against the shortcut.

ACTION ECONOMY (pre-computed): {score} (ratio: {ratio})
This score is already calculated. Do NOT re-evaluate it.

Evaluate the following TWO dimensions:

1. REDUNDANCY AVOIDANCE (0.0-1.0):
   Red flags: Same tool with identical arguments, fetching data never used,
   re-computing already obtained values, calling tools after answer is known.
   Acceptable: Retrying after error with different parameters, progressive refinement.
   1.0: No redundant calls
   0.5: Some redundancy
   0.0: Pervasive waste

2. LOGICAL SEQUENCING (0.0-1.0):
   Good: fetch data -> compute indicator -> analyze -> visualize
   Bad: visualize before data exists, compute before dependencies ready
   1.0: Perfect logical flow
   0.5: Some out-of-order actions
   0.0: Chaotic/random ordering

Return ONLY a JSON object:
{"redundancy_avoidance": <float>, "logical_sequencing": <float>,
 "reason": "<brief explanation>"}
```

### 2.4 QP Code Process

**File**: `code_process.py:273-420`

Programmatic half (50%): Iterative Refinement, Test Before Deliver, Error Recovery, Code Evolution. LLM judges remaining 50%:

```
You are evaluating the CODE DEVELOPMENT PROCESS of an AI tutoring agent.

SCORING SCALE: Use ONLY these values: {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt between two levels, select the LOWER score.

TASK: {task}
CODE ACTIVITY TRACE (write/exec events only): {activity_trace}
AGENT CONVERSATION (excerpt): {output_preview}

EVALUATE on 3 dimensions:

1. DEBUGGING COMPETENCE (0.0-1.0):
   When code fails, does the agent correctly diagnose the root cause?
   Targeted fixes vs blind trial-and-error?
   1.0: Excellent diagnosis, precise fix
   0.5: Some correct fixes mixed with guesswork
   0.0: No debugging ability or makes errors worse
   If no errors occurred, score based on code quality that prevented errors.

2. INCREMENTAL DEVELOPMENT (0.0-1.0):
   Does the agent build progressively (small steps, testing each)?
   1.0: Excellent progressive development
   0.5: Mix of incremental and bulk-written code
   0.0: Big-bang — entire solution written and tested only at the end

3. CODE EXPLANATION QUALITY (0.0-1.0):
   Does the agent explain code to the student (as a tutor)?
   1.0: Every code block explained; student understands purpose and logic
   0.5: Some explanations; student has partial understanding
   0.0: Code provided with no explanation whatsoever

Return ONLY a JSON object:
{"debugging_competence": <float>, "incremental_development": <float>,
 "code_explanation_quality": <float>, "reason": "<brief explanation>"}
```

### 2.5 QP Role Adherence

**File**: `custom_conv_metrics.py:120-178`

```
You are evaluating ROLE ADHERENCE of a quantitative finance tutor agent.

ROLE DEFINITION: The agent is a "quantitative finance tutor" who teaches through:
- Explaining financial/technical concepts in student-appropriate language
- Using tools to prepare real data and demonstrate concepts concretely
- Writing and executing Python code to show analysis steps
- Providing step-by-step guidance, answering questions, scaffolding learning

CRITICAL RULE: Tool usage and code execution are CORE tutoring activities,
NOT deviations from role. Only flag deviations where the agent clearly
abandons the teaching context.

SCORING SCALE: Rate each dimension as an INTEGER from 1 to 10.

Evaluate these 2 sub-dimensions:

1. PERSONA_CONSISTENCY (1-10):
   Does the agent maintain a consistent tutor persona throughout?
   10: Every substantive response includes educational framing
    5: Mixed — sometimes teaches, sometimes just silently executes
    1: No evidence of tutoring behavior; purely mechanical

2. BOUNDARY_MAINTENANCE (1-10):
   Does the agent stay within the tutor role boundaries?
   10: Consistently acts as educator; all content serves learning objectives
    5: Acceptable; some content doesn't serve educational purpose
    1: Completely abandons tutor role

CONVERSATION: {turns_text}

Return JSON:
{"persona_consistency": <int 1-10>, "boundary_maintenance": <int 1-10>,
 "reason": "<1-3 sentences>"}
```

### 2.6 QP Topic Adherence

**File**: `custom_conv_metrics.py:224-281`

```
You are evaluating TOPIC ADHERENCE of a quantitative finance tutor agent.

TASK CONTEXT: {task_description}
RELEVANT TOPIC DOMAINS: {topics_list}

CRITICAL CONTEXT FOR TOOL-USE AGENTS:
- Tool outputs (data tables, code results, numerical output) are ON-TOPIC
  when they serve the task.
- Python code for financial analysis is ON-TOPIC.
- Brief environment/metadata responses are neutral setup, not off-topic.
- Only flag content genuinely UNRELATED to the task (e.g., cooking recipes).

SCORING SCALE: Rate each dimension as an INTEGER from 1 to 10.

Evaluate these 2 sub-dimensions:

1. TOPIC_RELEVANCE (1-10):
   How well does conversation content align with task and topic domains?
   10: All substantive content directly serves the task
    5: Mixed — significant portions relevant but notable off-topic content
    1: Entirely off-topic

2. TASK_FOCUS (1-10):
   Does the agent stay focused on specific task objectives?
   10: Agent consistently works toward stated task goals
    5: Partially focused; some work doesn't contribute
    1: No focus on the stated task

CONVERSATION: {turns_text}

Return JSON:
{"topic_relevance": <int 1-10>, "task_focus": <int 1-10>,
 "reason": "<1-3 sentences>"}
```

---

## 3. Issues Found

### Issue 1: Role Adherence overlaps with Tutor D3 — double penalty

**Severity: High | Type: Cross-boundary overlap**

Role Adherence **Persona Consistency** evaluates:
> "10: Every substantive response includes educational framing (explains what, why, how)"
> "5: Mixed — sometimes teaches, sometimes just silently executes"

Tutor **D3 Pedagogical Method** evaluates:
> Teaching process responsiveness, structure, interactivity

Both dimensions penalize an agent that executes code without explaining it. An agent scoring poorly on "educational framing" gets penalized in QP (Role Adherence, 10% weight) AND in Tutor (D3, ~20% of Tutor score which is 30% of total). The same behavior is punished twice in different scoring pipelines.

**Impact**: Agents with poor pedagogical framing receive compounded penalties disproportionate to the severity, since the same observable behavior (silent execution) triggers two independent judge calls that each lower the score.

### Issue 2: Code Explanation Quality belongs in Tutor, not QP

**Severity: High | Type: Misattribution**

Code Process **Code Explanation Quality** evaluates:
> "Does the agent explain code to the student (as a tutor)?"
> "1.0: Every code block explained; student understands purpose and logic"
> "0.0: Code provided with no explanation whatsoever"

This is explicitly a teaching quality metric — it measures the agent's communication with the student about code, not the agent's code development process. It belongs in the Tutor evaluation pipeline alongside D3 (pedagogy) and D2 (code adaptation), not in QP which should measure the agent's internal process quality.

**Impact**: Teaching quality leaks into the QP score, which is supposed to measure process competence independently of tutoring skill. An agent could write excellent code with a flawless development process but score poorly on QP because it didn't explain the code well enough.

### Issue 3: Inconsistent scoring scales across prompts

**Severity: Low | Type: Design inconsistency**

| Prompt | Scale | Granularity |
|--------|-------|-------------|
| QR (Completeness, Correctness) | 1-10 integer | 10 levels |
| process_reasonableness | {0.0, 0.25, 0.5, 0.75, 1.0} | 5 levels |
| step_efficiency | {0.0, 0.25, 0.5, 0.75, 1.0} | 5 levels |
| code_process | {0.0, 0.25, 0.5, 0.75, 1.0} | 5 levels |
| role_adherence | 1-10 integer | 10 levels |
| topic_adherence | 1-10 integer | 10 levels |

All scores are ultimately normalized to 0-1, but role_adherence and topic_adherence have 2x the resolution of process_reasonableness. This means small distinctions in role/topic adherence (e.g., 7 vs 8) are preserved, while process reasonableness collapses everything between "mostly sound" and "excellent" into the same 0.75 bucket.

The mismatch is not necessarily wrong — the 5-point ordinal scale was a deliberate design choice for process dimensions (reducing LLM judge variance). But the inconsistency should be documented and justified.

### Issue 4: Topic Adherence has near-zero discrimination

**Severity: Medium | Type: Wasted evaluation budget**

The prompt explicitly states:
> "Only flag content that is genuinely UNRELATED to the task (e.g., discussing cooking recipes, unrelated personal topics)"

In a quantitative finance tutoring benchmark where the agent has domain-specific tools and a task-focused prompt, deviating to "cooking recipes" is essentially impossible. Expected score distribution: 9-10 for nearly all samples.

Topic Adherence occupies 10% of QP weight but contributes no discrimination between agents. The LLM judge call (~$0.01-0.03 per evaluation) produces no useful signal.

### Issue 5: Error Handling rewards simplicity over competence

**Severity: Low | Type: Systematic bias**

The prompt states:
> "1.0: Excellent error diagnosis and recovery **(or no errors occurred)**"

This means:
- Agent on an easy task that never encounters errors → automatic 1.0
- Agent on a hard task that encounters errors and expertly recovers → 0.75 at best (due to "minor diagnostic gaps")

Simple tasks receive a systematic scoring advantage on this dimension. The "or no errors occurred" clause conflates "no errors needed handling" with "excellent error handling."

### Issue 6: QR "Correctness" name misleads the judge

**Severity: Medium | Type: Naming/semantic mismatch**

QR Correctness evaluates:
> "10: All outputs are runnable/usable, formats match expectations, results fully actionable"
> "1: Outputs are entirely unusable or missing"

The word "Correctness" implies numerical/logical correctness ("are the numbers right?"), but the actual rubric evaluates format usability ("are outputs runnable/usable?"). Numerical correctness is handled separately by programmatic code_eval (Layer C) and eval_script.

An LLM judge seeing the label "Correctness" may attempt to verify numerical accuracy — which is not its job and which it cannot reliably do from a text trace. This name-content mismatch may introduce unpredictable scoring behavior.

**Suggested rename**: "Output Usability" or "Format Quality" to match what is actually being evaluated.

---

## 4. Summary

| # | Type | Severity | Description | Affected Score |
|---|------|----------|-------------|----------------|
| 1 | Overlap | High | Role Adherence (Persona Consistency) duplicates Tutor D3 evaluation | QP 10% + Tutor 30% |
| 2 | Misattribution | High | Code Explanation Quality is a teaching metric in QP | QP code_process 15% |
| 3 | Inconsistency | Low | 5-point vs 10-point scales mixed across QP dimensions | QP all |
| 4 | Low signal | Medium | Topic Adherence has near-zero discrimination in this domain | QP 10% |
| 5 | Bias | Low | Error Handling auto-1.0 for error-free tasks favors simple tasks | QP process_reasonableness 20% |
| 6 | Naming | Medium | QR "Correctness" name implies numerical accuracy, rubric evaluates format usability | QR 45% of LLM component |
