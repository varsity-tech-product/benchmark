# Judge Policy

Judge prompts are rubric-first artifacts. Each rendered input includes the
dimension, prompt, metadata, source files, and student-turn source policy.

Required judge output fields:

- D1: `reasoning`, `knowledge_boundary`, `emotional_tone`,
  `behavioral_rules`, `overall`
- D2: `reasoning`, `topic_trajectory`, `knowledge_display`,
  `emotional_consistency`, `question_patterns`, `overall_reproducibility`
- D3: `reasoning`, `per_turn`, `overall_drift_score`, `drift_onset_turn`
- Control: `reasoning`, `distinctiveness`, `persona_value_add`

The production judge model for this run is `anthropic/claude-sonnet-4-6` at
temperature `0.0`.
