# Human Review

`bench/experiments/human_review/` stores structured reviewer opinion cards for
archived session bundles. The console reads completed bundles from
`bench/results/server/` and writes one JSON file per bundle and GitHub reviewer:

```text
human_review/{bundle_id}/{github_user_id}.json
```

Each file is append-only from the UI perspective and uses
`human_review_opinions_v1`. The fields `sample_id`, `reviewer_id`,
`label_version`, and `timestamp` are kept so downstream tools can join this
artifact shape with the earlier `judge_validation/human_labels*.json` exports.

Opinion cards use one primary `section`:

- `task_spec`
- `conversation`
- `tool_log`
- `workspace`
- `judge_eval`
- `overall`

The optional `target` object points at the reviewed row:
`turn_index`, `tool_call_index`, `file_path`, or `criterion_id`.
