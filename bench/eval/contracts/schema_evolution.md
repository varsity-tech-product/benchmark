# Bundle Schema Evolution

The Bundle schema is the contract between session capture and scoring.
Old bundles must remain readable indefinitely so historical sessions can
be re-scored with newer evaluators. The rules below balance immutability
of past data with room for legitimate future evolution.

## Versioning

Top-level `schema_version` is mandatory and uses `MAJOR.MINOR` semver:

- `MAJOR` bumps signal incompatible changes (field rename, semantic
  change, deletion). They require an explicit migration plan reviewed
  before merge.
- `MINOR` bumps are additive only. New optional fields are allowed; no
  rename, no deletion, no semantic change.

Current version: **`1.0`** (constant `SCHEMA_VERSION` in `bundle.py`).

## Reader compatibility

A reader declares the `MAJOR` versions it handles. The current reader
handles `1.x` only. Within a major version, reads are forward-compatible:

- Unknown top-level keys are ignored.
- Unknown keys in nested objects (`session`, `runtime`,
  `agent_metadata`, each conversation turn's nested objects, each tool
  call, each workspace manifest entry) are ignored.
- Missing optional fields fall back to dataclass defaults.
- `schema_version` is the only field that may abort the read; a v2.x
  bundle opened by the v1 reader raises `BundleError`.

This means a v1.0 reader correctly opens a future v1.5 bundle: the v1.0
fields populate, the v1.5 additions silently disappear. A v1.5 reader
opens both v1.0 and v1.5 bundles.

## Adding a field (MINOR bump)

1. Add the field to the dataclass in `bundle.py` with a sensible default
   (so older bundles that lack it deserialize cleanly).
2. Pull it from `from_dict()` in `bundle_io.py` with `dict.get()` and the
   same default.
3. Bump `SCHEMA_VERSION` from `1.N` to `1.N+1` in `bundle.py`. Any newly
   written bundle stamps the new version; old bundles keep their old
   stamp.
4. Update the writer (or backfill script) to populate the field.

No reader code changes are needed for older callers — they keep dropping
the unknown field.

## Renaming, removing, or changing semantics (MAJOR bump)

Forbidden in MINOR. To do any of these:

1. File an issue describing the migration.
2. Bump `SCHEMA_VERSION` to `2.0`.
3. Either ship a one-shot migration script that rewrites historical
   bundles, or extend the reader to handle both majors via a version
   dispatch.
4. Update consumers.

## Bundle immutability

A `bundle.json` is immutable once written. Re-evaluation produces new
files under `scores/score_v*_<judge_model>_<ts>.json`; the bundle itself
is never rewritten. This keeps `task_spec_hash` meaningful as an audit
anchor.

If the underlying conversation truly needs amendment (a privacy redaction,
a corrupted ts), write a new bundle directory rather than mutating the
existing file.
