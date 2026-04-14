# Termination Criteria (TC) Design Guidelines

Guidelines for writing `termination_criteria` in task JSON files, based on lessons learned from S-series and I-series TC development.

---

## 1. Regex Parsing Compatibility

TC items use `(1)` `(2)` numbering. The parser regex uses negative lookbehind `(?<![A-Za-z])` to distinguish TC numbers from indicator notation like `SMA(20)`, `RSI(14)`, `EMA(10)`. **Indicator parameters with parenthesized numbers are safe to use in TC text.**

The end-of-TC sentinel is `Once all`. The regex uses `(?=Once all|$)` to stop matching at the closing sentence. Always end TC text with:

```
Once all N steps have been computationally demonstrated, the session is complete.
```

## 2. Checker Visibility Constraints

The incremental checker sees **one exchange at a time** (1 student message + 1 tutor response), with each message truncated to 3000 chars. A two-pass strategy checks head (first 3000) then tail (last 3000) if content exceeds the limit.

Implications:

- Each TC item must be **judgeable from a single exchange**, not accumulated across turns.
- TC items referencing content that could land in the middle of a long response (beyond head, before tail) may be missed. Prefer items that naturally appear at the start or end of a tutor response.

## 3. Describe Conversation Content, Not Tool Actions

The checker LLM only sees conversation text. It cannot see tool calls, workspace files, or backtest execution status.

**Do:**
- "Presented backtest results with specific numerical values such as total return, Sharpe ratio, or number of trades"
- "Showed trade log entries with actual entry and exit records including dates and prices"
- "Showed C# code for the strategy and explained key components"

**Do not:**
- "Ran the backtest to completion" (action — checker cannot verify execution)
- "Saved trade log to a file" (file operation — checker cannot see files)
- "Verified output by showing persisted file contents" (implies file access)

## 4. Avoid Over-Specification

The more specific requirements a TC item contains, the higher the bar for the checker to judge "covered." Long requirement lists combined with the 3000-char truncation window increase the risk of permanent non-coverage.

**Do:**
- "Showed C# code for the SMA(20) strategy and explained key components such as data subscription, indicator setup, or entry/exit logic" — `such as` + `or` = any subset satisfies

**Do not:**
- "Showed working LEAN C# code covering AddCryptoFuture subscription, SMA indicator registration, warm-up handling, and entry/exit logic in OnData" — requires all four points visible in one 3000-char window

## 5. Prevent First-Exchange Full Coverage

For tool-intensive tasks (I/E/X series), the tutor tends to complete write → run → present results in a single tool loop, producing one dense response that covers all content-based TC items at once.

**Solution:** Include one interaction-dependent TC item that cannot be satisfied in the first exchange:

```
(N) Responded to the student's follow-up about the implementation
or results with additional explanation, code refinement, or analysis
beyond the initial demonstration.
```

This guarantees at least 2 exchanges before termination, since the first exchange contains only the student's opening message (not a follow-up).

## 6. Item Count

- S-series: 3–4 items
- I-series: 4 items (3 content + 1 interaction)
- Fewer than 2 items: parser rejects and falls back to native checker
- More than 5 items: increases risk of permanent non-coverage and excessive checker LLM calls

Each item should correspond to one observable milestone that the tutor naturally produces.

## 7. Unified TC vs Per-Persona TC

The schema supports both formats:

```json
// Unified (string)
"termination_criteria": "... (1) ... (2) ... Once all ..."

// Per-persona (dict)
"termination_criteria": {
  "beginner_no_finance": "... (1) ... (2) ...",
  "intermediate_developer": "... (1) ... (2) ...",
  "advanced_quant": "... (1) ... (2) ..."
}
```

`_parse_tc_items(task, persona_id)` resolves the appropriate TC text. When using per-persona TC, ensure each persona's TC has the same number of items and follows the same structural pattern to maintain comparability.

Conversation depth differences between personas should be driven by `behavioral_rules` and `emotional_profile`, not by TC item count or difficulty. Use per-persona TC only when persona openings demand fundamentally different deliverables.

## 8. Category Registration

Only categories listed in `_INCREMENTAL_CHECKER_CATEGORIES` use the incremental TC checker. Currently:

```python
{"strategy", "backtest", "implementation", "debug", "data_analysis"}
```

Tasks in other categories (`end_to_end`, `adversarial`) use DeepEval's native `stop_conversation` with `expected_outcome`. To enable incremental checking for a new category, add it to this set and write `termination_criteria` for its tasks.

For `data_analysis` tasks, prefer TC items that are directly observable from
the tutor's message text:

- Showed literal pandas output, concrete numerical results, or chart-backed observations
- Explained a quant data caveat or interpretation issue tied to the displayed results
- Responded to a follow-up with additional concrete analysis beyond the first inspection

Avoid TC items that require hidden state or filesystem visibility, such as
"saved a CSV", "finished a network fetch", or "ran the script successfully".

## 9. Text Accumulation Dependency

The incremental checker reads `Turn.content`, which is the return value of `generate_response()`. The Anthropic adapter accumulates text from all iterations of the BetaToolRunner tool loop (fixed in the text accumulation patch). Without this fix, the checker would only see the final iteration's text, potentially missing TC-relevant content from earlier iterations.

When debugging TC coverage failures, verify that `content` in `run_state.json` matches the full `content_blocks` text — a mismatch indicates the accumulation fix is not active.
