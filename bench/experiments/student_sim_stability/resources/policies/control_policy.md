# Control Policy

The control group measures whether persona-conditioned conversations are
behaviorally distinguishable from generic-student conversations.

Required properties:

- Control uses the same task and student model as the paired live conversation.
- Control is generated for every task/persona/model combination.
- Control uses a generic student description, not a copied persona description.
- Control uses the neutral opener, not a persona-specific task opening.
- Control requires real `control__*.json` judge inputs and outputs.
- Control aggregate rows must come only from the distinguishability rubric.

The aggregate step must not synthesize control rows from D3 drift scores.
