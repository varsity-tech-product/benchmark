# Bundle Schema Evolution

The Bundle schema is the contract between session capture, scoring, and
research consumers. The current release is Stage 1 v0: a v1-family alpha
baseline that fixes the generic envelope while implementation pressure tests
continue.

## Versioning

Top-level `schema_version` is mandatory and uses semantic version strings.

- Current version: `1.0.0-alpha`
- The `1.x` family is the Bundle v1 family.
- Alpha versions can grow through additive fields and fixture-driven schema
  pressure tests during Stage 1.
- The Stage 2 freeze issue will define the stable `1.0.0` release and the
  compatibility window for historical bundles.

## Reader Compatibility

A reader declares the major versions it handles. The current reader handles
the `1.x` family.

- Unknown top-level keys are ignored by the dataclass reader.
- Unknown keys in nested objects are ignored by the dataclass reader.
- Missing optional fields fall back to dataclass defaults.
- `schema_version` gates the major-family dispatch.

JSON Schema validation is stricter than the dataclass reader. Use it for
producer tests and public fixtures:

```bash
python -m eval.contracts.bundle_schema bench/eval/contracts/fixtures/bundle_v1_alpha/impl_a_ref_harness.json
```

## Additive Changes During Alpha

1. Add the field to `bundle.py` with a default.
2. Pull it from `from_dict()` in `bundle_io.py`.
3. Update `bundle_v1_alpha.schema.json`.
4. Add or update a fixture that demonstrates the field.
5. Update `docs/bundle_v1_schema.md`.

## Stage 2 Placeholder

Stage 2 will fill in the migration policy after Impl A, Impl C, and Impl D
exercise the alpha schema end to end. The freeze issue should define:

- supported backward-compatibility window;
- conversion path for `1.0.0-alpha` fixtures;
- public deprecation policy;
- score artifact compatibility rules;
- workspace content reference rules for hashes, external paths, and inline
  payloads.

## Bundle Immutability

A `bundle.json` is immutable once written. Re-evaluation produces score
artifacts under the result directory or under `artifacts` in research exports.
Historical bundle data stays tied to its original `task_id`, transcript, tool
calls, and workspace file hashes.
