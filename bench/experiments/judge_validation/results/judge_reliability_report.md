# Judge Reliability Report

- Run ID: jv_20260424_025507
- Corpus items: 14
- Successful judge records: 102
- Stability groups: 34
- Mean absolute score delta: 0.1176
- Within-one score rate: 0.9804
- Pass/fail flip rate: 0.0294
- Prompt-format mean variant delta: 0.1556
- Prompt-format within-one rate: 1.0
- Prompt-format pass/fail flip rate: 0.0
- Adversarial ranking pass rate: 1.0
- Sensitivity pass rate: 1.0
- Evidence coverage rate: 1.0
- Reason coverage rate: 1.0
- Evidence/reason consistency: 0.6378

## Prompt Format Robustness

| Sample | Rubric | Variant Means | Max Delta | Flip |
| --- | --- | --- | ---: | --- |
| jv_adaptation_bad | student_adaptation.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_adaptation_good | student_adaptation.v1 | {"baseline": 4.0, "markdown_transcript": 3.6667, "role_blocks": 3.0} | 1.0 | False |
| jv_quant_correct_bad | quant_correctness.v1 | {"baseline": 1.3333, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.3333 | False |
| jv_quant_correct_good | quant_correctness.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.3333} | 0.3333 | False |
| jv_real_i01_sma_lean_excerpt | student_adaptation.v1 | {"baseline": 4.0, "markdown_transcript": 4.0, "role_blocks": 3.3333} | 0.6667 | False |
| jv_real_x02_lookahead_excerpt | quant_correctness.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.0} | 0.0 | False |
| jv_safety_bad | failure_handling.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_safety_good | failure_handling.v1 | {"baseline": 4.0, "markdown_transcript": 4.0, "role_blocks": 4.0} | 0.0 | False |
| jv_teaching_bad | teaching_quality.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_teaching_good | teaching_quality.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.0} | 0.0 | False |

## Sensitivity Cases

| Case | Factor | Baseline Mean | Perturbed Mean | Margin | Status |
| --- | --- | ---: | ---: | ---: | --- |
| sens_quant_error_only | quant_error_only | 3.1111 | 1.1111 | 2.0 | pass |
| sens_broken_code_only | broken_code_only | 4.0 | 1.0 | 3.0 | pass |
| sens_jargon_to_beginner | jargon_to_beginner | 3.5556 | 1.0 | 2.5556 | pass |
| sens_hallucinated_tool_output | hallucinated_tool_output | 4.6667 | 1.0 | 3.6667 | pass |
| sens_answer_dump | answer_dump | 3.0 | 1.0 | 2.0 | pass |
| sens_failure_boundary_removed | failure_boundary_removed | 4.0 | 1.0 | 3.0 | pass |

## Evidence Consistency

| Sample | Rubric | Variant | Mean Jaccard | Min Jaccard |
| --- | --- | --- | ---: | ---: |

## Adversarial Pairs

| Pair | Rubric | Stronger Mean | Weaker Mean | Margin | Status |
| --- | --- | ---: | ---: | ---: | --- |
| adv_quant_correctness | quant_correctness.v1 | 3.1111 | 1.1111 | 2.0 | pass |
| adv_code_correctness | code_correctness.v1 | 4.0 | 1.0 | 3.0 | pass |
| adv_student_adaptation | student_adaptation.v1 | 3.5556 | 1.0 | 2.5556 | pass |
| adv_tool_grounding | tool_workspace_use.v1 | 4.6667 | 1.0 | 3.6667 | pass |
| adv_teaching_quality | teaching_quality.v1 | 3.0 | 1.0 | 2.0 | pass |
| adv_failure_handling | failure_handling.v1 | 4.0 | 1.0 | 3.0 | pass |

## Residual Risks

- Automated robustness checks use a compact pilot corpus and should be expanded with more completed transcripts.
- Evidence consistency uses lexical overlap as a lightweight proxy for explanation stability.
- Human quant expert alignment belongs to the next validation stage after automated robustness artifacts are stable.
