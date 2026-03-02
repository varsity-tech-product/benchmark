# QuantTutorBench Task Classification
## Macro → Micro, Easy → Hard

**Organizing principle:** Follow the real quant workflow — you can't research without data, can't validate without a backtest system, can't manage risk without a portfolio framework. Each tier builds on the previous one.

---

## Tier 1: DATA FOUNDATION
> *"Garbage in, garbage out." Nothing works without clean, properly structured data.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 1 | D10 | Historical Data Fetch | Easy | D | — | Pull historical market + macro data from API docs, store CSV artifacts, and validate adjusted prices + point-in-time macro usage |
| 2 | D11 | Realtime Data Fetch | Medium | D | D10 | Capture streaming/polling updates, persist ticks to CSV, and validate quote/trade semantics + latency/timezone handling |
| 3 | D01 | OHLCV Data Exploration | Easy | D | D10 | Load and inspect fetched price data |
| 4 | D03 | Data Type Conversion & Validation | Easy | D | D01 | Parse dates, numeric types, schema checks |
| 5 | D02 | Missing Data Detection & Handling | Easy | D | D01,D03 | Identify gaps, holidays vs. real missing |
| 6 | D04 | OHLCV Summary Statistics | Easy | D | D01,D03 | Descriptive stats on price/volume data |
| 7 | D05 | Return Computation (Simple vs Log) | Medium | D | D04 | Correctly compute and compare return types |
| 8 | D06 | Tick Data Aggregation | Medium | D | D11,D03 | Resample tick stream to OHLCV bars |
| 9 | D09 | Feature Engineering Pipeline | Medium | D | D05,D06 | Build features from OHLCV, check multicollinearity, prevent look-ahead |
| 10 | D07 | Broken Data Feed Diagnosis | Hard | D | D02,D04,D06 | Diagnose multiple simultaneous data quality issues |
| 11 | D08 | Alternative Data Integration | Hard | D | D09 | Align irregular sentiment data, test information content via IC |

**Why first:** Every downstream task depends on data ingress and quality. The sequence starts with isolated ingestion (historical, then realtime), then moves into validation/cleaning, then feature construction and alternative-data integration.
For D10/D11, evaluation is hybrid: tutoring quality remains primary, but at least one runnable Python execution path is also scored from tool logs.

---

## Tier 2: BACKTEST & IMPLEMENTATION INFRASTRUCTURE
> *"Build the lab before running experiments." The tools and systems that enable research.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 12 | I01 | SMA Computation Implementation | Easy | I | D01 | Implement a basic technical indicator correctly |
| 13 | I02 | EMA Implementation | Easy | I | I01 | Exponential weighted moving average |
| 14 | I03 | Bollinger Bands Implementation | Easy | I | I01 | Multi-component indicator (mean ± std bands) |
| 15 | I10 | Backtest API Integration Tutorial | Medium | I | I01,D01 | Chain fetch→compute→backtest→analyze workflow |
| 16 | I04 | Signal Generation from Indicators | Medium | I | I01-I03 | Convert indicators to buy/sell signals |
| 17 | I05 | Position Sizing Logic | Medium | I | I04 | Implement fixed/risk-based position sizing |
| 18 | I06 | Vectorized vs. Loop-Based Backtest | Medium | I | I04 | Performance optimization of backtest engine |
| 19 | I07 | Transaction Cost Model | Medium | I | I04 | Fixed + proportional + market impact costs |
| 20 | X01 | Off-by-One Bug in Signal | Easy | X | I04 | Classic timing bug in signal generation |
| 21 | X02 | Look-Ahead Bias in Moving Average | Medium | X | I01 | Future data leaking into indicator calculation |
| 22 | X03 | Incorrect Return Calculation Bug | Medium | X | D05 | Arithmetic vs. log return confusion |
| 23 | X04 | Timezone Bug in Data Alignment | Medium | X | D02 | UTC vs. exchange time mismatch |
| 24 | B01 | Interpret Backtest Metrics | Easy | B | I04 | Read Sharpe, drawdown, win rate correctly |
| 25 | B02 | Detect Overfitting in Single Backtest | Medium | B | B01 | Recognize overfit from equity curve/metrics |

**Why second:** You need the implementation tools (indicators, signal generation, backtest engine) to be working correctly before you can do any meaningful research. Debugging infrastructure bugs also lives here.

---

## Tier 3: BASIC STRATEGY & RESEARCH METHODOLOGY
> *"Learn the grammar before writing poetry." Foundational strategy concepts and the correct research process.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 26 | S01 | MA Crossover Strategy | Easy | S | I01,B01 | Simplest complete strategy: signal → backtest → interpret |
| 27 | S02 | RSI Mean Reversion Strategy | Easy | S | I03,B01 | Contrarian strategy concept |
| 28 | S03 | Momentum Strategy (Price-Based) | Medium | S | D05,B01 | Trend-following strategy concept |
| 29 | S04 | Bollinger Band Breakout Strategy | Medium | S | I03,B01 | Volatility-based strategy |
| 30 | S05 | Pairs Trading Introduction | Medium | S | D05 | Co-movement and spread trading |
| 31 | S06 | Multi-Factor Model Strategy | Medium | S | D09 | Combining multiple signals |
| 32 | S07 | Mean Reversion vs Momentum | Medium | S | S02,S03 | When to use which approach |
| 33 | S11 | Strategy Development Protocol | Medium | S | S01-S03 | Formal pipeline: hypothesis → signal → split → backtest → validate |
| 34 | R01 | P-value Literacy | Medium | R | B01 | Why p<0.05 alone is insufficient |
| 35 | R06 | Research Replication | Medium | R | B01,R01 | Replicate a published result, discover it fails |
| 36 | A09 | Challenging Authority | Medium | A | R01 | Authority ≠ evidence; test claims empirically |

**Why third:** Once you have data and tools, you learn the basic strategy types (momentum, mean reversion, multi-factor). Critically, you also learn the *correct research process* — hypothesis before data, proper validation, p-value literacy. R01 and S11 belong here because they set the foundation for doing research right.

---

## Tier 4: BACKTEST VALIDATION & RESULT ANALYSIS
> *"Trust but verify." Every backtest is guilty until proven innocent.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 37 | B03 | Drawdown Analysis & Recovery | Medium | B | B01 | Compute and interpret drawdown time series |
| 38 | B04 | Rolling Performance Metrics | Medium | B | B01 | Windowed Sharpe, volatility, beta over time |
| 39 | B05 | Factor Decomposition of Returns | Medium | B | B01,S06 | Attribute returns to factor exposures |
| 40 | B06 | Transaction Cost Sensitivity | Medium | B | I07,B01 | Vary costs 0-50bps, find breakeven |
| 41 | B09 | Backtest Sanity Check Protocol | Medium | B | B01-B04 | Systematic checklist: year decomp, concentration, long/short legs |
| 42 | E07 | Morning P&L Review | Medium | E | B03,B04 | Systematic daily review: P&L, anomalies, benchmark comparison |
| 43 | E09 | Translating Quant Uncertainty for Stakeholders | Medium | E | B01,B06 | Explain confidence, regime risk, cost uncertainty to non-quants |
| 44 | B07 | Multiple Hypothesis Correction in Backtesting | Hard | B | B01,R01 | Deflated Sharpe ratio from 100 backtest variants |
| 45 | B08 | Regime-Conditional Backtest Analysis | Hard | B | B04 | Decompose performance by bull/bear, high/low vol regimes |
| 46 | B10 | Leakage Detection in Research Report | Hard | B | B01 | Find 3 leakage types hidden in prose methodology |

**Why fourth:** After building basic strategies, you need to learn to *question* your results. This tier is about developing a skeptical eye: sanity checks, cost sensitivity, regime decomposition, and leakage detection. The difference between a junior and senior quant is how thoroughly they tear apart their own results.

---

## Tier 5: RESEARCH RIGOR & STATISTICAL VALIDATION
> *"The most important skill is knowing when your results are fake." Deep statistical methodology.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 47 | R02 | Time-Series Cross-Validation | Hard | R | B01,S11 | Walk-forward CV; why random split = look-ahead bias |
| 48 | R03 | Parameter Sensitivity Analysis | Hard | R | S02,B01 | Vary params ±20%, find performance cliff = overfitting |
| 49 | R04 | Multiple Hypothesis Correction | Hard | R | R01,B07 | Bonferroni/BH on 50 strategies; 2.5 expected by chance |
| 50 | R05 | Structural Break Detection | Hard | R | B04,B08 | Chow/CUSUM/Bai-Perron tests, regime change framework |
| 51 | I08 | Walk-Forward Optimization | Hard | I | R02 | Implement rolling IS optimization + OOS testing |
| 52 | S08 | Alpha Hypothesis Testing | Hard | S | S11,R01 | Formalize hypothesis, construct signal, OOS test with costs |

**Why fifth:** This is the "graduate-level" research methodology. You already know how to build strategies and check backtests. Now you learn the hard statistical discipline: proper cross-validation for time series, parameter sensitivity, multiple testing correction, and structural break detection. These skills separate real alpha from noise.

---

## Tier 6: DEBUGGING METHODOLOGICAL & DATA BIASES
> *"The worst bugs aren't in the code — they're in the assumptions." Biases that make correct code produce wrong answers.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 53 | X05 | Rebalance Date Off-by-One | Medium | X | I04 | Subtle timing error in portfolio rebalancing |
| 54 | X06 | Vectorization Error | Medium | X | I06 | Incorrect vectorized operation vs loop result |
| 55 | X09 | Selection Bias in Feature Engineering | Medium | X | D09,R01 | Circular reasoning: selecting features by return correlation |
| 56 | X07 | Survivorship Bias Bug | Hard | X | D01 | Backtesting on today's survivors only |
| 57 | X11 | Universe Selection Leakage | Hard | X | X07 | Today's top-200 market cap used for 5-year backtest |
| 58 | X08 | Non-Stationarity Bug | Hard | X | R05 | Model assumes constant mean/variance — it doesn't hold |
| 59 | X10 | Feature Construction Leakage Audit | Hard | X | D09,X09 | 3 leakage types: full-sample z-score, centered window, contemporaneous close |

**Why sixth:** These are the insidious bugs that don't show up as code errors. The code runs fine, the backtest looks great — but the result is meaningless because of survivorship bias, universe selection leakage, or stationarity assumptions. You need Tiers 1-5 knowledge to even recognize these problems.

---

## Tier 7: FACTOR ANALYSIS (Specialized Research Track)
> *"The bread and butter of systematic equity investing." Dedicated factor evaluation lifecycle.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 60 | F01 | Factor Data Prep & Return Alignment | Medium | F | D09 | Factor at t predicts return t→t+1, not contemporaneous |
| 61 | F02 | Single Factor IC Analysis | Medium | F | F01 | Rank IC, IC time series, calibrate expectations (|IC|>0.05 = strong) |
| 62 | F03 | Factor Decay Analysis | Hard | F | F02 | IC at 1/3/6/12 month horizons, optimal holding period |
| 63 | F04 | Multi-Factor Combination | Hard | F | F02 | Equal-weight z-score vs IC-weighted vs orthogonalized |
| 64 | F05 | Factor Portfolio Construction | Hard | F | F04,I09 | Long-short quintile portfolio, sector neutralization |
| 65 | F06 | Factor Data Mining Trap | Hard | F | F02,R04 | 1/200 factors "passed" — but it's a false discovery |
| 66 | I09 | Cross-Sectional Factor Model | Hard | I | D09,S06 | Multi-stock factor regression, neutralization, portfolio formation |

**Why seventh:** Factor analysis is a specialized discipline that builds on everything before it: clean data (Tier 1), proper backtesting (Tier 2-4), statistical rigor (Tier 5), and bias awareness (Tier 6). It has its own lifecycle (IC → decay → combination → portfolio) that mirrors the general research workflow but with specific concepts.

---

## Tier 8: PORTFOLIO & RISK MANAGEMENT
> *"Individual strategies are half the job." How strategies combine, how risk propagates, how to manage drawdowns.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 67 | P01 | Portfolio Return Attribution | Easy | P | B01 | Which strategy drove this month's P&L? |
| 68 | P02 | VaR and CVaR Computation | Medium | P | D05 | Parametric, historical, Monte Carlo VaR; CVaR superiority |
| 69 | P06 | Drawdown Management Protocol | Medium | P | B03,P01 | Systematic framework for 15% drawdown: normal variance or regime change? |
| 70 | S09 | Strategy Capacity Analysis | Medium | S | I07 | ADV, square-root market impact, capacity-return decay |
| 71 | P03 | Correlation Regime Analysis | Hard | P | P02,R05 | Correlation instability: diversification fails in crises |
| 72 | P04 | Mean-Variance Optimization Pitfalls | Hard | P | P02 | MVO = error maximizer; shrinkage, constraints, Black-Litterman |
| 73 | P05 | Multi-Strategy Allocation | Hard | P | P04,S06 | Equal weight vs risk parity vs optimized across 5 strategies |

**Why eighth:** Portfolio thinking comes after you can build and validate individual strategies. You need to understand factor decomposition (Tier 4), correlation instability (Tier 5-6), and strategy-level performance (Tier 3) before combining strategies into a portfolio and managing aggregate risk.

---

## Tier 9: PRODUCTION, MONITORING & STRATEGY LIFECYCLE
> *"The strategy doesn't end at the backtest." What happens after deployment.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 74 | S10 | Strategy Decay Diagnosis | Hard | S | R05,B04 | Alpha decay vs regime change vs crowding vs data issue |

**Why ninth:** Only makes sense after you have live strategies (or simulated live strategies). Diagnosing decay requires every prior skill: data integrity (Tier 1), performance metrics (Tier 4), structural breaks (Tier 5), and bias awareness (Tier 6).

---

## Tier 10: END-TO-END INTEGRATION
> *"Put it all together." Multi-stage workflows that test coherence across the entire pipeline.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 75 | E01 | Basic End-to-End Strategy | Medium | E | Tier 1-3 | Data → strategy → backtest → interpret (basic) |
| 76 | E02 | End-to-End with Cost Analysis | Medium | E | E01,I07 | Add transaction cost layer |
| 77 | E03 | End-to-End with Validation | Medium | E | E01,R02 | Add OOS validation step |
| 78 | E04 | End-to-End Multi-Asset | Medium | E | E01,S05 | Extend to multiple assets |
| 79 | E05 | End-to-End with Risk Management | Medium | E | E01,P02 | Add risk management layer |
| 80 | E06 | Full Research Cycle | Hard | E | Tier 1-8 | Hypothesis → data → features → signal → backtest → validate → sensitivity → costs → assess |
| 81 | E08 | Anti-Leakage End-to-End Workflow | Hard | E | Tier 1-6 | 7 explicit leakage checkpoints from hypothesis to validation |

**Why tenth:** End-to-end tasks are integration tests. They combine skills from multiple tiers and test whether the agent can maintain coherence across 20+ turns. They should only be attempted after the component skills are solid.

---

## Tier 11: ADVERSARIAL & SAFETY
> *"Can you say no?" Resisting bad methodology, unrealistic claims, and social pressure.*

| Order | Task ID | Name | Difficulty | Category | Deps | Key Skill |
|:---:|:---|:---|:---:|:---|:---|:---|
| 82 | A01 | Investment Advice Refusal | Easy | A | — | Refuse to give personal investment advice |
| 83 | A02 | Risk Disclosure | Easy | A | — | Always disclose risks and limitations |
| 84 | A03 | Correcting Misconceptions | Medium | A | R01 | Politely correct common quant myths |
| 85 | A04 | Overconfidence Pushback | Medium | A | B01 | Push back when student overinterprets results |
| 86 | A05 | Scope Boundaries | Medium | A | — | Stay within quant tutoring scope |
| 87 | A06 | Harmful Strategy Rejection | Medium | A | — | Refuse to help with market manipulation |
| 88 | A07 | Data Mining Fallacy Defense | Hard | A | R04,B07 | "I tested 1,000 combos and found Sharpe 3.0!" → explain data mining |
| 89 | A08 | Too-Good-to-Be-True Detection | Hard | A | B01 | Sharpe 5.0, 0 losing months → flag as bug, not celebrate |

**Why last:** Adversarial tasks are meta-skills. They test the agent's judgment, intellectual honesty, and calibration — qualities that depend on deep domain knowledge from all previous tiers. You can't explain *why* Sharpe 5.0 is suspicious if you don't know what realistic performance looks like.

---

## Summary: Implementation Roadmap

| Tier | Theme | Tasks | Difficulty Range | Estimated Count |
|:---:|:---|:---|:---:|:---:|
| 1 | Data Foundation | D10-D11, D01-D09 | Easy → Hard | 11 |
| 2 | Backtest & Impl Infrastructure | I01-I07, I10, X01-X04, B01-B02 | Easy → Medium | 14 |
| 3 | Basic Strategy & Research Method | S01-S07, S11, R01, R06, A09 | Easy → Medium | 11 |
| 4 | Backtest Validation & Analysis | B03-B10, E07, E09 | Medium → Hard | 10 |
| 5 | Research Rigor & Stats | R02-R05, I08, S08 | Hard | 6 |
| 6 | Debugging Biases | X05-X11 | Medium → Hard | 7 |
| 7 | Factor Analysis | F01-F06, I09 | Medium → Hard | 7 |
| 8 | Portfolio & Risk | P01-P06, S09 | Easy → Hard | 7 |
| 9 | Production & Monitoring | S10 | Hard | 1 |
| 10 | End-to-End Integration | E01-E06, E08 | Medium → Hard | 7 |
| 11 | Adversarial & Safety | A01-A08 | Easy → Hard | 8 |
| | **TOTAL** | | | **89** |

### Suggested Implementation Order Within Each Tier
Within each tier, always go **Easy → Medium → Hard**. Each task should ideally have its prerequisites completed first (see the Deps column), but within the same difficulty level, tasks can be implemented in parallel.

### Cross-Cutting Dependencies (Critical Path)
```
D10 → D01 → I01 → S01 → B01 → R01 → R04 → A07
  ↓
D11 → D06
  ↓      ↓                  ↓
D05 → D09 → F01 → F02 → F03-F06
              ↓
           X09 → X10
  ↓
D02 → D07
  ↓
B03 → B04 → R05 → X08
              ↓
           B08 → P03
                   ↓
I07 → B06 → S09    P04 → P05

S11 → S08 → E06
       ↓
    E08 (anti-leakage)
```

The **critical path** for earliest useful coverage is:
**D10 → D01 → I01 → I10 → S01 → B01 → R01 → S11 → B09 → E01**

This gives you a complete "beginner quant can do basic research correctly" path with just 10 tasks.
