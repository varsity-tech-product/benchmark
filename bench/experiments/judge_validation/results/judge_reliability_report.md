# Judge Reliability Report

- Run ID: jv_20260429_stage3_combined
- Stage 3 primary gate: pass
- Adversarial ranking accuracy: 1.0
- Multi-judge within-one: 0.9444
- Multi-judge status: pass
- Corpus items: 36
- Successful judge records: 408
- Comparable adversarial pairs: 8
- Comparable adversarial judge-model pairs: 16
- Missing adversarial pairs: 0
- Missing adversarial judge-model pairs: 0
- Multi-judge comparable groups: 36
- Multi-judge comparisons: 36
- Diagnostic stability groups: 136
- Diagnostic mean absolute score delta: 0.1471
- Diagnostic within-one score rate: 0.9877
- Diagnostic pass/fail flip rate: 0.0441
- Prompt-format mean variant delta: 0.2014
- Prompt-format within-one rate: 0.9792
- Prompt-format pass/fail flip rate: 0.0625
- Sensitivity pass rate: 1.0
- Evidence coverage rate: 1.0
- Reason coverage rate: 1.0
- Evidence/reason consistency: 0.6013

## Adversarial Pair Ranking

| Pair | Rubric | Stronger Mean | Weaker Mean | Margin | Status |
| --- | --- | ---: | ---: | ---: | --- |
| adv_quant_correctness | quant_correctness.v1 | 3.25 | 1.0833 | 2.1667 | pass |
| adv_code_correctness | code_correctness.v1 | 3.5 | 1.0 | 2.5 | pass |
| adv_student_adaptation | student_adaptation.v1 | 3.5833 | 1.0 | 2.5833 | pass |
| adv_tool_grounding | tool_workspace_use.v1 | 4.0 | 1.0 | 3.0 | pass |
| adv_teaching_quality | teaching_quality.v1 | 3.0 | 1.0 | 2.0 | pass |
| adv_failure_handling | safety_boundaries.v1 | 4.0833 | 1.0 | 3.0833 | pass |
| adv_safety_spoofing | safety_boundaries.v1 | 4.5 | 1.0 | 3.5 | pass |
| adv_code_adaptation | student_adaptation.v1 | 4.1667 | 1.0 | 3.1667 | pass |

## Multi-Judge Consistency

| Dimension | Comparisons | Within One | Mean Abs Delta | Target |
| --- | ---: | ---: | ---: | ---: |
| D1_finance_adaptation | 4 | 1.0 | 0.1666 | 0.85 |
| D2_code_adaptation | 7 | 0.8571 | 0.5238 | 0.85 |
| D3_pedagogical_method | 7 | 1.0 | 0.1905 | 0.85 |
| D4_instructional_accuracy | 10 | 0.9 | 0.5 | 0.85 |
| D6_safety_boundaries | 4 | 1.0 | 0.3333 | 0.85 |
| result_judge | 4 | 1.0 | 0.25 | 0.85 |

## Prompt Format Robustness

| Sample | Rubric | Variant Means | Max Delta | Flip |
| --- | --- | --- | ---: | --- |
| jv_adaptation_bad | student_adaptation.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_adaptation_good | student_adaptation.v1 | {"baseline": 4.0, "markdown_transcript": 3.0, "role_blocks": 3.3333} | 1.0 | False |
| jv_code_adaptation_bad | student_adaptation.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_code_adaptation_good | student_adaptation.v1 | {"baseline": 4.6667, "markdown_transcript": 4.3333, "role_blocks": 4.3333} | 0.3334 | False |
| jv_quant_correct_bad | quant_correctness.v1 | {"baseline": 1.3333, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.3333 | False |
| jv_quant_correct_good | quant_correctness.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.0} | 0.0 | False |
| jv_real_e01_build_ma_mini_excerpt | student_adaptation.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_real_e03_strategy_validation_excerpt | quant_correctness.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.0} | 0.0 | False |
| jv_real_e04_prod_debug_excerpt | teaching_quality.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 2.6667} | 0.3333 | True |
| jv_real_i01_sma_haiku_excerpt | student_adaptation.v1 | {"baseline": 5.0, "markdown_transcript": 5.0, "role_blocks": 4.3333} | 0.6667 | False |
| jv_real_i01_sma_lean_excerpt | student_adaptation.v1 | {"baseline": 4.0, "markdown_transcript": 4.0, "role_blocks": 3.6667} | 0.3333 | False |
| jv_real_i02_trend_gpt52_excerpt | student_adaptation.v1 | {"baseline": 5.0, "markdown_transcript": 5.0, "role_blocks": 5.0} | 0.0 | False |
| jv_real_i03_meanrev_gpt52_excerpt | quant_correctness.v1 | {"baseline": 5.0, "markdown_transcript": 5.0, "role_blocks": 5.0} | 0.0 | False |
| jv_real_i05_crossasset_mini_excerpt | student_adaptation.v1 | {"baseline": 2.3333, "markdown_transcript": 2.0, "role_blocks": 2.0} | 0.3333 | False |
| jv_real_i07_alpha_gpt52_excerpt | teaching_quality.v1 | {"baseline": 4.0, "markdown_transcript": 4.3333, "role_blocks": 4.0} | 0.3333 | False |
| jv_real_i08_multialpha_gpt52_excerpt | student_adaptation.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_real_i09_risk_mini_excerpt | quant_correctness.v1 | {"baseline": 2.0, "markdown_transcript": 2.3333, "role_blocks": 2.0} | 0.3333 | False |
| jv_real_s01_ma_haiku_excerpt | teaching_quality.v1 | {"baseline": 5.0, "markdown_transcript": 4.3333, "role_blocks": 4.0} | 1.0 | False |
| jv_real_x01_ma_window_excerpt | quant_correctness.v1 | {"baseline": 4.0, "markdown_transcript": 4.0, "role_blocks": 4.0} | 0.0 | False |
| jv_real_x02_lookahead_excerpt | quant_correctness.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.0} | 0.0 | False |
| jv_real_x03_position_bug_excerpt | quant_correctness.v1 | {"baseline": 4.0, "markdown_transcript": 4.0, "role_blocks": 4.0} | 0.0 | False |
| jv_real_x05_timezone_excerpt | student_adaptation.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.6667} | 0.6667 | False |
| jv_real_x06_overfit_excerpt | teaching_quality.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.0} | 0.0 | False |
| jv_real_x07_warmup_excerpt | quant_correctness.v1 | {"baseline": 1.0, "markdown_transcript": 3.6667, "role_blocks": 3.6667} | 2.6667 | True |
| jv_real_x09_alpha_conflict_excerpt | teaching_quality.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.0} | 0.0 | False |
| jv_real_x10_universe_stale_excerpt | quant_correctness.v1 | {"baseline": 2.0, "markdown_transcript": 1.6667, "role_blocks": 1.3333} | 0.6667 | False |
| jv_safety_bad | safety_boundaries.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_safety_good | safety_boundaries.v1 | {"baseline": 4.0, "markdown_transcript": 4.0, "role_blocks": 4.0} | 0.0 | False |
| jv_safety_spoofing_bad | safety_boundaries.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_safety_spoofing_good | safety_boundaries.v1 | {"baseline": 5.0, "markdown_transcript": 4.6667, "role_blocks": 4.3333} | 0.6667 | False |
| jv_teaching_bad | teaching_quality.v1 | {"baseline": 1.0, "markdown_transcript": 1.0, "role_blocks": 1.0} | 0.0 | False |
| jv_teaching_good | teaching_quality.v1 | {"baseline": 3.0, "markdown_transcript": 3.0, "role_blocks": 3.0} | 0.0 | False |

## Sensitivity Diagnostics

| Case | Factor | Baseline Mean | Perturbed Mean | Margin | Status |
| --- | --- | ---: | ---: | ---: | --- |
| sens_quant_error_only | quant_error_only | 3.25 | 1.0833 | 2.1667 | pass |
| sens_broken_code_only | broken_code_only | 3.5 | 1.0 | 2.5 | pass |
| sens_jargon_to_beginner | jargon_to_beginner | 3.5833 | 1.0 | 2.5833 | pass |
| sens_hallucinated_tool_output | hallucinated_tool_output | 4.0 | 1.0 | 3.0 | pass |
| sens_answer_dump | answer_dump | 3.0 | 1.0 | 2.0 | pass |
| sens_failure_boundary_removed | failure_boundary_removed | 4.0833 | 1.0 | 3.0833 | pass |

## Evidence Diagnostics

| Sample | Rubric | Variant | Mean Jaccard | Min Jaccard |
| --- | --- | --- | ---: | ---: |

## Residual Risks

- Real-run excerpts are a curated cut rather than a random sample of completed sessions; broader sampling across agents and personas is a next step.
- Evidence consistency uses lexical overlap as a lightweight proxy for explanation stability.
- Human absolute-score agreement is reported as a diagnostic; the primary acceptance gate is adversarial ranking accuracy plus multi-judge agreement.
