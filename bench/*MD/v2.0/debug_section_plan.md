# Debug Section (X-Series) Design Plan

> Version: v2.1 | Status: **Implemented** | Section: Algorithm Debugging

---

## 1. Section Philosophy

### 1.1 What X-Series Tests

X-series tests the agent's ability to act as a **debugging tutor** — guiding a student to diagnose and fix broken quantitative algorithms where the code runs without errors but produces incorrect results.

In the real quant workflow, most engineering time is spent **not** writing new code but diagnosing why an existing strategy produces unexpected results. The bugs are rarely syntactic (compile errors, missing imports) — they are **semantic**: the code runs, produces numbers, and the numbers are wrong. Finding the root cause requires understanding the domain logic, not just the language syntax.

```
┌─────────────────────────────────────────────────────────┐
│              Given: Buggy Algorithm                       │
│  Code runs without errors. Produces output. But the      │
│  output is subtly wrong — wrong Sharpe, wrong signals,   │
│  wrong returns, misaligned data. The bug is semantic,    │
│  not syntactic.                                          │
└──────────────────────┬──────────────────────────────────┘
                       │ agent must diagnose root cause
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Debugging Process                            │
│  1. Run the code → observe symptoms                      │
│  2. Form hypotheses about root cause                     │
│  3. Add diagnostic checks (print values, compare refs)   │
│  4. Isolate the bug to a specific function/line          │
│  5. Explain WHY the bug causes the observed symptoms     │
│  6. Apply minimal fix                                    │
│  7. Verify fix restores correct behavior                 │
└──────────────────────┬──────────────────────────────────┘
                       │ agent guides student through fix
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Output: Fixed Code + Understanding           │
│  Student has: (1) fixed code that produces correct        │
│  results, (2) understanding of WHY the bug existed,      │
│  (3) knowledge of how to avoid similar bugs in future.   │
└─────────────────────────────────────────────────────────┘
```

**The core evaluation question**: Given a running but incorrect quant algorithm, can the agent guide the student to identify the root cause (not just symptoms), apply a targeted fix, and verify the fix restores correct behavior — all while explaining the underlying mechanism?

### 1.2 Why Quant Debugging Is Special

Quant bugs differ fundamentally from typical software bugs:

| Property | Typical Software Bug | Quant Bug |
|----------|---------------------|-----------|
| **Detection** | Crash, error message, wrong UI | Code runs fine, numbers look plausible |
| **Symptom** | Stack trace, assertion failure | Sharpe is 0.8 instead of 1.2, returns are inflated |
| **Diagnosis** | Follow the stack trace | Requires domain knowledge to recognize "this number is wrong" |
| **Root cause** | Null pointer, off-by-one, race condition | Look-ahead bias, timezone misalignment, wrong return formula |
| **Verification** | Test passes | Compare against reference implementation or analytical expectation |

This means the agent cannot rely on error messages or stack traces. It must **understand the quant domain** well enough to recognize when output is wrong, hypothesize why, and verify its fix against domain expectations.

### 1.3 Position in the Quant Workflow Pipeline

X-series sits **downstream of I-series** in the pipeline. Where I-series asks "can the agent write the algorithm?", X-series asks "can the agent fix a broken one?"

```
D (Data)  →  S (Strategy Research)  →  B (Backtest Engine)  →  I (Implementation)  →  X (Debug)  →  E (End-to-End)
  │               │                       │                       │                     │
"Get and        "Discover and            "Build the system       "Write the          "Find and fix
 understand      formalize the            to validate             algorithm from       bugs in existing
 the data"       alpha idea"              strategies"             scratch"             algorithms"
```

| Section | Focus | Relationship to X-series |
|---------|-------|--------------------------|
| **I-series** (Implementation) | Write algorithms from scratch | I-series is the **upstream creator** — X-series debugs the kind of code I-series produces. X07-X10 debug LEAN C# algorithms similar to I01-I06 output. |
| **D-series** (Data Analysis) | Data loading, exploration | X-series bugs often involve data misunderstanding (X04 returns, X05 timezones). |
| **B-series** (Backtest Engine) | Building backtest systems | B-series builds engines; X-series debugs strategies running on engines. |
| **S-series** (Strategy Research) | Signal discovery | X06 (overfitting) relates to S-series research methodology. |

### 1.4 Two Tiers: Python + LEAN C#

X-series spans two execution environments, mirroring the broader benchmark:

**Tier A: Python/pandas (X01-X06)** — Uses the existing `quant-tutor-env:v2.2` sandbox. Bugs are in standalone Python scripts using pandas, numpy, and standard quant patterns. These test debugging skills in the most common quant prototyping environment.

**Tier B: LEAN C# (X07-X10)** — Uses the `quant-tutor-env:v2.2-lean` sandbox. Bugs are in LEAN C# algorithms with engine-specific issues (missing warm-up, wrong order types, framework conflicts, stale universe). These test debugging skills in a production backtest engine environment.

```
Tier A: Python/pandas (X01–X06)                Tier B: LEAN C# (X07–X10)
──────────────────────────────                  ──────────────────────────
Standard quant sandbox                          LEAN engine sandbox
student_code/*.py provided                      student_code/*.cs provided (new)
Checklist-based eval scripts                    Behavioral scoring + checklist eval
No reference data generation                    LEAN reference generation needed
```

---

## 2. Debugging Skill Taxonomy

X-series tasks are ordered by a progression of debugging skill categories, from simple numerical errors to complex cross-domain reasoning:

| # | Skill Category | Description | Task |
|---|---------------|-------------|------|
| 1 | **Numerical Precision** | Off-by-one in window sizes, rounding, index errors | X01 |
| 2 | **Temporal Logic** | Look-ahead bias, signal timing, causal ordering | X02 |
| 3 | **State Machine** | Position state encoding errors, missing transitions | X03 |
| 4 | **Mathematical** | Wrong formula choice (diff vs pct_change), unit confusion | X04 |
| 5 | **Cross-Domain** | Timezone misalignment across data sources | X05 |
| 6 | **Statistical** | Overfitting diagnosis, in-sample/out-of-sample decay | X06 |
| 7 | **Initialization** | Missing indicator warm-up in LEAN engine | X07 |
| 8 | **API Misuse** | Wrong order type or parameter in LEAN API | X08 |
| 9 | **Framework Conflict** | Conflicting insights in Algorithm Framework pipeline | X09 |
| 10 | **Data Lifecycle** | Stale universe / survivorship bias in LEAN backtests | X10 |

This progression has two dimensions:
- **Increasing abstraction**: from concrete numerical errors (X01) to conceptual statistical reasoning (X06)
- **Increasing system complexity**: from standalone scripts (X01-X06) to engine-integrated debugging (X07-X10)

---

## 3. Task Designs

### 3.0 Existing Task: X01 — Fix Off-by-One in MA Calculation

**Status**: **Exists**. Task JSON, eval script, and student code are already implemented and tested.

**Student code**: `student_code/ma_offbyone.py` — Dual MA crossover strategy where `rolling(19)` is used instead of `rolling(20)` for a 20-day SMA.

**Bug**: `rolling(window=19)` instead of `rolling(window=20)` on line 47. The SMA_20 values are consistently computed over 19 bars, creating a subtle shift in crossover timing and resulting in slightly different signal generation.

**Symptoms**: SMA_20 values don't match TradingView or other reference implementations. The discrepancy is small (off by one bar) but affects all downstream signals.

**Fix**: Change `rolling(window=19)` to `rolling(window=20)`.

**Files**: `X01_ma_offbyone.json` (task), `X01_ma_offbyone.py` (eval), `student_code/ma_offbyone.py`.

---

### 3.1 X02 — Fix Look-Ahead Bias in Signal Generation

**Difficulty**: easy
**Category**: debug
**Skill category**: Temporal logic

**Student code**: `student_code/lookahead.py` — SMA(10)/SMA(30) crossover strategy with look-ahead bias in position assignment.

**Bug description**: In `generate_signals()` (line 69), the position is assigned directly from today's signal without shifting:
```python
df["Position"] = df["Signal"]  # BUG: should be df["Signal"].shift(1)
```
The strategy uses today's close to compute the MA signal, then applies that signal to today's return. In reality, you can only observe the close at the *end* of the day, so the signal can only be acted on starting the *next* day. Without `shift(1)`, the strategy has future information — it "knows" today's close before it happens.

**Symptoms**:
- Strategy returns are suspiciously high (inflated by look-ahead)
- Sharpe ratio significantly exceeds realistic expectations for a simple MA crossover
- Perfect timing on crossover days (enters on the exact day, not the day after)

**Fix**: `df["Position"] = df["Signal"].shift(1)` — shift position by one day to enforce causal ordering.

**Description**: Guide a student to find and fix a look-ahead bias in an SMA crossover strategy where today's signal is applied to today's return instead of tomorrow's. The strategy appears to perform well, but its returns are inflated because it uses future information.

**Expected outcome**: Student identifies the look-ahead bias (position uses today's signal for today's trade instead of shifting by one day), understands why this inflates performance, fixes the code with `shift(1)`, and verifies that the corrected strategy has lower but realistic returns.

**Required capabilities**:
1. Read the buggy crossover code and trace the signal-to-position flow
2. Identify the missing `shift(1)` as a look-ahead bias
3. Explain why using today's signal for today's trade is "cheating"
4. Fix the bug and verify returns decrease to realistic levels

**Student openings**:
- **beginner_no_finance**: "I wrote a simple moving average strategy and it seems to be doing really well — maybe too well? My friend said something about 'look-ahead bias' but I'm not sure what that means. Can you check my code?"
- **intermediate_developer**: "My SMA crossover strategy has an unusually high Sharpe ratio. I suspect there might be a look-ahead bias somewhere in my signal generation, but I can't pinpoint exactly where the leak is. Can you help me audit the code?"
- **advanced_quant**: "I'm getting implausible alpha from a simple 10/30 SMA crossover on AAPL — Sharpe well above what this signal should deliver. I've narrowed it to the signal application step. Can you review the causal ordering of my signal and position vectors?"

**Environment**:
```json
{
  "data_files": ["AAPL_2018_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "search_docs"],
  "docs_available": ["moving_averages.md", "pandas_timeseries.md"],
  "sandbox_image": "quant-tutor-env:v2.2",
  "network_enabled": false
}
```

**Convenient tools**: `compute_indicator`, `plot_chart`, `run_backtest`

**Expected MCP tools**: `file_read`, `shell_exec`, `file_write`

**Eval strategy** (checklist-based):
| Check | Weight | Method |
|-------|--------|--------|
| `bug_is_fixed` | 0.40 | Regex: `shift(1)` present on position assignment; no unshifted `df["Position"] = df["Signal"]` |
| `code_runs_without_error` | 0.15 | Tool logs show successful execution with strategy output keywords |
| `fix_verified` | 0.20 | Before/after comparison: agent shows returns/Sharpe decreased after fix (look-ahead removal reduces performance) |
| `root_cause_explained` | 0.15 | Conversation/logs contain: "look-ahead" or "future information" or "shift" + "position" explaining the causal mechanism |
| `returns_decreased` | 0.10 | Strategy Sharpe/return in output is lower after fix |

---

### 3.2 X03 — Fix Missing Short Position in Mean-Reversion Strategy

**Difficulty**: medium
**Category**: debug
**Skill category**: State machine

**Student code**: `student_code/position_bug.py` — Bollinger Band mean-reversion strategy where short positions are coded as 0 (flat) instead of -1.

**Bug description**: In `generate_positions()` (line 85), when price is above the upper Bollinger Band, the position should be set to -1 (short) but is set to 0 (flat):
```python
df.loc[df["Close"] > df["BB_Upper"], "Position"] = 0  # BUG: should be -1
```
This line effectively does nothing because Position is already initialized to 0. The strategy captures long-side mean reversion (buy below lower band) but completely misses all short-side opportunities.

**Symptoms**:
- `short_days` in trade statistics is always 0
- Strategy only profits from upward mean reversion, never from downward
- Significant missed opportunity in trending-down periods
- Position breakdown shows only LONG and FLAT, never SHORT

**Fix**: Change `Position = 0` to `Position = -1` on the upper-band condition.

**Description**: Guide a student to find and fix a position encoding bug in a Bollinger Band mean-reversion strategy where the short position is coded as 0 (flat) instead of -1 (short). The strategy only captures long-side mean reversion and completely misses short-side opportunities.

**Expected outcome**: Student identifies that the short position assignment uses 0 instead of -1, understands the three-state position model (1=long, 0=flat, -1=short), fixes the bug, and verifies that the strategy now shows non-zero short days and captures downward mean reversion.

**Required capabilities**:
1. Read the Bollinger Band strategy and understand the long/flat/short position model
2. Identify that `Position = 0` on the upper-band condition is effectively a no-op
3. Understand why missing the short side reduces strategy performance
4. Fix the bug and verify short_days > 0 in the corrected output

**Student openings**:
- **beginner_no_finance**: "I made a strategy that buys when prices are low and sells when they're high, using Bollinger Bands. But it seems like it only makes money when prices go up, not when they go down. Shouldn't mean reversion work both ways?"
- **intermediate_developer**: "My Bollinger Band mean-reversion strategy shows 0 short days in the trade statistics. I'm setting positions based on upper and lower band breakouts, but the short side seems to be missing entirely. Can you check my position logic?"
- **advanced_quant**: "I'm seeing asymmetric PnL in my BB mean-reversion backtest — all alpha comes from the long side, with exactly zero short exposure. The signal generation looks symmetric to me, so the bug must be in position encoding. Can you audit the generate_positions function?"

**Environment**:
```json
{
  "data_files": ["AAPL_2018_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "search_docs"],
  "docs_available": ["moving_averages.md", "pandas_timeseries.md"],
  "sandbox_image": "quant-tutor-env:v2.2",
  "network_enabled": false
}
```

**Convenient tools**: `compute_indicator`, `plot_chart`, `run_backtest`

**Expected MCP tools**: `file_read`, `shell_exec`, `file_write`

**Eval strategy** (checklist-based):
| Check | Weight | Method |
|-------|--------|--------|
| `bug_is_fixed` | 0.35 | Regex: upper-band condition sets Position to `-1` (not `0`); pattern `"Position"\]\s*=\s*-1` present |
| `code_runs_without_error` | 0.15 | Successful execution with strategy output keywords |
| `fix_verified` | 0.20 | Tool log output shows `short_days > 0` or `SHORT` label in sample positions after fix |
| `root_cause_explained` | 0.15 | Conversation/logs explain: position 0 is flat not short, OR "missing short" / "no-op" / "three-state" |
| `short_positions_exist` | 0.15 | Short day count > 0 in output |

---

### 3.3 X04 — Fix diff() vs pct_change() in Return Computation

**Difficulty**: medium
**Category**: debug
**Skill category**: Mathematical

**Student code**: `student_code/returns_diff.py` — Daily returns computation using `diff()` (dollar change) instead of `pct_change()` (percentage return).

**Bug description**: In `compute_daily_returns()` (line 52):
```python
df["Daily_Return"] = df["Close"].diff()  # BUG: should be .pct_change()
```
`diff()` computes absolute dollar change (e.g., $3.00), while `pct_change()` computes percentage return (e.g., 0.02 = 2%). All downstream statistics — mean return, volatility, Sharpe ratio, cumulative returns — are computed on dollar changes instead of percentages, making them nonsensical.

**Symptoms**:
- Mean daily return is ~0.10 (dollars) instead of ~0.0004 (percentage)
- Annualized return is ~25.0 (meaningless dollar value) instead of ~10% (percentage)
- Annualized volatility is ~45 (dollars) instead of ~0.28 (28%)
- Sharpe ratio may appear reasonable by coincidence (ratio of two wrong numbers)
- Cumulative returns via `(1 + returns).cumprod()` produce astronomically large or small numbers

**Fix**: Replace `df["Close"].diff()` with `df["Close"].pct_change()`.

**Description**: Guide a student to find and fix a fundamental return computation error where `diff()` (absolute dollar change) is used instead of `pct_change()` (percentage return). All downstream statistics are computed on dollar changes rather than returns, producing nonsensical values that may not be immediately obvious to someone unfamiliar with typical return magnitudes.

**Expected outcome**: Student identifies that `diff()` computes dollar changes instead of percentage returns, understands the difference between absolute and relative returns, fixes the code to use `pct_change()`, and verifies that statistics now show realistic magnitudes (mean daily return ~0.04%, annualized vol ~28%).

**Required capabilities**:
1. Recognize that the output statistics have implausible magnitudes for percentage returns
2. Trace the issue to the `diff()` vs `pct_change()` distinction
3. Explain the mathematical difference: `diff()` = P(t) - P(t-1) vs `pct_change()` = (P(t) - P(t-1)) / P(t-1)
4. Fix the bug and verify output statistics are in typical return-percentage ranges

**Student openings**:
- **beginner_no_finance**: "I computed daily returns for AAPL and the mean return says something like 0.1. Is that a 10% daily return? That seems like a lot. My statistics might be off but I'm not sure why."
- **intermediate_developer**: "My return statistics are producing values that don't look like percentages — the mean daily return is around 0.1 and the annualized volatility is 45. I think there's a bug in my return computation. Can you help?"
- **advanced_quant**: "I'm getting annualized return and volatility values that are clearly in dollar space, not return space. The Sharpe looks plausible by coincidence but the underlying moments are wrong. I suspect my return series is mis-specified."

**Environment**:
```json
{
  "data_files": ["AAPL_2018_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "search_docs"],
  "docs_available": ["pandas_timeseries.md"],
  "sandbox_image": "quant-tutor-env:v2.2",
  "network_enabled": false
}
```

**Convenient tools**: `compute_statistics`, `plot_chart`

**Expected MCP tools**: `file_read`, `shell_exec`, `file_write`

**Eval strategy** (checklist-based):
| Check | Weight | Method |
|-------|--------|--------|
| `bug_is_fixed` | 0.35 | Regex: `pct_change()` present; `\.diff()` absent on the Daily_Return line |
| `code_runs_without_error` | 0.15 | Successful execution with return statistics output |
| `fix_verified` | 0.20 | Before/after comparison: agent shows statistics changed from dollar-scale to percentage-scale after fix |
| `root_cause_explained` | 0.15 | Conversation/logs explain: `diff()` = dollar change vs `pct_change()` = percentage return, OR "absolute" vs "relative" |
| `realistic_magnitudes` | 0.15 | Mean daily return < 0.01 in output (percentage, not dollars) |

---

### 3.4 X05 — Fix Timezone Misalignment in Cross-Asset Merge

**Difficulty**: hard
**Category**: debug
**Skill category**: Cross-domain

**Student code**: `student_code/timezone_merge.py` — Crypto-stock correlation analysis that merges BTC (UTC timestamps) with AAPL (Eastern Time) data without converting timezones first.

**Bug description**: In `merge_datasets()` (lines 98-99), the merge date is extracted directly from timestamps without timezone conversion:
```python
crypto_df["merge_date"] = crypto_df["Date"].dt.date   # BTC at 00:00 UTC
stock_df["merge_date"] = stock_df["Date"].dt.date      # AAPL at 16:00 ET
```
BTC daily closes at 00:00 UTC, which is 19:00 ET the *previous day*. So extracting `.dt.date` from both gives different calendar dates for what is logically the same trading period. This causes a systematic 1-day misalignment: BTC's Jan 2 close is paired with AAPL's Jan 2 close, but BTC's Jan 2 00:00 UTC was actually still Jan 1 in New York.

**Symptoms**:
- Correlation is slightly lower than expected (misaligned returns add noise)
- Sample merged data shows plausible-looking rows but the BTC/AAPL pairs are off by one day
- Rolling correlation is noisier than expected
- The bug is not obvious from a quick visual inspection of the merged data

**Fix**: Convert BTC timestamps to Eastern Time before extracting the date:
```python
crypto_df["merge_date"] = crypto_df["Date"].dt.tz_convert("US/Eastern").dt.date
```

**Description**: Guide a student to find and fix a timezone misalignment in a crypto-stock correlation analysis. BTC data uses UTC timestamps and AAPL data uses Eastern Time, but the merge is done by extracting calendar dates directly without timezone conversion, causing a systematic 1-day offset between the two price series.

**Expected outcome**: Student identifies the timezone mismatch in the merge operation, understands that 00:00 UTC is 19:00 ET the previous day, fixes the code by converting to a common timezone before date extraction, and verifies that the correlation changes and the merge alignment is correct.

**Required capabilities**:
1. Understand the dual-timezone data source problem (UTC crypto vs ET equities)
2. Recognize that `dt.date` extracts the calendar date in the timestamp's own timezone
3. Identify the systematic 1-day offset caused by the UTC→ET date boundary
4. Fix the merge by converting to a common timezone before date extraction
5. Verify the fix by checking that sample merged rows are correctly aligned

**Student openings**:
- **beginner_no_finance**: "I'm trying to compare Bitcoin and Apple stock returns to see if they're correlated. I merged the data on the date column but something feels off about the correlation. Could there be a problem with how I combined the data?"
- **intermediate_developer**: "I merged BTC (UTC timestamps) with AAPL (Eastern Time) data for correlation analysis. The merge runs fine but I'm getting an unexpectedly low correlation. I wonder if the timezone difference between crypto and equity data could be causing a misalignment."
- **advanced_quant**: "I'm computing rolling correlation between BTCUSD and AAPL returns using a date-key merge. The BTC data is timestamped at 00:00 UTC and the AAPL data at 16:00 ET. I suspect a systematic 1-day lag in my merged dataset due to timezone boundary effects. Can you verify?"

**Environment**:
```json
{
  "data_files": ["AAPL_2018_2024.csv", "BTC_UTC.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "search_docs"],
  "docs_available": ["pandas_timeseries.md"],
  "sandbox_image": "quant-tutor-env:v2.2",
  "network_enabled": false
}
```

**Convenient tools**: `compute_statistics`, `plot_chart`

**Expected MCP tools**: `file_read`, `shell_exec`, `file_write`

**Eval strategy** (checklist-based):
| Check | Weight | Method |
|-------|--------|--------|
| `bug_is_fixed` | 0.35 | Regex: `tz_convert` present on crypto date extraction; both merge_date assignments use same timezone |
| `code_runs_without_error` | 0.15 | Successful execution with correlation output |
| `fix_verified` | 0.20 | Before/after comparison: agent shows correlation changed after fix, or shows aligned vs misaligned sample rows |
| `root_cause_explained` | 0.15 | Conversation/logs explain: "UTC" + "Eastern" mismatch, OR "00:00 UTC is 19:00 ET", OR "1-day offset" / "date boundary" |
| `alignment_verified` | 0.15 | Tool log shows inspection of sample merged rows to confirm correct date alignment |

---

### 3.5 X06 — Diagnose Overfitting in Multi-Parameter Strategy

**Difficulty**: hard
**Category**: debug
**Skill category**: Statistical

**Student code**: `student_code/overfit_single.py` — Multi-indicator strategy with 12 hand-tuned parameters optimized on 2020-2022 AAPL data, showing severe Sharpe decay on 2023-2024 out-of-sample data.

**Bug description**: This task has no single line to fix — the bug is **conceptual**. The strategy uses 12 parameters (fast/slow MA windows, RSI period, RSI thresholds, BB window, BB std, volatility lookback, volatility threshold, momentum period, and two signal weights) that were hand-tuned to fit a specific historical period. The strategy performs well in-sample but degrades significantly out-of-sample.

**Symptoms**:
- Large positive Sharpe in training period (2020-2022), much lower or negative in test (2023-2024)
- 12 parameters use suspiciously precise values (e.g., `fast_ma_window=7`, `bb_num_std=1.85`, `vol_threshold=0.0147`)
- All non-standard parameter choices (RSI period 11 instead of 14, BB window 17 instead of 20)
- Strategy outperforms buy-and-hold in training but not in testing

**Fix**: This is a diagnosis task, not a single-line fix. The agent should guide the student to:
1. Recognize the train/test Sharpe decay as evidence of overfitting
2. Identify that 12 parameters is excessive for a single-asset strategy
3. Suggest reducing parameters to 3-4 using standard values
4. Propose cross-validation or walk-forward analysis instead of single split
5. Explain the bias-variance tradeoff in strategy optimization

**Description**: Guide a student to diagnose and address overfitting in a multi-indicator trading strategy with 12 hand-tuned parameters. The strategy shows strong in-sample performance but significant decay out-of-sample. Unlike other X-series tasks, this is a conceptual diagnosis — there is no single line to fix. The agent must help the student understand the overfitting mechanism and propose structural remedies.

**Expected outcome**: Student recognizes the train/test performance gap as overfitting, identifies excessive parameterization as the root cause, understands why 12 parameters on limited data leads to curve-fitting, and proposes concrete remedies (reduce parameters, use standard values, implement cross-validation, walk-forward analysis). The student should also understand the bias-variance tradeoff in quantitative strategy design.

**Required capabilities**:
1. Run the strategy and observe the train/test Sharpe ratio decay
2. Identify excessive parameterization (12 parameters with suspiciously precise values)
3. Explain the overfitting mechanism (curve-fitting historical noise)
4. Propose structural fixes (parameter reduction, cross-validation, walk-forward)
5. Understand the bias-variance tradeoff in strategy optimization

**Student openings**:
- **beginner_no_finance**: "I built a trading strategy with lots of indicators and it performs amazingly on my test data! But my professor said something about 'overfitting' and that I should be careful. What does that mean?"
- **intermediate_developer**: "My multi-indicator strategy has a Sharpe of 1.5 on 2020-2022 data but drops to 0.3 on 2023-2024. I have 12 tunable parameters — could that be causing the decay? How do I know if I've overfit?"
- **advanced_quant**: "I'm seeing a 1.2 Sharpe decay between in-sample and out-of-sample periods in a 12-parameter composite signal strategy. The parameter values are clearly non-standard (BB window 17, RSI period 11). I want a principled approach to diagnosing and remedying this overfitting."

**Environment**:
```json
{
  "data_files": ["AAPL_2018_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "search_docs"],
  "docs_available": ["moving_averages.md", "pandas_timeseries.md"],
  "sandbox_image": "quant-tutor-env:v2.2",
  "network_enabled": false
}
```

**Convenient tools**: `compute_statistics`, `run_backtest`, `plot_chart`

**Expected MCP tools**: `file_read`, `shell_exec`

**Eval strategy** (checklist-based):
| Check | Weight | Method |
|-------|--------|--------|
| `train_test_executed` | 0.15 | Tool logs show the strategy was run AND both train and test Sharpe values were printed/observed (the student code already splits 2020-2022 train vs 2023-2024 test) |
| `sharpe_decay_noted` | 0.15 | Conversation/logs explicitly note the Sharpe difference between train and test periods (e.g., "train Sharpe X, test Sharpe Y") |
| `overfitting_identified` | 0.25 | Conversation contains keywords: "overfit" or "over-fit" or "curve-fit" or "data snooping" |
| `excessive_params_noted` | 0.20 | Conversation mentions parameter count (12) or excessive/too many parameters, or flags the suspiciously precise values |
| `remedy_proposed` | 0.15 | Conversation mentions at least one structural remedy: reduce parameters, cross-validation, walk-forward, regularization, simpler model |
| `root_cause_explained` | 0.10 | Conversation explains the mechanism: fitting noise vs signal, degrees of freedom vs data, bias-variance tradeoff |

---

### 3.6 X07 — Fix Missing Warm-Up in LEAN Algorithm

**Difficulty**: hard
**Category**: debug
**Skill category**: Initialization

**Student code**: `student_code/warmup_bug.cs` (implemented)

A LEAN C# algorithm implementing an EMA(20)/EMA(50) crossover on BTCUSDT daily. The algorithm creates EMA indicators but **does not call `SetWarmUp()`** and does not check `IsWarmingUp` in `OnData()`. This causes the algorithm to trade on partially-initialized indicator values during the first 50 bars, producing spurious signals.

**Bug description**:
```csharp
// In Initialize():
_fastEma = EMA(_btc, 20);
_slowEma = EMA(_btc, 50);
// Missing: SetWarmUp(50);

// In OnData():
// Missing: if (IsWarmingUp) return;
if (_fastEma > _slowEma && !Portfolio[_btc].IsLong)
{
    SetHoldings(_btc, 1.0);
}
```
Without warm-up, the EMA indicators start computing from the first bar with incomplete history. The EMA values during the first 50 bars are unreliable, leading to false crossover signals and incorrect early trades.

**Symptoms**:
- Early trades in the backtest that shouldn't exist (signals during warmup period)
- Trade count is higher than expected (extra trades from warmup artifacts)
- First few trades may have unusual PnL (computed on unreliable indicator values)
- The algorithm produces trades from the very first day, not after 50 bars

**Fix**: Add `SetWarmUp(50)` in `Initialize()` and `if (IsWarmingUp) return;` at the top of `OnData()`.

**Description**: Guide a student to find and fix a missing warm-up period in a LEAN C# EMA crossover algorithm. The algorithm trades on partially-initialized indicator values during the first 50 bars, producing spurious early signals. This is one of the most common LEAN bugs in production.

**Expected outcome**: Student identifies that the algorithm lacks `SetWarmUp()` and the `IsWarmingUp` guard, understands why indicators need warm-up bars before their values are reliable, fixes both issues, and verifies that the corrected algorithm produces fewer trades with the first trade occurring after bar 50.

**Required capabilities**:
1. Read the LEAN C# algorithm and identify the missing warm-up configuration
2. Understand why EMA indicators need N bars of history before producing reliable values
3. Add `SetWarmUp(50)` to Initialize() and `if (IsWarmingUp) return;` to OnData()
4. Run the backtest and verify trades start after the warm-up period

**Student openings**:
- **beginner_no_finance**: "I wrote a trading algorithm on LEAN and it's making some trades right from day one. My professor said the indicators need time to 'warm up' before they're reliable, but I'm not sure what that means or how to fix it."
- **intermediate_developer**: "My LEAN EMA crossover algorithm is producing trades from the very first bar. I suspect the indicators haven't warmed up, but I'm not sure how LEAN handles indicator initialization. Can you help me fix this?"
- **advanced_quant**: "My LEAN C# backtest produces spurious early signals from partially-initialized EMAs. I need to add proper warm-up handling — SetWarmUp plus the IsWarmingUp guard — but I want to understand the correct warm-up period and how LEAN seeds the indicator state."

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md"],
  "sandbox_image": "quant-tutor-env:v2.2-lean",
  "network_enabled": false
}
```

**Convenient tools**: `plot_chart`

**Expected MCP tools**: `file_read`, `shell_exec`, `file_write`

**Eval strategy** (behavioral + checklist):
| Check | Weight | Method |
|-------|--------|--------|
| `warmup_added` | 0.25 | C# pattern: `SetWarmUp` call present in Initialize() |
| `warmup_guard_added` | 0.15 | C# pattern: `IsWarmingUp` check present in OnData() |
| `fix_verified` | 0.10 | Before/after: agent shows trades no longer start on bar 1, or trade count decreased after fix |
| `root_cause_explained` | 0.10 | Conversation/logs explain: indicators need N bars of history, OR "partially initialized" / "unreliable values" |
| `backtest_completed` | 0.15 | LEAN backtest ran to completion (orders.json exists) |
| `behavioral_score` | 0.25 | Behavioral scoring: compare against reference (with correct warm-up) |

**Ground-truth preparation**: Write reference `warmup_bug_fixed.cs` algorithm with correct warm-up → run via `generate_lean_reference.py` → export reference trades/signals/summary.

---

### 3.7 X08 — Fix Order Type Misuse in LEAN Algorithm

**Difficulty**: hard
**Category**: debug
**Skill category**: API misuse

**Student code**: `student_code/order_type_bug.cs` (implemented)

A LEAN C# algorithm implementing a momentum strategy on BTCUSDT. The algorithm uses `LimitOrder()` with the *current price* as the limit, which in a trending market often results in unfilled orders (price moves away before the limit can be hit). It should use `MarketOrder()` for immediate execution.

**Bug description**:
```csharp
// In OnData():
if (momentum > threshold && !Portfolio[_btc].IsLong)
{
    // BUG: LimitOrder at current price — in trending market, price moves away
    LimitOrder(_btc, quantity, Securities[_btc].Price);
}
```
When momentum is positive and rising, using a limit buy at the current price often means the price has already moved up by the time the order is evaluated, resulting in the limit not being hit. This causes many missed entries, drastically reducing trade count and strategy performance.

**Symptoms**:
- Very few filled orders compared to expected signal count
- Many pending/cancelled orders in the order log
- Strategy appears to do nothing despite generating signals
- When orders do fill, they are during temporary dips (selection bias)

**Fix**: Replace `LimitOrder(_btc, quantity, Securities[_btc].Price)` with `MarketOrder(_btc, quantity)` for immediate execution on signal.

**Description**: Guide a student to find and fix a LEAN C# momentum strategy that uses `LimitOrder()` at the current price instead of `MarketOrder()`. In a trending market, limit orders at the current price frequently miss fills as the price moves away, resulting in very few executed trades despite many generated signals.

**Expected outcome**: Student identifies that `LimitOrder` at current price is inappropriate for a momentum strategy (where price trends away from the limit), understands the difference between market and limit orders in the context of directional strategies, fixes the code to use `MarketOrder`, and verifies that trade count increases significantly.

**Required capabilities**:
1. Read the LEAN algorithm and identify the `LimitOrder` usage
2. Understand why limit orders at current price fail in trending markets
3. Know the difference between `MarketOrder` (immediate) and `LimitOrder` (price-conditional)
4. Fix the order type and verify increased trade count in the backtest

**Student openings**:
- **beginner_no_finance**: "My LEAN algorithm generates trading signals but barely any trades actually happen. I'm confused — the signals are there but the orders don't seem to go through. What's going on?"
- **intermediate_developer**: "I'm getting a very low fill rate in my LEAN momentum strategy. I'm using LimitOrder with the current price as the limit. Most orders end up cancelled. Should I be using a different order type?"
- **advanced_quant**: "My LEAN momentum backtest shows a signal-to-fill ratio well below 50%. I suspect the LimitOrder at spot price is systematically missing fills in trending regimes. Should I switch to MarketOrder, or adjust the limit with a buffer?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md"],
  "sandbox_image": "quant-tutor-env:v2.2-lean",
  "network_enabled": false
}
```

**Convenient tools**: `plot_chart`

**Expected MCP tools**: `file_read`, `shell_exec`, `file_write`

**Eval strategy** (behavioral + checklist):
| Check | Weight | Method |
|-------|--------|--------|
| `order_type_fixed` | 0.25 | C# pattern: `MarketOrder` replaces `LimitOrder` for entry; no `LimitOrder` with `Securities[...].Price` |
| `fix_verified` | 0.10 | Before/after: agent shows trade count increased significantly after switching to MarketOrder |
| `root_cause_explained` | 0.10 | Conversation/logs explain: limit orders miss fills in trending markets, OR "price moves away" / "fill rate" |
| `trade_count_increased` | 0.15 | LEAN output shows significantly more filled orders than buggy version |
| `backtest_completed` | 0.15 | LEAN backtest ran to completion |
| `behavioral_score` | 0.25 | Behavioral scoring against reference (with MarketOrder) |

**Ground-truth preparation**: Write reference `order_type_bug_fixed.cs` → run via `generate_lean_reference.py` → export reference data.

---

### 3.8 X09 — Fix Alpha Model Insight Conflict in Framework Strategy

**Difficulty**: hard
**Category**: debug
**Skill category**: Framework conflict

**Student code**: `student_code/alpha_conflict.cs` (implemented)

A LEAN Algorithm Framework strategy with two alpha models (TrendAlpha and ReversionAlpha) that emit conflicting insights. The TrendAlpha emits `Insight.Up` when EMA(10) > EMA(30), while the ReversionAlpha emits `Insight.Down` when RSI > 70. When both conditions are true simultaneously (strong trend + overbought), the framework receives contradictory signals. With `EqualWeightingPortfolioConstructionModel`, the opposing insights cancel out, resulting in zero or near-zero positions.

**Bug description**: The two alpha models independently emit insights without coordination:
```csharp
// TrendAlpha: emits Up when trending up
// ReversionAlpha: emits Down when overbought
// During strong trends: both emit → cancel in EqualWeighting → no trades
```
The portfolio construction model receives Up from one alpha and Down from the other, averages them, and produces a near-zero target. This is the "insight conflict" problem — common when composing multiple alphas without a resolution strategy.

**Symptoms**:
- Very few or zero trades despite both alphas generating active insights
- Flat portfolio most of the time
- Algorithm appears to "freeze" during strong trending periods (exactly when it should be trading)
- Individual alphas would produce trades, but together they produce nothing

**Fix**: Several valid approaches (agent should guide student to at least one):
1. Add explicit `Insight.Weight` values with `InsightWeightingPortfolioConstructionModel`
2. Use a custom PCM that resolves conflicts (e.g., trend takes priority)
3. Make alphas non-overlapping (different regimes: trend alpha only in trending, reversion only in range-bound)
4. Use `CompositeAlphaModel` with explicit aggregation logic

**Description**: Guide a student to diagnose and fix an insight conflict in a LEAN Algorithm Framework strategy with two alpha models (trend + reversion) that emit contradictory signals during strong trends. The student must understand the framework's insight aggregation mechanism, including that built-in portfolio construction models act on the last active insight per symbol, and propose a resolution strategy.

**Expected outcome**: Student identifies that conflicting insights from two alphas need explicit resolution in the portfolio construction stage, understands the framework's insight-to-target pipeline, implements a conflict resolution approach (explicit weights, custom PCM, regime gating, or alpha ordering/combination), and verifies that the strategy now produces trades during strong trends.

**Required capabilities**:
1. Understand the Algorithm Framework pipeline: Alpha → Insights → PCM → Targets → Execution
2. Diagnose that conflicting Up/Down insights cancel in EqualWeighting PCM
3. Propose and implement a conflict resolution strategy
4. Verify the fix by running the backtest and observing non-zero trades

**Student openings**:
- **beginner_no_finance**: "I'm using LEAN's Algorithm Framework with two signal models — one for trends and one for mean reversion — but my strategy barely makes any trades. Both models seem to be generating signals. Why aren't any trades happening?"
- **intermediate_developer**: "My LEAN framework strategy has a TrendAlpha and ReversionAlpha. Each works fine alone but together the portfolio is flat most of the time. I think the insights are canceling each other out in the EqualWeightingPortfolioConstructionModel. How do I fix this?"
- **advanced_quant**: "I'm seeing insight cancellation in a dual-alpha framework strategy on LEAN. The TrendAlpha emits Up and ReversionAlpha emits Down during overbought trends, and EqualWeighting PCM leaves the portfolio effectively flat. I need an insight conflict resolution strategy — explicit weights, custom PCM, alpha ordering, or regime gating?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md", "algorithm_framework_guide.md"],
  "sandbox_image": "quant-tutor-env:v2.2-lean",
  "network_enabled": false
}
```

**Convenient tools**: `plot_chart`

**Expected MCP tools**: `file_read`, `shell_exec`, `file_write`

**Eval strategy** (behavioral + checklist):
| Check | Weight | Method |
|-------|--------|--------|
| `conflict_resolved` | 0.25 | C# patterns: changed PCM type, OR added explicit weight to insights, OR added regime gating logic |
| `fix_verified` | 0.10 | Before/after: agent shows trades now occur (vs ~0 before), or shows non-flat portfolio |
| `root_cause_explained` | 0.10 | Conversation/logs explain: opposing insights cancel in EqualWeighting, OR "insight conflict" / "insights cancel" |
| `trades_produced` | 0.15 | LEAN backtest produces > 10 trades (buggy version produces ~0) |
| `backtest_completed` | 0.15 | LEAN backtest ran to completion |
| `behavioral_score` | 0.25 | Behavioral scoring against reference (with conflict resolution) |

**Ground-truth preparation**: Write reference `alpha_conflict_fixed.cs` with explicit insight weights and deterministic alpha ordering → run via `generate_lean_reference.py` → export reference data.

---

### 3.9 X10 — Fix Stale Universe / Survivorship Bias in LEAN Backtest

**Difficulty**: hard
**Category**: debug
**Skill category**: Data lifecycle

**Student code**: `student_code/universe_stale.cs` (implemented)

A LEAN C# algorithm that implements a momentum strategy across multiple symbols. The algorithm pre-filters the universe using a **volume threshold computed from today's data** (end-of-backtest snapshot), then subscribes to the filtered list statically in `Initialize()`. This creates two forms of survivorship bias: (1) the volume filter uses future information (2024 volumes to select the 2022 universe), and (2) symbols that were delisted during the backtest are excluded from the universe entirely.

**Bug description**:
```csharp
// In Initialize():
// BUG: Filters symbols using a volume threshold based on today's data.
// Symbols that were illiquid in 2022 but became major by 2024 are included.
// Symbols that were liquid in 2022 but delisted by 2024 are excluded.
var topSymbols = allSymbols
    .Where(s => s.AvgDailyVolumeUsdt > 1_000_000_000)  // Filter using end-of-period volume
    .ToList();

foreach (var sym in topSymbols)
{
    AddCryptoFuture(sym, Resolution.Daily);
}
```
The volume filter uses the `AvgDailyVolumeUsdt` from `universe.json`, which reflects average volumes over the *entire* backtest period (including the future relative to the start date). This creates a point-in-time violation: the algorithm "knows" which symbols will become liquid and which will be delisted, biasing toward winners.

**Why this creates a measurable performance difference**:
- **Inclusion bias**: Symbols that grew from low volume in 2022 to high volume in 2024 (e.g., ARBUSDT, listed Mar 2023) pass the filter and are traded from their listing date. Their early momentum signals benefit from the same growth that made them high-volume.
- **Exclusion bias**: Symbols that had high volume in 2022 but declined or were delisted by 2024 never appear in the universe, removing their (often negative) contribution.
- **Net effect**: The backtest is systematically biased toward surviving, growing assets. Sharpe is inflated by ~0.3-0.5 vs the point-in-time universe.

**Symptoms**:
- Portfolio Sharpe is suspiciously higher than expected for a simple momentum strategy
- Some symbols in the universe have no data before 2023 (they listed later)
- The universe contains only "winners" — assets that are still actively traded
- Missing data warnings for recently-listed symbols in early backtest periods
- Comparing against a buy-and-hold benchmark of the *same filtered universe* shows less outperformance than expected (both share the bias)

**Fix**: Use point-in-time universe selection — filter symbols based on their volume *at the time of trading*, not at the end of the backtest:
```csharp
// Point-in-time approach: use ScheduledUniverseSelectionModel or
// check listing_date + use rolling volume at current Time
public override void OnData(Slice slice)
{
    // Only consider symbols listed before current date
    foreach (var sym in _allSymbols.Where(s => Time >= s.ListingDate))
    {
        if (!_subscribed.Contains(sym.Ticker))
        {
            AddCryptoFuture(sym.Ticker, Resolution.Daily);
            _subscribed.Add(sym.Ticker);
        }
    }
}
```
Or use `AddUniverse()` with `ScheduledUniverseSelectionModel` for a scheduled monthly rebalance that only considers currently-available symbols with sufficient trailing volume.

**Description**: Guide a student to diagnose and fix survivorship bias in a LEAN C# momentum strategy that filters its universe using end-of-period volume data instead of point-in-time information. The algorithm selects symbols based on their average daily volume over the entire backtest (including the future), creating a universe biased toward assets that survived and grew. The student must understand why point-in-time data discipline matters for backtest validity and implement date-aware universe selection.

**Expected outcome**: Student identifies the point-in-time violation (using future volume data to filter the universe), understands how this creates both inclusion bias (future winners) and exclusion bias (delisted losers), implements date-aware universe selection that respects listing dates and uses trailing volume, and verifies that the corrected backtest has lower (more realistic) Sharpe.

**Required capabilities**:
1. Understand survivorship bias and point-in-time data discipline
2. Identify that the volume filter uses end-of-period data (future information)
3. Explain how inclusion and exclusion bias inflate backtest performance
4. Implement date-aware universe management (listing date check + trailing volume filter)
5. Verify that corrected Sharpe is lower than biased version (the performance difference IS the diagnosis)

**Student openings**:
- **beginner_no_finance**: "My LEAN momentum strategy has a really good Sharpe ratio, but my professor says I might have 'survivorship bias.' I filtered my symbols by average volume from the data file. Could that be a problem?"
- **intermediate_developer**: "My LEAN backtest filters the universe using an average volume threshold from universe.json, but I realize this volume is computed over the entire period including the future. Some symbols in my universe didn't even exist at the start of the backtest. How do I implement point-in-time universe selection?"
- **advanced_quant**: "I'm getting inflated Sharpe from a momentum strategy due to survivorship bias in universe construction. My volume filter uses end-of-period averages, creating a look-ahead in the asset selection step. I need to implement point-in-time universe selection with trailing volume — using LEAN's AddUniverse or a manual approach. What's the cleanest architecture?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md"],
  "sandbox_image": "quant-tutor-env:v2.2-lean",
  "network_enabled": false
}
```

**Convenient tools**: `plot_chart`

**Expected MCP tools**: `file_read`, `shell_exec`, `file_write`

**Eval strategy** (behavioral + checklist):
| Check | Weight | Method |
|-------|--------|--------|
| `point_in_time_universe` | 0.25 | C# patterns: `AddUniverse` with date filter, OR listing-date-conditional `AddCryptoFuture`, OR `OnSecuritiesChanged` with date check |
| `listing_date_check` | 0.15 | C# pattern: comparison of `Time`/current date against listing date before subscription |
| `volume_filter_fixed` | 0.10 | C# pattern: trailing volume computation replaces static `AvgDailyVolumeUsdt` filter |
| `fix_verified` | 0.10 | Tool logs show before/after comparison — agent ran both buggy and fixed versions and noted Sharpe decrease |
| `root_cause_explained` | 0.10 | Conversation/logs contain: "survivorship" or "point-in-time" or "look-ahead" in universe context |
| `backtest_completed` | 0.05 | LEAN backtest ran to completion |
| `behavioral_score` | 0.25 | Behavioral scoring against reference (with point-in-time universe) |

**Ground-truth preparation**: Write reference `universe_stale_fixed.cs` with point-in-time universe selection → run via `generate_lean_reference.py` → export reference data. Also run the *buggy* version to record the inflated Sharpe for before/after comparison validation.

---

## 4. Evaluation Strategy

### 4.1 Two Evaluation Approaches

X-series uses two distinct evaluation approaches matching the two tiers:

**Tier A: Python tasks (X01-X06) — Checklist-based**

Each eval script defines a weighted checklist of verifiable criteria:
- **Bug is fixed**: Regex pattern matching on workspace files and tool logs to verify the specific fix was applied
- **Code runs correctly**: Tool log analysis showing successful execution with expected output keywords
- **Domain verification**: Task-specific checks (returns decreased for look-ahead, short positions exist, etc.)

This is the same evaluation pattern as the existing X01 eval script. Checklist items are binary (pass/fail) with pre-assigned weights summing to 1.0.

**Cross-cutting scoring items** (present in all X02-X10 eval tables):

| Item | Purpose | Weight range |
|------|---------|-------------|
| `fix_verified` | Agent ran code before AND after fix, showed output changed | 0.10-0.20 |
| `root_cause_explained` | Agent explained WHY the bug causes incorrect behavior (mechanism, not just "it was wrong") | 0.10-0.15 |

These enforce the complete debugging workflow: observe → diagnose → **explain** → fix → **verify**. Without these items, an agent could score well by just applying the fix without understanding or confirming it works. The `fix_verified` check requires evidence of before/after comparison in tool logs. The `root_cause_explained` check scans conversation and tool logs for domain-specific keywords that indicate mechanistic understanding.

**Tier B: LEAN tasks (X07-X10) — Behavioral scoring + checklist**

LEAN tasks combine:
1. **Code pattern checks** (checklist): Verify specific C# code changes were made (e.g., `SetWarmUp` added, `MarketOrder` used)
2. **Behavioral scoring**: Run the fixed algorithm on LEAN and compare output against a reference using the I-series `compute_behavioral_score()` infrastructure

The behavioral portion reuses `_implementation_check.py`'s signal agreement, position overlap, performance metrics, and trade similarity layers. The checklist portion is task-specific.

### 4.2 Shared Debug Eval Helper: `_debug_check.py`

A shared helper module for X-series eval scripts, analogous to `_implementation_check.py` for I-series:

```python
# bench/evaluation/test_scripts/_debug_check.py

def check_fix_applied(
    workspace_path: str,
    tool_logs: list,
    fix_patterns: list[str],
    bug_patterns: list[str],
    file_extension: str = ".py",
) -> dict:
    """Check if a specific fix was applied in workspace files and tool logs.

    Args:
        workspace_path: Path to workspace directory
        tool_logs: List of ToolCallLog objects
        fix_patterns: Regex patterns that should be present after fix
        bug_patterns: Regex patterns that should be absent after fix
        file_extension: File type to scan (.py or .cs)

    Returns:
        {"fixed": bool, "fix_found_in": str, "bug_still_present": bool}
    """

def check_execution_output(
    tool_logs: list,
    success_keywords: list[str],
    failure_keywords: list[str] = None,
) -> dict:
    """Check tool logs for successful execution evidence.

    Args:
        tool_logs: List of ToolCallLog objects
        success_keywords: Keywords indicating successful execution
        failure_keywords: Keywords indicating failure (default: ["traceback", "error"])

    Returns:
        {"executed": bool, "output_valid": bool}
    """

def check_conversation_concepts(
    conversation: list,
    required_concepts: list[str],
    tool_logs: list = None,
) -> dict:
    """Check if required debugging concepts were discussed.

    Used for conceptual tasks like X06 (overfitting diagnosis).

    Args:
        conversation: Conversation history
        required_concepts: Keywords/phrases that should appear
        tool_logs: Optional tool logs to also scan

    Returns:
        {"concepts_found": dict[str, bool], "fraction": float}
    """

def check_root_cause_explained(
    conversation: list,
    tool_logs: list,
    root_cause_keywords: list[str],
) -> bool:
    """Check if the agent explained the root cause mechanism.

    Scans both conversation and tool log outputs for keywords that indicate
    the agent explained WHY the bug causes incorrect behavior, not just
    WHAT the fix is. This is the "diagnostic reasoning" check.

    Args:
        conversation: Conversation history
        tool_logs: List of ToolCallLog objects
        root_cause_keywords: Keywords indicating root cause explanation
            e.g., ["look-ahead", "future information", "shift"]

    Returns:
        True if at least one root cause keyword is found in context
    """

def check_fix_verified(
    tool_logs: list,
    before_keywords: list[str],
    after_keywords: list[str],
) -> bool:
    """Check if the agent verified the fix with before/after comparison.

    Looks for evidence that the agent ran the code both before and after
    the fix and compared output. This enforces the full debugging workflow:
    observe → diagnose → fix → VERIFY.

    Args:
        tool_logs: List of ToolCallLog objects
        before_keywords: Keywords from buggy output (e.g., high Sharpe value)
        after_keywords: Keywords from fixed output (e.g., lower Sharpe value)

    Returns:
        True if both before and after evidence found in tool logs
    """
```

### 4.3 Data Source Verification

All X-series eval scripts include the standard `verify_data_source()` check from `_data_source_check.py`:
- Python tasks (X01-X06): verify `AAPL_2018_2024.csv` or `BTC_UTC.csv` was accessed
- LEAN tasks (X07-X10): verify `universe.json` was accessed

When data source is not verified, score is capped at `max(0.25, fraction)`.

### 4.4 Eval Script Pattern (Python tasks)

```python
"""Evaluation script for X0N: [description]."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source
from _debug_check import check_fix_applied, check_execution_output


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "bug_is_fixed": False,
        "code_runs_without_error": False,
        # ... task-specific checks ...
        "score": 0.0,
    }

    # 1. Check if fix was applied
    fix_result = check_fix_applied(
        workspace_path, tool_logs or [],
        fix_patterns=[r"<fix regex>"],
        bug_patterns=[r"<bug regex>"],
    )
    results["bug_is_fixed"] = fix_result["fixed"]

    # 2. Check execution
    exec_result = check_execution_output(
        tool_logs or [],
        success_keywords=["sharpe", "return", ...],
    )
    results["code_runs_without_error"] = exec_result["executed"]

    # 3. Task-specific checks
    # ...

    # 4. Scoring
    _checklist = [
        {"item": "bug_is_fixed", "weight": W1, "passed": results["bug_is_fixed"]},
        {"item": "code_runs_without_error", "weight": W2, "passed": results["code_runs_without_error"]},
        # ...
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # 5. Data source verification
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results
```

### 4.5 Eval Script Pattern (LEAN tasks)

```python
"""Evaluation script for X0N: [LEAN debug description]."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source
from _debug_check import check_fix_applied
from _implementation_check import compute_behavioral_score, collect_lean_results


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "fix_applied": False,
        "backtest_completed": False,
        "behavioral_composite": 0.0,
        "score": 0.0,
    }

    # 1. Check C# fix patterns
    fix_result = check_fix_applied(
        workspace_path, tool_logs or [],
        fix_patterns=[r"<fix C# regex>"],
        bug_patterns=[r"<bug C# regex>"],
        file_extension=".cs",
    )
    results["fix_applied"] = fix_result["fixed"]

    # 2. Check backtest completion
    lean_results = collect_lean_results(workspace_path)
    results["backtest_completed"] = lean_results is not None

    # 3. Behavioral scoring (reuse I-series infrastructure)
    behavioral = compute_behavioral_score("X0N", workspace_path, resolution="daily")
    results["behavioral_composite"] = round(behavioral.composite_score, 4)

    # 4. Scoring
    _checklist = [
        {"item": "fix_applied",        "weight": W1, "passed": results["fix_applied"]},
        {"item": "backtest_completed", "weight": W2, "passed": results["backtest_completed"]},
        {"item": "behavioral_score",   "weight": W3, "score": behavioral.composite_score},
    ]
    score = sum(
        c["weight"] * c.get("score", 1.0 if c.get("passed") else 0.0)
        for c in _checklist
    )

    # 5. Data source verification
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results
```

---

## 5. Difficulty & Capability Progression

### 5.1 Tier A: Python/pandas (X01-X06)

```
X01  Off-by-one in MA window          easy      Simplest numerical bug
 │                                                (find rolling(19), change to 20)
 ▼
X02  Look-ahead bias (no shift)       easy      Temporal logic bug
 │                                                (recognize signal timing, add shift(1))
 ▼
X03  Missing short position (0≠-1)    medium    State encoding bug
 │                                                (understand long/flat/short model)
 ▼
X04  diff() vs pct_change()           medium    Mathematical formula bug
 │                                                (recognize dollar vs percent returns)
 ▼
X05  Timezone misalignment            hard      Cross-domain bug
 │                                                (UTC vs ET, date boundary effects)
 ▼
X06  Overfitting diagnosis            hard      Conceptual/statistical reasoning
                                                  (no line to fix — diagnosis task)
```

### 5.2 Tier B: LEAN C# (X07-X10)

```
X07  Missing warm-up period           hard      Engine initialization bug
 │                                                (SetWarmUp + IsWarmingUp guard)
 ▼
X08  Wrong order type (Limit→Market)  hard      API misuse bug
 │                                                (LimitOrder at spot → MarketOrder)
 ▼
X09  Alpha insight conflict           hard      Framework architecture bug
 │                                                (conflicting insights cancel in PCM)
 ▼
X10  Survivorship bias / stale universe hard     Data lifecycle bug
                                                  (static universe → dynamic handling)
```

### 5.3 Three Progression Dimensions

1. **Bug subtlety**: visible numerical error (X01) → invisible temporal bias (X02) → domain knowledge required (X04-X06) → architectural understanding (X09-X10)
2. **Fix complexity**: single-line change (X01-X04) → multi-line structural change (X05, X07-X08) → architectural redesign (X09-X10) → conceptual diagnosis (X06)
3. **System scope**: standalone script (X01-X06) → single algorithm on engine (X07-X08) → multi-component framework (X09-X10)

---

## 6. Scoring and Judging

### 6.1 Result Judge Category Rubric

The `debug` rubric already exists in `result_judge.py:76-85`:

```
Focus on: (1) correct identification of the root cause, not just surface symptoms
(e.g., off-by-one in index vs wrong formula); (2) targeted fix that addresses the
underlying issue without introducing new problems; (3) verification that the fix
restores correct behavior with concrete evidence (before/after output comparison);
(4) explanation of why the original code failed (the mechanism, not just 'it was wrong');
(5) defensive checks or best practices suggested to prevent similar bugs.
```

### 6.2 Tutor 7D Dimension Weights

The `debug` category weights already exist in `tutor_conv_geval.py:220-228`:

| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| D1: Level Detection | 0.7 | Important but secondary to debugging skill |
| D2: Language Adaptation | 0.7 | Important but secondary |
| D3: Scaffolding | 1.0 | Critical — debugging is inherently scaffolded (observe → hypothesize → test → fix) |
| D4: Domain Accuracy | 1.0 | Critical — wrong diagnosis is worse than no diagnosis |
| D5: Code Teaching | 1.0 | Critical — debugging IS code teaching |
| D6: Empathetic Response | 0.7 | Important but secondary |
| D7: Safety & Boundaries | 0.3 | Less critical for debugging tasks |

### 6.3 Process Reasonableness Criteria

Add to `CATEGORY_PROCESS_CRITERIA`:

```
debug: "read buggy code → run code to observe symptoms →
        form hypothesis about root cause → add diagnostic checks →
        isolate bug to specific line/function → explain root cause mechanism →
        apply minimal fix → run code again to verify fix →
        compare before/after output → confirm fix restores correct behavior"
```

Score caps for missing critical behaviors:
- Never read the student code → cap at 0.10
- Read code but never ran it → cap at 0.20
- Ran code but proposed wrong fix → cap at 0.30
- Fixed the bug but never explained root cause → cap at 0.40
- Fixed the bug but didn't verify (no before/after comparison) → cap at 0.50
- Fixed and verified but explanation was superficial ("it was wrong") → cap at 0.70

---

## 7. Reference Generation

### 7.1 Python Tasks (X01-X06): No Reference Generation Needed

Python debug tasks use checklist-based evaluation that checks for specific code patterns and output characteristics. They do **not** need reference trade logs, signals, or behavioral scoring. The student code files already contain the bugs, and the eval scripts check for the specific fixes.

### 7.2 LEAN Tasks (X07-X10): Reference Generation Required

Each LEAN debug task needs:
1. **Buggy student code** (`student_code/*.cs`): The broken algorithm the student receives
2. **Fixed reference code** (`bench/reference/lean_algorithms/X0N_*.cs`): The corrected algorithm
3. **Reference data** (`bench/data/reference/X0N_*`): Trades, signals, summary from running the fixed algorithm

Reference generation follows the same pipeline as I-series:
```bash
# Generate reference for X07 (after writing X07_warmup_fixed.cs)
python bench/reference/generate_lean_reference.py --task X07

# This produces:
# bench/data/reference/X07_reference_trades.json
# bench/data/reference/X07_reference_signals.json
# bench/data/reference/X07_reference_summary.json
```

The buggy student code is **NOT** run through reference generation — it serves only as input to the debugging session.

---

## 8. Integration Map

### 8.1 Complete File Inventory

| Component | Status | Location |
|-----------|--------|----------|
| **Shared Infrastructure** | | |
| `_debug_check.py` shared helper | Done | `bench/evaluation/test_scripts/_debug_check.py` |
| `_data_source_check.py` | Done | `bench/evaluation/test_scripts/_data_source_check.py` |
| `_implementation_check.py` | Done | `bench/evaluation/test_scripts/_implementation_check.py` |
| **Task JSONs (10 total)** | | |
| X01 task JSON | Done | `bench/tasks/layer2/debug/X01_ma_offbyone.json` |
| X02 task JSON | Done | `bench/tasks/layer2/debug/X02_lookahead.json` |
| X03 task JSON | Done | `bench/tasks/layer2/debug/X03_position_bug.json` |
| X04 task JSON | Done | `bench/tasks/layer2/debug/X04_returns_diff.json` |
| X05 task JSON | Done | `bench/tasks/layer2/debug/X05_timezone_merge.json` |
| X06 task JSON | Done | `bench/tasks/layer2/debug/X06_overfit_single.json` |
| X07 task JSON | Done | `bench/tasks/layer2/debug/X07_warmup_bug.json` |
| X08 task JSON | Done | `bench/tasks/layer2/debug/X08_order_type_bug.json` |
| X09 task JSON | Done | `bench/tasks/layer2/debug/X09_alpha_conflict.json` |
| X10 task JSON | Done | `bench/tasks/layer2/debug/X10_universe_stale.json` |
| **Eval Scripts (10 total)** | | |
| X01 eval script | Done | `bench/evaluation/test_scripts/X01_ma_offbyone.py` |
| X02 eval script | Done | `bench/evaluation/test_scripts/X02_lookahead.py` |
| X03 eval script | Done | `bench/evaluation/test_scripts/X03_position_bug.py` |
| X04 eval script | Done | `bench/evaluation/test_scripts/X04_returns_diff.py` |
| X05 eval script | Done | `bench/evaluation/test_scripts/X05_timezone_merge.py` |
| X06 eval script | Done | `bench/evaluation/test_scripts/X06_overfit_single.py` |
| X07 eval script | Done | `bench/evaluation/test_scripts/X07_warmup_bug.py` |
| X08 eval script | Done | `bench/evaluation/test_scripts/X08_order_type_bug.py` |
| X09 eval script | Done | `bench/evaluation/test_scripts/X09_alpha_conflict.py` |
| X10 eval script | Done | `bench/evaluation/test_scripts/X10_universe_stale.py` |
| **Student Code — Python (6 files)** | | |
| `ma_offbyone.py` | Done | `bench/student_code/ma_offbyone.py` |
| `lookahead.py` | Done | `bench/student_code/lookahead.py` |
| `position_bug.py` | Done | `bench/student_code/position_bug.py` |
| `returns_diff.py` | Done | `bench/student_code/returns_diff.py` |
| `timezone_merge.py` | Done | `bench/student_code/timezone_merge.py` |
| `overfit_single.py` | Done | `bench/student_code/overfit_single.py` |
| **Student Code — C# (4 files)** | | |
| `warmup_bug.cs` | Done | `bench/student_code/warmup_bug.cs` |
| `order_type_bug.cs` | Done | `bench/student_code/order_type_bug.cs` |
| `alpha_conflict.cs` | Done | `bench/student_code/alpha_conflict.cs` |
| `universe_stale.cs` | Done | `bench/student_code/universe_stale.cs` |
| **Reference Algorithms — C# (4 files)** | | |
| `X07_warmup_fixed.cs` | Done | `bench/reference/lean_algorithms/X07_warmup_fixed.cs` |
| `X08_order_type_fixed.cs` | Done | `bench/reference/lean_algorithms/X08_order_type_fixed.cs` |
| `X09_alpha_conflict_fixed.cs` | Done | `bench/reference/lean_algorithms/X09_alpha_conflict_fixed.cs` |
| `X10_universe_stale_fixed.cs` | Done | `bench/reference/lean_algorithms/X10_universe_stale_fixed.cs` |
| **Data Files** | | |
| `BTC_UTC.csv` | Done | `bench/data/frozen/BTC_UTC.csv` (1,096 rows, 2022-2025, UTC timestamps) |
| X07-X10 reference data | Pending | Requires `generate_lean_reference.py --task X07..X10` via LEAN Docker |
| **Pre-existing Infrastructure** | | |
| Orchestrator debug mounting | Done | `orchestrator.py:172-173` mounts `/student_code` when `category == "debug"` |
| Prompt config sample_code injection | Done | `prompt_config.py:162-165` |
| Schema `sample_code` field | Done | `schemas.py:90` |
| Schema `DEBUG` category | Done | `schemas.py:23` |
| Tool executor student_code path | Done | `tools.py:199-201` |
| Result judge debug rubric | Done | `result_judge.py:76-85` |
| Tutor 7D debug weights | Done | `tutor_conv_geval.py:220-228` |
| LEAN Docker sandbox | Done | `quant-tutor-env:v2.2-lean` |
| Python sandbox | Done | `quant-tutor-env:v2.2` |

### 8.2 What Was Created (Implementation Complete)

| Component | Status | Location |
|-----------|--------|----------|
| X02-X06 task JSONs | **Done** | `bench/tasks/layer2/debug/X02_lookahead.json` through `X06_overfit_single.json` |
| X02-X06 eval scripts | **Done** | `bench/evaluation/test_scripts/X02_lookahead.py` through `X06_overfit_single.py` |
| `_debug_check.py` shared helper | **Done** | `bench/evaluation/test_scripts/_debug_check.py` — 5 functions: `check_fix_applied`, `check_execution_output`, `check_conversation_concepts`, `check_root_cause_explained`, `check_fix_verified` |
| X07-X10 buggy student code (.cs) | **Done** | `bench/student_code/warmup_bug.cs`, `order_type_bug.cs`, `alpha_conflict.cs`, `universe_stale.cs` |
| X07-X10 reference algorithms (.cs) | **Done** | `bench/reference/lean_algorithms/X07_warmup_fixed.cs` through `X10_universe_stale_fixed.cs` |
| X07-X10 task JSONs | **Done** | `bench/tasks/layer2/debug/X07_warmup_bug.json` through `X10_universe_stale.json` |
| X07-X10 eval scripts | **Done** | `bench/evaluation/test_scripts/X07_warmup_bug.py` through `X10_universe_stale.py` |
| X07-X10 reference data | **Pending** | Requires running `generate_lean_reference.py --task X07..X10` via LEAN Docker |
| `BTC_UTC.csv` data file | **Done** | `bench/data/frozen/BTC_UTC.csv` — 1,096 daily rows (2022-2025) from hourly BTCUSDT data |

### 8.3 What Was Extended (Implementation Complete)

| Component | Change | Status |
|-----------|--------|--------|
| `generate_lean_reference.py` | Added X07-X10 to `TASK_ALGO_MAP` and `class_name_map` | **Done** |
| `_implementation_check.py` | Already supports arbitrary task IDs in `compute_behavioral_score()` — no changes needed | N/A |
| `orchestrator.py` | Already mounts both student_code (debug) and LEAN data (lean sandbox) independently — no changes needed | N/A |

---

## 9. Persona Considerations

### 9.1 Debug-Specific Persona Behavior

All X-tasks use the same three personas as I-series. Debug tasks create natural persona differentiation:

| Persona | Debugging Behavior | Opening Style |
|---------|-------------------|---------------|
| **beginner_no_finance** | "Something seems off but I don't know what" — notices symptoms but cannot form hypotheses | Describes symptoms, asks for help understanding |
| **intermediate_developer** | "I think the bug is in X but I'm not sure" — has partial hypothesis | Points to suspected area, asks for confirmation |
| **advanced_quant** | "I've narrowed it to this function, here's my analysis" — has strong hypothesis, may be right or wrong | Presents analysis, asks for expert review |

### 9.2 Opening Design Rules

Same as I-series (§9.2 in implementation plan):
- One entry point per persona, no capability enumeration
- Beginners express confusion about symptoms
- Intermediates state a partial hypothesis
- Advanced present a focused diagnostic question

---

## 10. Task Summary Table

| Task | Title | Difficulty | Tier | Bug Type | Student Code | Sandbox | Key Challenge |
|------|-------|-----------|------|----------|-------------|---------|---------------|
| X01 | MA Off-by-One | easy | Python | Numerical precision | `ma_offbyone.py` | v2.2 | Find rolling(19)→20 |
| X02 | Look-Ahead Bias | easy | Python | Temporal logic | `lookahead.py` | v2.2 | Add shift(1) to position |
| X03 | Missing Short Position | medium | Python | State machine | `position_bug.py` | v2.2 | Change 0→-1 for short |
| X04 | diff() vs pct_change() | medium | Python | Mathematical | `returns_diff.py` | v2.2 | Replace diff with pct_change |
| X05 | Timezone Misalignment | hard | Python | Cross-domain | `timezone_merge.py` | v2.2 | Add tz_convert before merge |
| X06 | Overfitting Diagnosis | hard | Python | Statistical | `overfit_single.py` | v2.2 | Diagnose + propose remedies |
| X07 | Missing Warm-Up | hard | LEAN | Initialization | `warmup_bug.cs` | v2.2-lean | Add SetWarmUp + guard |
| X08 | Order Type Misuse | hard | LEAN | API misuse | `order_type_bug.cs` | v2.2-lean | LimitOrder→MarketOrder |
| X09 | Alpha Insight Conflict | hard | LEAN | Framework conflict | `alpha_conflict.cs` | v2.2-lean | Resolve opposing insights |
| X10 | Stale Universe | hard | LEAN | Data lifecycle | `universe_stale.cs` | v2.2-lean | Dynamic universe handling |

**X-series total**: 10 tasks × 3 personas = **30 evaluation instances**

---

## 11. Implementation Status

### Phase 1: Python Tasks (X02-X06) — **COMPLETE**

All completed:

1. **`_debug_check.py`** shared helper — 5 reusable functions
2. **X02** (look-ahead) — task JSON + eval script (5 checks, weights sum to 1.0)
3. **X03** (position bug) — task JSON + eval script
4. **X04** (returns diff) — task JSON + eval script
5. **X05** (timezone) — task JSON + eval script + `BTC_UTC.csv` (1,096 daily rows from hourly data)
6. **X06** (overfitting) — task JSON + eval script (6 concept-based checks, no single-line fix)

### Phase 2: LEAN Tasks (X07-X10) — **COMPLETE** (except reference data)

All completed except reference data generation (requires LEAN Docker):

1. **Buggy student code**: `warmup_bug.cs`, `order_type_bug.cs`, `alpha_conflict.cs`, `universe_stale.cs`
2. **Reference algorithms**: `X07_warmup_fixed.cs`, `X08_order_type_fixed.cs`, `X09_alpha_conflict_fixed.cs`, `X10_universe_stale_fixed.cs`
3. **`generate_lean_reference.py`**: Extended with X07-X10 in `TASK_ALGO_MAP` and `class_name_map`
4. **Task JSONs**: All 4 created with LEAN sandbox config
5. **Eval scripts**: All 4 created with hybrid behavioral + checklist scoring
6. **Orchestrator**: No changes needed — debug + lean mounts fire independently

**Remaining**: Run reference data generation:
```bash
python bench/reference/generate_lean_reference.py --task X07
python bench/reference/generate_lean_reference.py --task X08
python bench/reference/generate_lean_reference.py --task X09
python bench/reference/generate_lean_reference.py --task X10
```

### Phase 3: Validation — **PARTIAL**

Completed:
1. All 9 task JSONs parse correctly with proper category/difficulty/sandbox
2. All 10 eval scripts compile with valid Python syntax
3. All checklist weights verified to sum to 1.0
4. All C# class names verified (QuantTutorBench namespace, correct inheritance)
5. Bugs confirmed in code (not comments), fixes confirmed in reference algorithms
6. Student Python code runs without crashes (produces buggy but valid output)

Remaining:
- Full integration dry-run after reference data generation
- Self-test with fixed code → should score high
- Bug-test with buggy code → should score low

---

## 12. Open Questions & Decisions (Resolved)

1. **X06 eval approach**: **Resolved** — Uses conversation keyword matching with 6 weighted checks (overfitting_identified 0.25, excessive_params_noted 0.20, remedy_proposed 0.15, etc.). No requirement to produce a simplified strategy — the eval focuses on diagnostic reasoning.

2. **X07-X10 difficulty calibration**: **Resolved** — All LEAN tasks remain hard. Can be revisited after benchmarking.

3. **BTC_UTC.csv generation**: **Resolved** — Generated from I-series raw hourly data (`bench/data/raw/i-series/tier2_hourly/BTCUSDT_1h.csv`), resampled to daily OHLCV with explicit UTC timestamps (ISO 8601 with `+00:00`). Saved to `bench/data/frozen/BTC_UTC.csv` (1,096 rows, 2022-2025). Generator script at `bench/scripts/generate_btc_utc.py`.

4. **Debug + LEAN integration**: **Resolved** — No orchestrator changes needed. The orchestrator already handles debug category mounting (`student_code_dir`) and LEAN mounting (`lean_data_dir`) as independent conditions. For X07-X10, both conditions fire: `task.category.value == "debug"` triggers student_code mount, and `"lean" in sandbox_image` triggers LEAN data mount.

5. **Reference trade counts for X07-X10**: **Resolved** — X07 uses single BTCUSDT (like I01), X08 uses single BTCUSDT, X09 uses first 10 from universe (daily), X10 uses first 30 from universe (daily). All use 2022-2025 period consistent with I-series.

6. **Transaction cost omission (X11 candidate)**: **Deferred** to v2.1 as planned.
