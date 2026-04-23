# Opener Policy

Live conversations may start with task fixture openings because they provide a
natural first student request for the tutor. These openings are data fixtures,
not generated model behavior.

Control conversations must start with a neutral generic opening:

```text
Hi, I need help understanding this quantitative finance task.
```

Both fixture and control openings are excluded from D1-D4 and control
student-message scoring. Only turns marked with `source: "student_model"` are
rendered into judge prompts.
