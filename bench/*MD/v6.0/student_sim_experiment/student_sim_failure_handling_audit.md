# Student Simulator Failure Handling Audit

> Date: 2026-04-21
> Scope: `server/core/student_sim.py`, `server/core/session.py`, and downstream
> experiment/eval data integrity
> Status: revised after code-level review; supersedes first draft

---

## 1. Why This Matters

`StudentSimulator` is part of the benchmark data-generating process. If it
fails silently, the benchmark can record conversations that look complete but
were actually terminated by simulator/network/parse errors.

For product UX, a soft fallback may be acceptable. For benchmark data, the
priority should be different: tolerate harmless formatting variance, but never
hide simulator failures as normal completed sessions.

---

## 2. Current Behavior

### 2.1 StudentSimulator parsing

The simulator asks the model to return:

```json
{"simulated_input": "..."}
```

Current behavior:

- Extract `simulated_input` from a JSON object via `_parse_simulated_input_strict`.
- If no JSON is found, treat raw text as natural-language fallback only if
  `_is_usable_student_message()` accepts it.
- Retry generation up to 3 times across both network errors and parse errors
  (unified budget via `_MAX_GENERATE_ATTEMPTS = 3`).
- If all attempts fail, raise `StudentSimError`.

The new `StudentSimError` records:

- `error_type`, such as `network` or `parse`;
- `attempts`, including attempt index, timestamp, output length, cost, and
  error detail.

This is directionally good: prompt leakage or malformed model output is no
longer silently accepted as a real student turn.

### 2.2 Session-level handling

In `TutoringSession.handle_send_message()`, simulator exceptions are caught.
The session is marked done and the result is returned as:

```python
self._done = True
self._completion_reason = "student_sim_error:<type>"
return self._result("", "completed", reason=reason)
```

That means a simulator failure currently becomes:

- `status = "completed"`;
- `reason = "student_sim_error:<type>"`;
- empty student reply returned to the caller.

The conversation does not append the empty student turn, which is good. The
problem is that the external status still looks like a normal completion unless
the caller explicitly inspects `reason`.

---

## 3. Problems Found

### 3.1 Legitimate short replies can be rejected

The natural-language fallback rejects text shorter than 10 characters.

This can reject valid student replies such as:

```text
Got it.
Why?
I see.
Okay.
Makes sense.
```

These are plausible student messages, especially when the tutor asks a brief
confirmation question or when the student is confused.

### 3.2 Simulator failures are reported as completed sessions

Returning `status="completed"` for simulator failure is risky. Many downstream
callers and scripts may only check status and ignore `reason`.

This can cause:

- failed sessions to be treated as complete benchmark samples;
- inflated completion rates;
- hidden data quality defects in generated conversations;
- misleading evaluation artifacts.

**Note: `agent_stuck` has the identical problem.** `session.py:432–434` also
returns `self._result("", "completed", reason="agent_stuck")`. Any fix to
simulator failure reporting must be applied symmetrically to `agent_stuck` and
any other non-content termination paths. The session class has three terminal
paths that all mask abnormal endings as `"completed"`:
`student_sim_error:*`, `agent_stuck`, and any future additions.

### 3.3 Retry policy treats all failure modes the same

Network errors and parse errors currently share the same retry budget.

This is reasonable for transient network failures, but less ideal for parse
failures. If the model produces prompt leakage or malformed output at
temperature 0, retrying the exact same prompt may repeat the same failure.

A parse retry should use a stricter repair prompt, not an identical prompt.

### 3.4 Completion reason loses error history

`StudentSimError.attempts` stores useful diagnostics, but
`completion_reason = student_sim_error:<last_type>` only exposes the final
attempt's error type.

Example:

```text
attempt 1: parse
attempt 2: network
attempt 3: network
completion_reason: student_sim_error:network
```

The full attempt history is still available in the exception, but the session
summary loses useful context. Worse, the exception object is discarded when
caught in `handle_send_message`—there is no code path that preserves
`exc.attempts` anywhere downstream.

### 3.5 Experiment validation cannot yet detect simulator-failed sessions

The stability experiment currently validates artifact counts and aggregate
shape. It does not validate:

- simulator failure reasons;
- empty student messages;
- expected user/assistant turn counts per conversation;
- whether a conversation ended early because of `student_sim_error:*`.

In the current experiment runner, a `StudentSimError` generally causes the trial
wrapper to return `FAIL` and skip saving the conversation. The larger risk is
the server/session path used by benchmark runs.

---

## 4. Additional Bugs Found During Code Review

These were not in the original draft and must be fixed alongside the above.

### 4.1 Dead code `_STUDENT_FALLBACK` contradicts Principle 2

`session.py:30` defines:

```python
_STUDENT_FALLBACK = "Could you explain that in a bit more detail?"
```

This constant is not referenced anywhere in the current code. It is a leftover
from the legacy fallback behavior that Principle 2 explicitly prohibits. Its
presence is a readability hazard: a future maintainer may reintroduce it
thinking it is intentional. **Delete it.**

### 4.2 `parse_method="json"` is set for natural-language fallback paths too

In `_generate_parsed`:

```python
attempt_record.update(error_type=None, parse_method="json")
```

This line runs for every successful parse, including the case where
`_is_usable_student_message()` accepted plain text. The `parse_method` field
therefore misrepresents how the output was parsed. Add `"fallback_text"` as a
distinct value so attempt records are trustworthy.

### 4.3 `"empty"` error type exists in docstrings but not in code

`StudentSimError.__init__` documents `error_type` as `"network" | "parse" | "empty"`.
In practice, empty LLM output raises `ValueError("LLM returned empty output")`
inside `_parse_simulated_input_strict`, and `_generate_parsed` catches all
`ValueError` as `error_type="parse"`. The `"empty"` type is never set anywhere.

This means empty-output failures are indistinguishable from structural parse
failures in attempt records and completion reasons. Either remove `"empty"`
from the docstring or implement the distinction: detect `ValueError` with
message starting `"LLM returned empty output"` and set `error_type="empty"`.

### 4.4 JSON-with-wrong-key discards potentially valid natural-language fallback

`_parse_simulated_input_strict` logic:

```python
match = _JSON_RE.search(raw)
if match:
    try:
        data = json.loads(match.group())
        ...
    except (json.JSONDecodeError, TypeError):
        pass
    raise ValueError(f"JSON found but no valid simulated_input: {raw[:120]!r}")
```

When the model returns valid JSON with a wrong key (e.g.,
`{"response": "Why does this work?"}`) the function raises `ValueError`
immediately without checking if the surrounding text or the value of the wrong
key would pass `_is_usable_student_message()`. The student's intended message
is discarded for a formality.

This is the mirror image of Problem 3.1: the short-text gate is too strict for
plain text, and the JSON-key gate is too strict when JSON structure is present
but key name drifts. Consider extracting the string value from any single-value
JSON object as a fallback before raising.

---

## 5. Recommended Design Principles

### Principle 1: Be tolerant about output shape, strict about error state

The parser should accept reasonable model-output variation, including short
natural-language student replies. But once the simulator truly fails, the
session/result state must represent failure explicitly.

### Principle 2: Do not synthesize fake student data

Do not use hardcoded fallback strings like:

```text
Could you explain that in a bit more detail?
```

That keeps a conversation moving, but it inserts a student message not produced
by the student model. For benchmark data, that is worse than a visible failure.
The constant `_STUDENT_FALLBACK` in `session.py` must be deleted to prevent
accidental reuse.

### Principle 3: Make data-quality failures machine-detectable

Any simulator failure should be queryable by downstream scripts without reading
logs. Validation should be able to fail the dataset when simulator errors are
present.

### Principle 4: Abnormal terminations must use a distinct status

A session status of `"completed"` should mean the conversation ran to a natural
end. Simulator failure, stuck agent, and any future forced-stop paths must not
use `"completed"`. All terminal paths in `TutoringSession` must be audited
together, not fixed in isolation.

---

## 6. Proposed Fix Plan

### Step 1: Relax natural-language fallback

Replace the hard `len(text) < 10` cutoff with a more nuanced check.

Accept short text if:

- it is non-empty;
- it does not match prompt-leakage/meta patterns;
- it contains alphabetic content or a meaningful question/acknowledgment;
- it is not JSON-shaped garbage;
- it is not a code fence or tool/meta output.

Examples that should pass:

```text
Got it.
Why?
I see.
Okay, thanks.
Makes sense.
```

Examples that should fail:

````text
{}
{"foo": "bar"}
JSON Output:
simulated_input
```python
...
```
````

Additionally, add a single-value JSON fallback: if the JSON has exactly one
string value (regardless of key name) and that value passes
`_is_usable_student_message()`, accept it with a warning log instead of
raising.

### Step 2: Fix `parse_method` tagging and `"empty"` error type

In `_generate_parsed`, distinguish parse methods in the attempt record:

```python
# JSON parse succeeded
attempt_record.update(error_type=None, parse_method="json")
# Natural-language fallback succeeded
attempt_record.update(error_type=None, parse_method="fallback_text")
```

For empty output, set `error_type="empty"` rather than `"parse"` so downstream
filtering can distinguish transient empty responses from structural parse
failures:

```python
except ValueError as exc:
    error_type = "empty" if "empty output" in str(exc) else "parse"
    attempt_record.update(error_type=error_type, detail=str(exc))
```

### Step 3: Add parser tests

Add tests covering:

- valid JSON with `simulated_input`;
- JSON without `simulated_input` (wrong key with usable value);
- JSON without `simulated_input` (wrong key with unusable value);
- malformed JSON;
- plain natural-language fallback;
- short valid replies (< 10 chars);
- prompt leakage;
- code-fence leakage;
- empty output.

### Step 4: Improve parse retry behavior

On first parse failure, retry with a repair instruction that includes the
previous bad output. The repair prompt must show the model what it produced so
it can correct course:

```text
Your previous response could not be parsed as a student reply.
Your output was:
<previous output, truncated to ~300 chars>

Return only valid JSON in this exact shape:
{"simulated_input": "..."}
Do not include markdown, analysis, or any other keys.
```

Distinguish retry strategies by error type:

- network errors: retry with backoff, identical prompt;
- empty output: retry once with identical prompt, then fail;
- parse errors: retry with repair instruction containing the bad output.

This requires changing `_generate_parsed(self, prompt, images)` to accept an
optional `repair_context: str | None` parameter, or splitting into two
methods. The repair prompt should be a short focused message—do not re-send
the full original prompt alongside it, as that wastes tokens and may confuse
the model.

### Step 5: Preserve structured failure metadata

Keep `StudentSimError.attempts`, but add a compact summary property:

```python
@property
def summary(self) -> dict:
    return {
        "final_error_type": self.error_type,
        "attempt_count": len(self.attempts),
        "error_types": [a.get("error_type") for a in self.attempts],
        "last_detail": self.attempts[-1].get("detail", "") if self.attempts else "",
    }
```

The session handler should preserve this summary. The `_result()` method
signature must be extended to accept optional error metadata:

```python
def _result(
    self,
    student_message: str,
    status: str,
    reason: str | None = None,
    sim_error: dict | None = None,
) -> str:
    d = {"student_message": student_message or "", "status": status}
    if reason:
        d["reason"] = reason
    if sim_error:
        d["sim_error"] = sim_error
    return json.dumps(d)
```

Then in the exception handler:

```python
sim_error = exc.summary if isinstance(exc, StudentSimError) else {"final_error_type": type(exc).__name__}
return self._result("", "failed", reason=reason, sim_error=sim_error)
```

### Step 6: Stop reporting abnormal terminations as `"completed"`

The only clean fix is to introduce a `"failed"` status for all non-content
terminations. This affects at minimum:

- `student_sim_error:*` (simulator failure)
- `agent_stuck` (repeat detection)

Change both to return `status="failed"` instead of `status="completed"`.
Do not use the compatibility option of keeping `status="completed"` with
structured error metadata—that approach is only safe if every downstream
consumer is audited and patched, and it leaves a standing trap for future code.

Before making this change, audit these callsites for `status` assumptions:

- `session_api` and `http_app` (what do they return to the agent on failure?)
- `result_writer` (does it gate on status?)
- `eval_writer` / `pipeline.py` (does it filter on status?)
- `batch_eval.py` (does it count completions by status?)
- experiment runner trial wrappers

### Step 7: Delete `_STUDENT_FALLBACK`

Remove `_STUDENT_FALLBACK = "Could you explain that in a bit more detail?"` from
`session.py`. It is dead code that contradicts Principle 2.

### Step 8: Add experiment validation

For `experiments/student_sim_stability`, add validation for:

- no simulator failure metadata in results;
- no empty student messages in conversation turns;
- no `reason` values matching `student_sim_error:*` or `agent_stuck`;
- turn count within expected range given completion reason.

For turn count validation: "expected" counts are not fixed—they depend on
completion reason (`max_turns`, `objectives_met`, `timeout`). Validation logic
must condition on completion reason, not assert a fixed count.

Recommended artifact shape change (sidecar is less disruptive to existing
readers):

```text
conversations/live__...json      # existing format, unchanged
metadata/live__...meta.json      # new: {completion_reason, student_sim_errors, turn_count}
```

---

## 7. Recommended Implementation Order

1. Delete `_STUDENT_FALLBACK` (trivial, no risk).
2. Change only `_is_usable_student_message()` and add parser tests (Step 1 + 3).
3. Fix `parse_method` tagging and `"empty"` error type (Step 2).
4. Add `StudentSimError.summary` property (Step 5, first part only).
5. Extend `_result()` and update exception handler to preserve summary.
6. Change `status="failed"` for `student_sim_error` and `agent_stuck` after
   auditing all downstream callers (Step 6).
7. Implement parse repair retry (Step 4) — this is the most structurally
   invasive change; do it after status semantics are stable.
8. Add stability experiment validation (Step 8).
9. Run a small generation sample before regenerating any full result set.

---

## 8. Open Questions for Review

1. Should `handle_send_message()` introduce a public `status="failed"` value
   now, or should the full caller audit happen first to avoid breaking
   in-flight pipelines?
2. For the single-value JSON fallback (Step 1): should the wrong-key value
   be accepted silently, or logged as a warning to track model format drift?
3. Should the repair prompt re-send only the correction instruction (smaller,
   but loses context), or include the original system framing too (safer, but
   costs more tokens)?
4. Should simulator failure invalidate the whole benchmark task immediately, or
   should it be saved as a `status="failed"` artifact for audit?
5. Should `student_sim_stability` add sidecar metadata files, or migrate to a
   single structured artifact per conversation?

---

## 9. Bottom Line

The current strict parser is directionally correct, but four boundaries need
work:

- **content boundary**: short natural student replies should not be rejected;
- **state boundary**: simulator failures must not look like normal completed
  sessions—and `agent_stuck` has the same problem and must be fixed together;
- **tracking boundary**: `parse_method` and error type fields in attempt
  records are currently inaccurate and must be fixed before any reliability
  analysis of the retry logic is meaningful;
- **dead code boundary**: `_STUDENT_FALLBACK` must be deleted before it
  confuses a future maintainer into reintroducing the fake-student-data
  anti-pattern.
