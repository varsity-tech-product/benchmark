# Score Report: D10_historical_data_fetch / beginner_no_finance

**Category**: data_analysis | **Difficulty**: easy | **Timestamp**: 2026-03-04 19:19:28

## Overall Scores

| Metric | Score |
|--------|-------|
| Overall Agent Score (OAS) | 0.3436 |
| Quant Result (QR) | 0.0117 |
| Quant Process (QP) | 0.3640 |
| Tutor Score (avg 7D) | 0.7302 |

## Quant Result (QR) Breakdown

### Code Execution Eval

- **Applicable**: True
- **Combined score**: 0.0000
- Layer A (static): 0.0000 — syntax=OK, files=0, funcs=0
- Layer B (execution): 0.0000 — calls=0, success_rate=0.00, untested=[]
- Layer C (output): SKIPPED (no reference)

### LLM Result Judge

- **Score (avg)**: 0.0292
- **Has reference**: False

| Sub-dimension | Avg | claude-opus-4.6 | claude-sonnet-4.6 | gpt-5.2 |
|---------------|-----|------|------|------|
| numerical_accuracy | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| completeness | 0.0833 | 0.0000 | 0.0000 | 0.2500 |
| correctness | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| **Overall** | 0.0292 | 0.0000 | 0.0000 | 0.0875 |

> The agent produced no files, no code, no CSV outputs, no API calls, and no numerical results whatsoever. The response consists only of incomplete, truncated text explaining OHLCV terminology. None of the task requirements were fulfilled: no Python code for fetching data, no market price dataset, no

### QR Blending

- **Final QR**: 0.0117
- Formula: 30% programmatic + 30% code_eval + 40% LLM judge

## Quant Process (QP) Breakdown

### Summary

| Metric | Weight | Avg | claude-opus-4.6 | claude-sonnet-4.6 | gpt-5.2 |
|--------|--------|-----|------|------|------|
| tool_usage | 0.20 | 0.3000 | — | — | — |
| step_efficiency | 0.15 | 0.4000 | 0.3000 | 0.6000 | 0.3000 |
| process_reasonableness | 0.20 | 0.0583 | 0.1750 | 0.0000 | 0.0000 |
| process_alignment | 0.10 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| code_process | 0.15 | — | — | — | — |
| role_adherence | 0.10 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| knowledge_retention | *(diag)* | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| topic_adherence | 0.10 | 0.7778 | 1.0000 | 0.5000 | 0.8333 |
| **Aggregate QP** | | **0.3640** | **0.4000** | **0.3529** | **0.3392** |

### Tool Usage Detail

| Component | Score |
|-----------|-------|
| Selection Score (60%) | 0.5000 |
| Effectiveness (40%) | 0.0000 |

| Diagnostic | Value |
|------------|-------|
| Base | 0.80 (has convenient) |
| Bonus | +0.0000 |
| Penalty (missing expected) | 0.3000 |
| Penalty (distractor) | 0.0000 |
| Called convenient | — |
| Missing expected | `shell_exec`, `file_read` |
| Called distractors | — |
| Ineffective expected | `shell_exec`, `file_read` |

### Step Efficiency Detail

| Sub-dimension | Weight | Avg | claude-opus-4.6 | claude-sonnet-4.6 | gpt-5.2 |
|---------------|--------|-----|------|------|------|
| action_economy | 0.40 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| redundancy_avoidance | 0.30 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| logical_sequencing | 0.30 | 0.3333 | 0.0000 | 1.0000 | 0.0000 |

> Agent steps: 0

### Process Reasonableness Detail

| Sub-dimension | Weight | Avg | claude-opus-4.6 | claude-sonnet-4.6 | gpt-5.2 |
|---------------|--------|-----|------|------|------|
| problem_decomposition | 0.30 | 0.0833 | 0.2500 | 0.0000 | 0.0000 |
| execution_soundness | 0.40 | 0.0833 | 0.2500 | 0.0000 | 0.0000 |
| error_handling | 0.30 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## Tutor Quality (7D) Breakdown

| Dimension | Avg | claude-opus-4.6 | claude-sonnet-4.6 | gpt-5.2 |
|-----------|-----|------|------|------|
| D1_level_detection | 0.7111 | 0.6333 | 0.7000 | 0.8000 |
| D2_language_adaptation | 0.6667 | 0.6667 | 0.6000 | 0.7333 |
| D3_scaffolding_calibration | 0.7222 | 0.7333 | 0.6333 | 0.8000 |
| D4_domain_accuracy | 0.6778 | 0.7333 | 0.5333 | 0.7667 |
| D5_code_teaching | 0.8111 | 0.8333 | 0.8000 | 0.8000 |
| D6_empathetic_response | 0.6889 | 0.6667 | 0.5667 | 0.8333 |
| D7_safety_boundaries | 0.8333 | 0.8333 | 0.8333 | 0.8333 |
| **Average** | 0.7302 | 0.7286 | 0.6667 | 0.7952 |

## Sandbox Info

- **container_id**: dc06f80b04c0
- **network_enabled**: True
- **network_mode**: bridge
- **use_docker**: True
- **sandbox_image**: quant-tutor-env:v2.0
