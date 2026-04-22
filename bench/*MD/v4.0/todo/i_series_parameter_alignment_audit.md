# I-Series Parameter Alignment Audit

Investigation date: 2026-03-30

## Background

I-series tasks use behavioral scoring (signal_agreement, position_overlap, performance_match, trade_similarity) to compare agent-produced LEAN backtests against pre-computed reference results. Behavioral scoring is sensitive to indicator parameters — if the agent uses SMA(50) but the reference uses SMA(20), the signal_agreement score drops significantly even though the implementation is technically correct.

The agent (tutor) does NOT see `expected_outcome` or `required_capabilities` directly. It only sees:
- Student opening message (first user message)
- Student profile (persona description)
- Tool usage directives
- Reference documentation (`lean_algorithm_guide.md`, `moving_averages.md`)

The student simulator sees `scenario` (which includes learning goals from `required_capabilities`) and `user_description`, but NOT `expected_outcome` in its message generation prompt. `expected_outcome` is only used by the stop_conversation checker.

Therefore, **the only way to guide the agent to use specific parameters is through the student opening message and learning goals**.

## Reference Document Risk

`lean_algorithm_guide.md` contains examples using SMA(20)/SMA(50), EMA(21), RSI(14). For tasks where the reference algorithm uses different parameters (e.g., I02 uses SMA 10/30), the agent is likely to pick parameters from the guide examples instead.

## Per-Task Analysis

### I02 — Trend Following

| Item | Value |
|---|---|
| **Reference** | SMA(10)/SMA(30) crossover, long-only, equal-weight, daily |
| **EO** | "creates SMA indicators per symbol" — **no periods specified** |
| **Openings** | None of 3 personas specify SMA periods |
| **Guide examples** | SMA(20)/SMA(50) — **conflicts with reference** |
| **Risk** | **HIGH** — agent will almost certainly pick wrong periods |
| **Recommendation** | Strong guidance: all 3 openings add "SMA(10)/SMA(30) dual crossover" |

### I03 — Mean Reversion

| Item | Value |
|---|---|
| **Reference** | RSI(14), long entry<30, exit>50, short entry>70, exit<50, stop 5%, size 5% |
| **EO** | Mentions "RSI" and "5% per position" but **no RSI period or thresholds** |
| **Openings** | None specify parameters |
| **Guide examples** | RSI(14) — matches reference period |
| **Risk** | **MEDIUM** — RSI(14) is industry standard (agent may guess correctly), but entry/exit thresholds and stop-loss need explicit specification |
| **Recommendation** | Weak guidance: learning goals add "RSI(14) with entry at 30/70, exit at 50, and 5% stop-loss" |

### I04 — Multi-Timeframe

| Item | Value |
|---|---|
| **Reference** | EMA(20) on 4h, RSI(14) on 1h, entry threshold<40, exit>60 |
| **EO** | "20-period EMA on 4h bars and RSI on 1h bars" — **has EMA period but no RSI period/thresholds** |
| **Openings** | beginner mentions "4-hour", others have no params |
| **Risk** | **MEDIUM** — EMA(20) is in EO (but agent can't see EO), RSI(14) likely guessed correctly, but 40/60 thresholds are non-standard |
| **Recommendation** | Weak guidance: learning goals add "EMA(20) on 4h, RSI(14) on 1h, long entry when RSI<40, exit when RSI>60" |

### I05 — Cross-Asset Pairs

| Item | Value |
|---|---|
| **Reference** | Z-score lookback=20, entry=±2.0, exit=±0.5, per-leg=5%, max 10 pairs |
| **EO** | Mentions "ratio z-scores" and "up to 10 active pairs" but **no z-score thresholds or lookback** |
| **Openings** | None specify parameters |
| **Risk** | **MEDIUM** — z-score=2.0 is statistically common, lookback=20 is common. Agent may guess correctly |
| **Recommendation** | Weak guidance: learning goals add "rolling z-score with lookback 20, entry at |z|>2.0, exit at |z|<0.5" |

### I06 — Multi-Signal Sweep

| Item | Value |
|---|---|
| **Reference** | SMA(20)/SMA(60), RSI(14), 19 weight combos (trend/reversion/carry in [0.1, 0.5]) |
| **EO** | Mentions "19 weight combinations" and signal types but **no indicator periods** |
| **Openings** | None specify parameters |
| **Risk** | **LOW** — sweep task; `sweep_completed` (15% weight) matters more than exact base parameters. Behavioral_score weight is 0.60 but tested against equal-weight baseline |
| **Recommendation** | Weak guidance: learning goals add "trend via SMA(20)/SMA(60), reversion via RSI(14)" |

### I07 — Alpha Model Framework

| Item | Value |
|---|---|
| **Reference** | EMA(10)/EMA(30) crossover, ~20 symbols, daily |
| **EO** | **Explicitly specifies** "EMA crossovers via Insight.Price(...)" and "EMA(10)/EMA(30)" |
| **Openings** | Only advanced has "EMA(10)/EMA(30)"; beginner/intermediate have no params |
| **Risk** | **MEDIUM** — EO has params but agent can't see EO. Guide example uses EMA(21). Behavioral_score weight is 0.40 (lower than I01-I06) |
| **Recommendation** | Strong guidance: beginner/intermediate openings add "EMA(10)/EMA(30) crossover" |

### I08 — Multi-Alpha Composition

| Item | Value |
|---|---|
| **Reference** | TrendAlpha=SMA(20)/SMA(50), MeanReversionAlpha=RSI(14), MomentumAlpha=ROC(20) |
| **EO** | Does **not** specify any indicator periods |
| **Openings** | Advanced has "SMA 20/50, RSI 14"; beginner/intermediate have no params |
| **Risk** | **MEDIUM** — behavioral_score weight is 0.30; architecture checks (multi_alpha, portfolio_model_comparison) are 0.35 combined. Parameter mismatch hurts but doesn't dominate |
| **Recommendation** | Weak guidance: learning goals add "TrendAlpha(SMA 20/50), MeanReversionAlpha(RSI 14), MomentumAlpha(ROC 20)" |

### I09 — Risk Management

| Item | Value |
|---|---|
| **Reference** | EMA(24)/EMA(72) hourly, ~20 symbols |
| **EO** | **Explicitly specifies** "EMA(24)/EMA(72) hourly alpha" |
| **Openings** | **None** of 3 personas specify EMA periods (not even advanced) |
| **Guide examples** | No EMA(24) example in guide — **agent has zero guidance** |
| **Risk** | **HIGH** — EMA(24)/EMA(72) is unusual, not in any example doc, and no opening mentions it |
| **Recommendation** | Strong guidance: all 3 openings add "EMA(24)/EMA(72) hourly" |

### I10 — Parameter Optimization

| Item | Value |
|---|---|
| **Reference** | EMA fast∈{5,10,15,20,25,30}, slow∈{20-100 step 10}, threshold∈{0-0.02 step 0.005} |
| **EO** | Mentions "GetParameter() for fast_period, slow_period, and signal_threshold" and "~250 combinations" |
| **Openings** | Advanced has partial grid spec |
| **Risk** | **LOW** — parameter range selection is the task's core skill. Behavioral_score weight is only 0.20. Grid completeness (0.15) and results structure (0.10) matter more |
| **Recommendation** | No changes needed. Parameter range is part of the task |

## Summary Table

| Task | Risk | Guidance Level | Where to Modify | Key Parameters to Add |
|---|---|---|---|---|
| I02 | HIGH | Strong (openings) | All 3 openings + learning goals | SMA(10)/SMA(30) crossover |
| I03 | MEDIUM | Weak (learning goals) | Learning goals | RSI(14), entry 30/70, exit 50, stop 5% |
| I04 | MEDIUM | Weak (learning goals) | Learning goals | RSI(14), entry<40, exit>60 |
| I05 | MEDIUM | Weak (learning goals) | Learning goals | z-score lookback 20, entry ±2.0, exit ±0.5 |
| I06 | LOW | Weak (learning goals) | Learning goals | SMA(20)/SMA(60), RSI(14) |
| I07 | MEDIUM | Strong (openings) | beginner/intermediate openings | EMA(10)/EMA(30) |
| I08 | MEDIUM | Weak (learning goals) | Learning goals | SMA(20/50), RSI(14), ROC(20) |
| I09 | HIGH | Strong (openings) | All 3 openings | EMA(24)/EMA(72) hourly |
| I10 | LOW | None | — | Parameter range is task skill |

## Next Steps

1. Run I01 (already modified) with all 3 personas to validate the approach
2. Compare behavioral_scores with vs without parameter guidance
3. If validated, apply modifications to I02-I09 per the recommendations above
4. For "weak guidance" tasks, test whether learning goals alone are sufficient or if opening modifications are also needed
