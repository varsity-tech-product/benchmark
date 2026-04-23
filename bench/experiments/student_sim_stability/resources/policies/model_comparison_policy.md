# Model Comparison Policy

The experiment compares student-simulator models as implementations of the
same persona contract. D3 judge prompts anonymize model names as System A/B/C
and store the label mapping in metadata for post-hoc analysis.

Model ranking should be treated as exploratory unless:

- D1, D2, D3, D4, and control all pass strict validation.
- Control distinctiveness confirms that persona conditioning adds observable
  behavior beyond a generic student.
- D4 output quality checks find no template-like duplicate score clusters.
- Any human-alignment or multi-judge extensions are clearly marked as present
  or absent.
