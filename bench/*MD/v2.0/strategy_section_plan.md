# Strategy Section (S-Series) Design Plan

> Version: v2.0 | Status: Draft | Section: Alpha Research

---

## 1. Section Philosophy

### 1.1 What S-Series Tests

S-series tests the agent's ability to act as a **quant researcher** — guiding a student through the systematic process of discovering, formalizing, and evaluating trading alpha from raw market data.

This is the **intellectual core** of the quant workflow. In a production quant firm:

```
D-series (Data)          S-series (Alpha Research)             B-series (Backtest Engineering)
───────────────          ────────────────────────              ─────────────────────────────────
Get and understand  →    Given clean data,                →    Given validated alpha idea,
the data                 discover and formalize alpha           build the system to validate it
                                                               under realistic conditions

                         ┌──────────────────────────┐
                         │   Quant Researcher's Job   │
                         │                            │
                         │  1. Explore data            │
                         │  2. Spot patterns           │
                         │  3. Form hypothesis         │
                         │  4. Formalize signal        │
                         │  5. Evaluate signal quality  │
                         │  6. Rough PnL check          │
                         │  7. Assess robustness        │
                         │                            │
                         │  Output: validated alpha    │
                         │  idea ready for B-series    │
                         └──────────────────────────┘
```

**The core evaluation question**: Given raw market data and a broad research direction, can the agent guide the student through a systematic alpha research process — from exploratory analysis to a formalized, evaluated signal — rather than just handing them a cookbook strategy?

### 1.2 What Separates S-Series from Other Sections

| Section | Focus | How S-series differs |
|---------|-------|---------------------|
| **D-series** | Data loading, cleaning, exploration | D-series treats data as the end goal. S-series treats data as the starting material for alpha discovery. |
| **I-series** | Implementing specific computations | I-series says "implement RSI." S-series says "here's data — find a signal." The agent must decide which tools and indicators to explore. |
| **B-series** | Backtest engine engineering | B-series builds the validation infrastructure. S-series produces the alpha idea that B-series validates. |
| **E-series** | End-to-end system | E-series compresses the full pipeline. S-series goes deep on the research methodology. |

### 1.3 S-Series Is NOT "Implement Known Strategy X"

The existing S01 (MA crossover) is prescriptive — it tells the student exactly what strategy to build. Under the new framing, S-series tasks should be **research-oriented**:

```
OLD framing (prescriptive):
  "Guide the student to implement a moving average crossover strategy."
  → The agent just codes a known recipe.

NEW framing (research-oriented):
  "Given BTCUSDT daily data, guide the student to explore trend-following
   patterns and develop a signal."
  → The agent must explore the data, identify what works, formalize it,
    and evaluate whether it's real.
```

The task provides **data + a broad research direction** (trend-following, mean-reversion, volume-based, cross-asset). The agent must guide the research process, not just execute a recipe.

**Note on S01**: S01 remains as-is for backward compatibility. It serves as an easy entry point. The new S02–S06 tasks follow the research-oriented framing.

### 1.4 S-Series Includes Rough Signal Testing

S-series is NOT purely statistical analysis with zero PnL computation. A quant researcher absolutely does rough signal testing as part of the research loop:

```
Research loop (within S-series):
  explore → hypothesize → build signal → evaluate signal quality (IC, decay, turnover)
                                              ↓
                                    rough PnL check (signal × returns → Sharpe)
                                              ↓
                                    "Is this worth pursuing?"
                                         yes → iterate / refine → hand off to B-series
                                         no  → discard / try another hypothesis
```

The distinction from B-series:

| Aspect | S-series (rough test) | B-series (rigorous validation) |
|--------|----------------------|-------------------------------|
| Method | Vectorized: `signal.shift(1) * returns` | Proper engine with bar-by-bar replay |
| Look-ahead | Researcher is careful (shift) but no formal proof | Architecturally enforced, provably correct |
| Execution costs | Ignored or trivially estimated | Fully modeled (slippage, fees, funding) |
| Position tracking | None — just directional returns | Full accounting (margin, PnL, trade log) |
| Purpose | "Does this signal have any merit?" | "What does this actually return in production?" |

### 1.5 Why Crypto Futures Data (Shared with B-Series)

S-series and B-series use the **same Binance USDT-M Futures kline data**, forming a continuous pipeline:
- S-series: research alpha on BTCUSDT/ETHUSDT data
- B-series: build the engine to rigorously validate that alpha

See B-series plan §1.3 for full rationale (freely available, 24/7 market, rich data structure, multi-timeframe).

---

## 2. New Tool: `evaluate_signal`

### 2.1 Rationale

The `evaluate_signal` tool is to S-series what `run_backtest` is to B-series — the domain-specific convenient tool that encapsulates the core workflow.

Every day, a quant researcher computes the same signal quality metrics. Without this tool, the student writes 40+ lines of pandas/scipy code each time. With it, the researcher can iterate fast.

Per v2.0 tool philosophy: this is a convenient tool — fully replaceable by `shell_exec` + pandas/scipy, but encapsulates the domain-idiomatic workflow. Using it earns a bonus; not using it is fine.

### 2.2 Tool Specification

```python
"evaluate_signal": {
    "function": evaluate_signal_fn,
    "description": "Evaluate the quality of a trading signal against forward returns. "
                   "Computes Information Coefficient (IC), IC decay, quantile returns, "
                   "turnover, hit rate, and rough PnL metrics. "
                   "Input: a CSV with at least a 'signal' column and a 'close' column.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to CSV containing 'signal' and 'close' columns. "
                               "Optionally 'returns' column; if absent, computed from close."
            },
            "forward_periods": {
                "type": "integer",
                "description": "Number of periods ahead for forward returns (default: 1)",
                "default": 1
            },
            "quantiles": {
                "type": "integer",
                "description": "Number of quantile buckets for quantile analysis (default: 5)",
                "default": 5
            },
            "decay_lags": {
                "type": "integer",
                "description": "Number of lags for IC decay analysis (default: 5)",
                "default": 5
            }
        },
        "required": ["file_path"]
    }
}
```

### 2.3 Output Format

```json
{
    "signal_metrics": {
        "ic_mean": 0.032,
        "ic_std": 0.148,
        "ic_ir": 0.216,
        "ic_tstat": 2.84,
        "ic_pvalue": 0.0046,
        "ic_decay": [0.032, 0.018, 0.009, 0.003, -0.001],
        "hit_rate": 0.534,
        "turnover": 0.342,
        "signal_autocorrelation": 0.87
    },
    "quantile_analysis": {
        "quantile_mean_returns": [0.00012, 0.00034, 0.00051, 0.00078, 0.00124],
        "long_short_spread": 0.00112,
        "monotonicity_score": 0.9
    },
    "rough_pnl": {
        "total_return": 0.453,
        "annualized_return": 0.148,
        "annualized_sharpe": 1.21,
        "max_drawdown": -0.187,
        "num_observations": 1460
    },
    "diagnostics": {
        "signal_coverage": 0.95,
        "signal_mean": 0.002,
        "signal_std": 1.034,
        "forward_return_mean": 0.00045,
        "correlation_signal_abs_return": 0.12
    }
}
```

### 2.4 Implementation Notes

The tool internally:
1. Loads the CSV, computes forward returns if not provided
2. Computes rank IC (Spearman correlation between signal and forward returns) per period, then mean/std/IR
3. Computes IC at multiple lags (decay analysis)
4. Sorts by signal into quantile buckets, computes mean return per bucket
5. Computes turnover as `mean(|signal_t - signal_{t-1}|)`
6. Computes rough PnL as `cumsum(signal.shift(1) * returns)` → Sharpe, drawdown
7. Returns all metrics as a structured dict

### 2.5 Distractor Tool Considerations for S-Series

Tools that the researcher should NOT use during research (premature):

| Tool | Why it's a distractor in S-series context |
|------|------------------------------------------|
| `deploy_trading_bot` | Way too early — no validated alpha yet |
| `optimize_portfolio` | Portfolio construction before having a single alpha is premature |
| `compute_greeks` | Options-specific, irrelevant to signal research on futures |

Note: `run_backtest` is NOT a distractor for S-series. A researcher doing a quick backtest as part of signal evaluation is legitimate behavior. However, spending most of the conversation building a full backtest engine (B-series work) instead of doing research would be caught by `process_reasonableness`.

---

## 3. Sandbox Environment

### 3.1 Required Python Libraries

The sandbox (`quant-tutor-env:v2.0`) must have these libraries installed for S-series research tasks:

| Library | Version | Research Use |
|---------|---------|-------------|
| `pandas` | ≥2.0 | Core data manipulation |
| `numpy` | ≥1.24 | Numerical computation |
| `scipy` | ≥1.10 | Statistical tests, rank correlation |
| `statsmodels` | ≥0.14 | ADF, cointegration, regression, GARCH |
| `matplotlib` | ≥3.7 | Plotting |
| `seaborn` | ≥0.12 | Statistical visualization (heatmaps, distributions) |
| `scikit-learn` | ≥1.3 | PCA, clustering, basic ML for feature analysis |
| `pandas_ta` | ≥0.3 | Technical indicator library (broader than `compute_indicator`) |
| `arch` | ≥6.0 | GARCH modeling, volatility analysis |

**Note**: These are sandbox-installed libraries, not tools. The agent accesses them through `shell_exec` (writing Python scripts). They enable the research workflow without requiring new tools.

### 3.2 Core MCP Tools for S-Series

All S-tasks use:
```json
"core_mcp_tools": [
    "shell_exec",
    "file_write",
    "file_read",
    "file_list",
    "get_environment_info",
    "search_docs"
]
```

`search_docs` is included because S-tasks have reference docs on research methodology.

---

## 4. Reference Documentation

### 4.1 New Doc: `alpha_research_methodology.md`

The core methodology doc for S-series — equivalent to `backtesting_101.md` for B-series.

```markdown
# Alpha Research Methodology: A Systematic Guide

## 1. What Is Alpha?
- Alpha = return not explained by market exposure (beta)
- Alpha sources: behavioral biases, structural inefficiencies, information advantages
- Alpha decay: signals lose power over time as markets adapt

## 2. The Research Process
- Step 1: Exploratory Data Analysis — understand the data before forming hypotheses
- Step 2: Hypothesis Formation — "I believe X because of Y" (must have a reason)
- Step 3: Signal Construction — turn the hypothesis into a computable signal
- Step 4: Signal Evaluation — IC, decay, turnover, quantile analysis
- Step 5: Rough PnL Check — does the signal generate positive returns?
- Step 6: Robustness Assessment — stability across time, parameter sensitivity
- Step 7: Documentation — record the hypothesis, signal definition, and results

## 3. Exploratory Data Analysis for Alpha
- Return distribution analysis (skewness, kurtosis, tail behavior)
- Autocorrelation analysis (predictability in returns)
- Volatility patterns (clustering, mean-reversion)
- Volume patterns (volume-price relationships)
- Cross-asset relationships (correlation, lead-lag)

## 4. Hypothesis-Driven Research
- Why hypotheses matter: prevents data mining
- Good hypothesis: "Momentum exists because investors underreact to information"
- Bad hypothesis: "I found a pattern in the data" (no causal reasoning)
- The p-hacking problem: testing 100 signals guarantees false positives

## 5. Signal Construction Best Practices
- Signals should be simple (few parameters)
- Signals should be robust (small parameter changes shouldn't destroy performance)
- Signals should be explainable (you can articulate why it works)
- Normalize signals (z-score or rank) for comparability

## 6. Signal Evaluation Metrics
### Information Coefficient (IC)
- Rank correlation between signal and forward returns
- IC > 0.02 is meaningful in practice
- IC IR = mean(IC) / std(IC) — consistency matters more than magnitude

### IC Decay
- IC computed at increasing forward lags
- Fast decay → short-horizon signal (high turnover)
- Slow decay → long-horizon signal (low turnover)

### Quantile Analysis
- Sort universe by signal, compute mean return per quantile
- Monotonic spread = good signal (top quantile beats bottom consistently)
- Non-monotonic = signal may only work at extremes

### Turnover
- How often the signal changes
- High turnover × low IC = unprofitable after costs
- Turnover must be considered alongside IC

## 7. Common Pitfalls
- Data mining / p-hacking: testing too many signals without correction
- Overfitting: signal works on historical data but fails out-of-sample
- Survivorship bias: only testing on assets that still exist
- Look-ahead bias: using information not available at signal time
- Regime dependence: signal works in bull markets but fails in bear markets
```

### 4.2 New Doc: `signal_evaluation.md`

Detailed reference on signal quality metrics with formulas and Python code.

```markdown
# Signal Evaluation: Metrics and Methods

## 1. Information Coefficient (IC)
### Formula
IC_t = spearman_rank_correlation(signal_t, forward_return_{t+1})
IC_mean = mean(IC_t) across all periods
IC_IR = IC_mean / std(IC_t)

### Python Implementation
from scipy.stats import spearmanr
ic_series = [spearmanr(signal[t], returns[t+1])[0] for t in range(len(signal)-1)]

### Interpretation
| IC_mean | Quality |
|---------|---------|
| > 0.05  | Strong  |
| 0.02-0.05 | Moderate |
| 0.01-0.02 | Weak but potentially useful |
| < 0.01 | Noise |

## 2. IC Decay Analysis
...

## 3. Quantile Return Analysis
...

## 4. Turnover Analysis
...

## 5. Rough PnL Estimation
...
```

### 4.3 Existing Docs (Reusable)

- `statistical_tests.md` — ADF, cointegration, correlation. Used by S05, S06.
- `risk_metrics.md` — Sharpe, drawdown, Sortino. Used for rough PnL interpretation.
- `moving_averages.md` — SMA/EMA computation. Used by S02, S03.

---

## 5. Data Preparation

### 5.1 Shared Data with B-Series

S-series and B-series share the same frozen Binance futures datasets. This is intentional — the two sections form a continuous pipeline (research alpha on the data → build engine to validate it).

See B-series plan §2 for full data preparation details.

### 5.2 Data Files Used by S-Series

| File | Used By | Research Purpose |
|------|---------|-----------------|
| `BTCUSDT_1d_2021_2024.csv` | S02, S03, S04, S06 | Core single-asset research (daily) |
| `ETHUSDT_1d_2021_2024.csv` | S05, S06 | Cross-asset research |
| `BTCUSDT_1h_2023_2024.csv` | S04 | Intraday pattern research |
| `BTCUSDT_5m_2024Q4.csv` | S04 | High-frequency microstructure research |
| `ETHUSDT_1h_2023_2024.csv` | S05 | Cross-asset intraday |
| `BTCUSDT_funding_2021_2024.csv` | S06 | Alternative signal source |

---

## 6. Task Designs

### 6.0 Existing Task: S01 — MA Crossover Strategy

**Status**: Exists, keep as-is. S01 is prescriptive ("implement an MA crossover") and uses stock data (AAPL/SPY). It serves as the easy entry point to the section. The new S02–S06 tasks follow the research-oriented framing with crypto futures data.

---

### 6.1 S02 — Trend-Following Alpha Research

**Difficulty**: medium
**Category**: strategy

**Core idea**: Given BTCUSDT daily data, guide the student to explore trend-following patterns, form a hypothesis about why trends persist in crypto, construct a trend signal, and evaluate whether it contains genuine predictive power.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`
- Research direction (in task description): "Explore whether trend-following signals have predictive power on BTC daily data."

**Description**: Guide a student to conduct systematic alpha research on trend-following patterns in BTCUSDT daily data. The student should explore the data to identify trend characteristics, form a hypothesis about why trend-following might work in crypto markets, construct one or more trend signals (e.g., moving average slope, breakout, time-series momentum), evaluate signal quality using IC and quantile analysis, perform a rough PnL check, and assess robustness across different time periods.

**Expected outcome**: Student produces a research output containing: (1) exploratory analysis showing trend characteristics in BTC data (autocorrelation, regime identification, return persistence), (2) a clearly stated hypothesis for why trends exist in this market, (3) at least one formalized trend signal with a precise mathematical/code definition, (4) signal evaluation metrics (IC, IC decay, quantile spread, turnover), (5) rough PnL results (directional return, Sharpe), and (6) a robustness note — does the signal work across the full period or only in specific regimes?

**Required capabilities**:
1. Perform exploratory analysis to identify trend characteristics in price data (autocorrelation, return persistence, regime structure)
2. Form a testable hypothesis about why trend-following should work in this market
3. Construct a trend-following signal with a clear definition (formula or code)
4. Evaluate signal quality using IC, IC decay, quantile analysis, and turnover
5. Perform a rough PnL check (signal × returns → Sharpe) and interpret the results
6. Assess robustness: does the signal degrade in specific time periods or market regimes?

**Student openings**:
- **beginner_no_finance**: "I have a year of Bitcoin price data. People say 'the trend is your friend' — is that actually true? How would I even check if trends exist in this data?"
- **intermediate_developer**: "I want to research trend-following signals on BTC daily data. I can code in Python, but I've never done systematic signal research before. What's the right process?"
- **advanced_quant**: "I'm researching trend-following alpha in crypto. I want to go beyond simple MA crossovers — autocorrelation structure, return persistence, and a properly evaluated signal with IC analysis. Where should I start with this dataset?"

**Environment**:
```json
{
    "data_files": ["BTCUSDT_1d_2021_2024.csv"],
    "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
    "docs_available": ["alpha_research_methodology.md", "signal_evaluation.md", "moving_averages.md"],
    "network_enabled": false
}
```

**Convenient tools**: `compute_indicator`, `compute_statistics`, `evaluate_signal`, `plot_chart`

**Eval strategy**:
- **Exploratory analysis performed**: Check workspace/tool logs for descriptive statistics, autocorrelation, or distribution analysis on the raw data.
- **Hypothesis stated**: Conversation contains an explicit reason for why trends should exist (not just "I tried MA and it works").
- **Signal formalized**: Workspace contains a signal definition (code that produces a signal column) — not just an ad-hoc observation.
- **Signal evaluated**: Check for IC computation, quantile analysis, or `evaluate_signal` tool usage. The student should have quantitative evidence of signal quality.
- **Rough PnL**: Workspace or tool output contains return/Sharpe estimate.

---

### 6.2 S03 — Mean-Reversion Alpha Research

**Difficulty**: medium
**Category**: strategy

**Core idea**: Given BTCUSDT daily data, guide the student to explore mean-reversion patterns, understand when mean-reversion works (and fails), construct a reversion signal, and evaluate it.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`
- Research direction: "Explore whether short-term mean-reversion signals have predictive power on BTC daily data."

**Description**: Guide a student to research mean-reversion alpha in BTCUSDT daily data. The student should explore the data for reversion characteristics (negative autocorrelation at certain lags, overextension patterns), form a hypothesis about what drives reversion in crypto, construct a mean-reversion signal (e.g., z-score of price deviation from a rolling mean, RSI extremes, Bollinger Band deviation), evaluate signal quality, and critically assess when mean-reversion fails (trending markets).

**Expected outcome**: Student produces a research output containing: (1) exploratory analysis showing reversion characteristics (lag-specific autocorrelation, volatility patterns, overextension statistics), (2) a hypothesis for why reversion occurs (liquidity provision, overreaction, technical levels), (3) a formalized mean-reversion signal, (4) signal evaluation metrics (IC, quantile spread, turnover — note: mean-reversion signals typically have higher turnover than trend signals), (5) rough PnL, and (6) critical analysis of when the signal fails — specifically, does it blow up in strong trends?

**Required capabilities**:
1. Identify mean-reversion characteristics in the data (negative autocorrelation at specific lags, overextension frequencies)
2. Form a hypothesis about what drives mean-reversion in this market
3. Construct a mean-reversion signal (z-score, RSI deviation, Bollinger deviation, or similar)
4. Evaluate signal quality with awareness that reversion signals behave differently from trend signals (higher turnover, different IC profile)
5. Perform rough PnL check and interpret results
6. Critically identify the failure mode: what happens during strong trends? (this is the key insight — mean-reversion strategies have fat-tail risk)

**Student openings**:
- **beginner_no_finance**: "I noticed that Bitcoin sometimes drops a lot and then bounces back. Is there a way to trade that 'bounce back' pattern? How do I know if it's real?"
- **intermediate_developer**: "I want to research mean-reversion signals on BTC. I know about z-scores and Bollinger Bands, but I want to systematically evaluate whether reversion is actually present in this data before building a strategy."
- **advanced_quant**: "I'm researching short-term mean-reversion alpha in crypto. I want to analyze the autocorrelation structure, identify the optimal reversion horizon, construct a signal, and stress-test it against trending regimes. What's the approach?"

**Environment**:
```json
{
    "data_files": ["BTCUSDT_1d_2021_2024.csv"],
    "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
    "docs_available": ["alpha_research_methodology.md", "signal_evaluation.md", "statistical_tests.md"],
    "network_enabled": false
}
```

**Convenient tools**: `compute_indicator`, `compute_statistics`, `evaluate_signal`, `plot_chart`

**Eval strategy**:
- Same framework as S02, plus:
- **Failure mode analysis**: The student explicitly addresses what happens in trending markets. A strong response identifies the fat-tail risk of mean-reversion. Missing this is a significant gap.
- **Regime awareness**: Conversation or analysis shows awareness that mean-reversion signal quality is regime-dependent.

---

### 6.3 S04 — Volume/Microstructure Alpha Research

**Difficulty**: hard
**Category**: strategy

**Core idea**: Given BTCUSDT data at multiple timeframes (daily, hourly, 5-minute), guide the student to explore volume-based and microstructure patterns and develop a signal that uses non-price information.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`, `BTCUSDT_1h_2023_2024.csv`, `BTCUSDT_5m_2024Q4.csv`
- Research direction: "Explore whether volume patterns, trade flow imbalances, or other microstructure features have predictive power for BTC price movements."

**Description**: Guide a student to research alpha signals derived from non-price data: volume patterns, taker buy/sell imbalance, trade count, and cross-timeframe features. The student should explore the rich Binance kline data (which includes quote volume, trade count, and taker buy volume beyond basic OHLCV), identify features that might predict future price movements, construct signals from these features, and evaluate their quality. This task is harder because it requires the student to go beyond standard technical analysis into microstructure territory.

**Expected outcome**: Student produces a research output containing: (1) exploration of non-price features in the data (volume profiles, taker imbalance patterns, trade count anomalies), (2) feature engineering — creating derived signals from raw microstructure data (e.g., taker buy ratio, volume z-score, abnormal trade count), (3) at least one formalized microstructure-based signal, (4) signal evaluation with IC and quantile analysis, (5) cross-timeframe analysis — does a signal computed on 5m data predict daily returns?, and (6) assessment of signal decay — microstructure signals typically decay fast.

**Required capabilities**:
1. Understand and explore non-price data fields (quote volume, taker buy volume, trade count) in Binance kline data
2. Engineer features from microstructure data (volume ratios, flow imbalance, normalized trade count)
3. Analyze cross-timeframe signal relationships (5m features → hourly or daily return prediction)
4. Construct a microstructure-based signal with clear definition
5. Evaluate signal quality with attention to decay characteristics (microstructure alpha decays fast)
6. Discuss practical considerations: can this signal be traded? (latency, capacity)

**Student openings**:
- **beginner_no_finance**: "I see that my Bitcoin data has not just prices but also volume, trade count, and something called 'taker buy volume.' What do these mean, and can they help predict price movements?"
- **intermediate_developer**: "I want to research volume-based trading signals on BTC. I have multi-timeframe data (daily, hourly, 5-minute) with detailed volume breakdowns. How should I approach feature engineering from this data?"
- **advanced_quant**: "I'm researching microstructure alpha in crypto futures using Binance kline data. I want to analyze taker flow imbalance, volume anomalies, and cross-timeframe signal propagation. I have 5m, 1h, and 1d data. What's the systematic approach?"

**Environment**:
```json
{
    "data_files": [
        "BTCUSDT_1d_2021_2024.csv",
        "BTCUSDT_1h_2023_2024.csv",
        "BTCUSDT_5m_2024Q4.csv"
    ],
    "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
    "docs_available": ["alpha_research_methodology.md", "signal_evaluation.md", "crypto_futures_basics.md"],
    "network_enabled": false
}
```

**Convenient tools**: `compute_statistics`, `evaluate_signal`, `plot_chart`

**Eval strategy**:
- **Non-price features used**: The signal is NOT purely price-based. Check that volume, trade count, or taker buy data was used.
- **Feature engineering**: Workspace contains derived features (ratios, z-scores, normalizations), not just raw columns.
- **Cross-timeframe analysis**: Evidence that the student examined relationships across timeframes (e.g., 5m volume spike → 1h return).
- **Decay awareness**: Conversation addresses that microstructure signals have fast decay.

---

### 6.4 S05 — Cross-Asset Alpha Research

**Difficulty**: hard
**Category**: strategy

**Core idea**: Given daily data for both BTCUSDT and ETHUSDT, guide the student to explore cross-asset relationships and develop a signal that exploits the lead-lag or relative-value dynamics between BTC and ETH.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`, `ETHUSDT_1d_2021_2024.csv`, `BTCUSDT_1h_2023_2024.csv`, `ETHUSDT_1h_2023_2024.csv`
- Research direction: "Explore cross-asset relationships between BTC and ETH — lead-lag dynamics, relative value, correlation regime changes — and develop a tradeable signal."

**Description**: Guide a student to research alpha from cross-asset dynamics between BTC and ETH. The student should explore the relationship (correlation, cointegration, lead-lag), identify periods where the relationship breaks down or provides a trading opportunity, construct a cross-asset signal (spread mean-reversion, lead-lag exploitation, correlation regime change), evaluate it, and understand the unique risks of cross-asset strategies (relationship breakdown, correlation regime changes).

**Expected outcome**: Student produces a research output containing: (1) cross-asset exploration (rolling correlation, cointegration test, lead-lag analysis at daily and hourly frequency), (2) identification of tradeable dynamics (e.g., "ETH tends to follow BTC with a lag" or "the BTC/ETH ratio mean-reverts"), (3) a formalized cross-asset signal, (4) signal evaluation, (5) rough PnL on a dollar-neutral strategy (long one, short the other), and (6) risk analysis — what happens when the BTC/ETH relationship breaks down (structural regime changes, ETH-specific events)?

**Required capabilities**:
1. Analyze cross-asset relationships (correlation, cointegration, lead-lag at multiple frequencies)
2. Identify specific tradeable dynamics between BTC and ETH
3. Construct a cross-asset signal (spread z-score, lead-lag predictor, or relative momentum)
4. Evaluate the signal on a dollar-neutral basis (long-short, not directional)
5. Understand the risks specific to cross-asset strategies (relationship breakdown, correlation instability)
6. Use multi-frequency data to strengthen the analysis (hourly lead-lag → daily signal)

**Student openings**:
- **beginner_no_finance**: "I have both Bitcoin and Ethereum data. I heard they're related — when Bitcoin goes up, Ethereum usually does too. Can I somehow trade that relationship?"
- **intermediate_developer**: "I want to research cross-asset signals between BTC and ETH. I have daily and hourly data for both. I'm thinking about correlation, cointegration, and lead-lag analysis. What's the right framework?"
- **advanced_quant**: "I'm researching relative-value alpha between BTC and ETH futures. I want to analyze the cointegration relationship, measure lead-lag dynamics at hourly frequency, and construct a dollar-neutral signal. I have multi-frequency data for both assets."

**Environment**:
```json
{
    "data_files": [
        "BTCUSDT_1d_2021_2024.csv",
        "ETHUSDT_1d_2021_2024.csv",
        "BTCUSDT_1h_2023_2024.csv",
        "ETHUSDT_1h_2023_2024.csv"
    ],
    "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
    "docs_available": ["alpha_research_methodology.md", "signal_evaluation.md", "statistical_tests.md"],
    "network_enabled": false
}
```

**Convenient tools**: `compute_statistics`, `evaluate_signal`, `plot_chart`

**Eval strategy**:
- **Cross-asset analysis**: Both assets are loaded and analyzed together (not independently).
- **Relationship tested**: Cointegration, correlation, or lead-lag analysis was performed.
- **Dollar-neutral**: Signal or PnL is computed on a long-short basis, not just directional BTC.
- **Relationship risk addressed**: Student discusses what happens when BTC/ETH relationship breaks down.

---

### 6.5 S06 — Multi-Signal Combination & Alpha Synthesis

**Difficulty**: hard
**Category**: strategy

**Core idea**: Given the full suite of data (BTCUSDT daily + ETHUSDT daily + funding rates), guide the student to combine multiple signal sources into a composite alpha and evaluate whether the combination is stronger than any individual signal.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`, `ETHUSDT_1d_2021_2024.csv`, `BTCUSDT_funding_2021_2024.csv`
- Research direction: "Combine multiple alpha sources — trend, reversion, cross-asset, and carry (funding rate) — into a composite signal and evaluate whether the combination adds value."

**Description**: Guide a student to synthesize multiple alpha sources into a single composite signal. The student should construct (or use previously researched) individual signals from different sources: a trend signal, a mean-reversion signal, a cross-asset signal (BTC/ETH), and a carry signal (funding rate). Then combine them using a principled method (equal weight, IC-weight, or regression), evaluate the composite signal, and demonstrate that diversification across signal types improves the risk-adjusted return compared to any single signal.

**Expected outcome**: Student produces a research output containing: (1) at least 3 individual signals from distinct alpha sources (trend, reversion, cross-asset, carry — at least 3 of 4), (2) correlation analysis between signals (low correlation = good diversification), (3) a composite signal constructed via a stated combination method, (4) evaluation of the composite vs individual signals (IC, Sharpe comparison), (5) demonstration that the composite has better IC IR or Sharpe than any single signal, and (6) discussion of when signal combination fails (all signals correlated in a crisis).

**Required capabilities**:
1. Construct multiple individual signals from distinct alpha sources
2. Analyze signal-to-signal correlations to understand diversification potential
3. Combine signals using a principled method (equal weight, IC-weighted, or regression-based)
4. Evaluate the composite signal and compare against individual signals
5. Demonstrate diversification benefit quantitatively (improved IC IR or Sharpe)
6. Discuss limitations: when does diversification fail? (correlated drawdowns, regime shifts)

**Student openings**:
- **beginner_no_finance**: "I've been learning about different trading signals — some follow trends, others bet on reversals. Can I combine them to get something better than either one alone?"
- **intermediate_developer**: "I want to build a multi-signal alpha model for BTC. I have price data, ETH for cross-asset analysis, and funding rate data. How do I systematically combine multiple signals and evaluate the composite?"
- **advanced_quant**: "I'm constructing a multi-factor alpha model for crypto futures. I want to combine orthogonal signal sources — time-series momentum, mean-reversion, cross-asset relative value, and carry (funding rate) — with IC-weighted combination. I need to evaluate diversification benefit and tail correlation."

**Environment**:
```json
{
    "data_files": [
        "BTCUSDT_1d_2021_2024.csv",
        "ETHUSDT_1d_2021_2024.csv",
        "BTCUSDT_funding_2021_2024.csv"
    ],
    "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
    "docs_available": ["alpha_research_methodology.md", "signal_evaluation.md", "statistical_tests.md", "risk_metrics.md"],
    "network_enabled": false
}
```

**Convenient tools**: `compute_indicator`, `compute_statistics`, `evaluate_signal`, `plot_chart`

**Eval strategy**:
- **Multiple signals**: At least 3 distinct signals from different alpha sources exist in workspace.
- **Correlation analysis**: Signal-to-signal correlation matrix was computed.
- **Combination method stated**: The method of combination is explicit (not ad-hoc).
- **Composite vs individual comparison**: Quantitative comparison (IC or Sharpe) between composite and each individual signal.
- **Diversification demonstrated**: Composite IC IR or Sharpe exceeds the best individual signal.

---

## 7. Difficulty & Capability Progression

```
S01  MA Crossover (prescriptive)   easy      Known strategy implementation
 │                                             (existing, stock data)
 ▼
S02  Trend-Following Research      medium    Single-asset, single-archetype research
 │                                             (daily, price-based signals)
 ▼
S03  Mean-Reversion Research       medium    Contrasting archetype + failure mode analysis
 │                                             (daily, price-based, regime awareness)
 ▼
S04  Volume/Microstructure Alpha   hard      Non-price data + cross-timeframe + fast decay
 │                                             (multi-TF, feature engineering)
 ▼
S05  Cross-Asset Alpha             hard      Multi-asset + relationship dynamics
 │                                             (BTC+ETH, daily+hourly, dollar-neutral)
 ▼
S06  Multi-Signal Combination      hard      Alpha synthesis + diversification
                                               (multiple sources, composite construction)
```

**Concept progression**:

| Progression Axis | S02 | S03 | S04 | S05 | S06 |
|------------------|-----|-----|-----|-----|-----|
| Data complexity | 1 asset, daily | 1 asset, daily | 1 asset, multi-TF | 2 assets, multi-TF | 2 assets + funding |
| Signal type | Price-based trend | Price-based reversion | Non-price microstructure | Cross-asset relative value | Composite multi-source |
| Research depth | Basic: explore → signal → eval | + failure mode analysis | + feature engineering + decay | + relationship analysis | + combination methodology |
| Key insight tested | "Does trend-following work?" | "When does reversion fail?" | "Can non-price data predict?" | "Can you exploit cross-asset dynamics?" | "Is diversification real?" |

**S02 and S03 are designed as a contrasting pair**: trend-following vs mean-reversion. The agent that teaches S03 well must explain why these two archetypes are fundamentally different and when each one works/fails. This is a core quant literacy test.

---

## 8. Evaluation Architecture

### 8.1 Programmatic Eval Focus

S-series eval is fundamentally different from D-series (data correctness) or B-series (architecture quality). S-series evals check for **research process quality**:

| Check Type | Method | What It Catches |
|------------|--------|-----------------|
| **Exploratory analysis** | Check tool logs for descriptive stats, plots, distribution analysis before signal construction | Agents that skip exploration and jump to a canned strategy |
| **Signal exists** | Check workspace for a file/code that produces a signal column | Agents that discuss ideas but never formalize them |
| **Signal evaluated** | Check for `evaluate_signal` usage or IC/correlation computation in tool logs | Agents that build signals without evaluating quality |
| **Rough PnL computed** | Check workspace/tool output for return or Sharpe estimate | Missing the practical "does this work?" check |
| **Robustness addressed** | Primarily LLM-judged: does the conversation address when the signal fails? | Agents that only show the sunny-day case |

### 8.2 Result Judge Category Rubric

Add a `strategy` entry to `CATEGORY_RESULT_RUBRICS`:

```
Strategy (alpha research) tasks — evaluation focus:
1. Research Process: Did the agent guide systematic exploration before signal construction?
2. Hypothesis Quality: Is there a stated rationale for why the signal should work?
3. Signal Formalization: Is the signal precisely defined (computable, not vague)?
4. Evaluation Rigor: Were signal quality metrics computed and interpreted?
5. Critical Assessment: Were failure modes, robustness, and limitations discussed?
```

### 8.3 Process Reasonableness Criteria

Add to `CATEGORY_PROCESS_CRITERIA`:

```
strategy: "explore data → form hypothesis → construct signal → evaluate signal quality →
           rough PnL check → assess robustness → document findings"
```

The expected process is **exploration-first** (understand the data before building a signal). An agent that jumps directly to "here's an RSI strategy" without exploring the data first should score lower on process reasonableness.

### 8.4 LLM Judge Weight (S-Series Specific)

S-series tasks rely more heavily on LLM-judged evaluation than most other categories because research quality (hypothesis clarity, robustness discussion) is hard to check programmatically.

Recommended QR blend adjustment for `strategy` category:
```
QR = 0.20 × Eval Script + 0.20 × Code Eval + 0.60 × Result Judge
```
(Higher Result Judge weight vs the standard 0.30/0.30/0.40, because research quality is primarily assessed by LLM.)

---

## 9. Relationship to B-Series: The Handoff

S-series and B-series form a continuous pipeline. Conceptually, the output of an S-task is the input material for a B-task:

```
S02 (trend signal on BTC daily)      →  B02 (build engine, use MA crossover idea)
S03 (reversion signal on BTC daily)  →  B03 (build engine with look-ahead proof, use RSI idea)
S05 (cross-asset BTC/ETH signal)     →  B04 (build multi-asset engine, use ratio idea)
S04 (volume signal, multi-TF)        →  B05 (build engine with execution sim, use breakout idea)
S06 (composite signal)               →  B06 (build walk-forward framework, use parameterized idea)
```

This pairing is **conceptual, not enforced** — each task is independently executable. But the design ensures that:
1. S-series produces alpha ideas that B-series consumes
2. The same data appears in both sections
3. The benchmark covers the full quant workflow: research → validation

---

## 10. Task Summary Table

| Task | Title | Difficulty | Data | Research Direction | Key Research Concept | New Eval Instances |
|------|-------|-----------|------|-------------------|---------------------|-------------------|
| S01 | MA Crossover | easy | AAPL, SPY | (prescriptive) | Known strategy implementation | 3 (existing) |
| S02 | Trend-Following Research | medium | BTCUSDT 1d | Trend patterns | Autocorrelation, momentum, signal evaluation | 3 |
| S03 | Mean-Reversion Research | medium | BTCUSDT 1d | Reversion patterns | Regime awareness, failure mode analysis | 3 |
| S04 | Volume/Microstructure Alpha | hard | BTCUSDT multi-TF | Non-price features | Feature engineering, cross-TF, fast decay | 3 |
| S05 | Cross-Asset Alpha | hard | BTC+ETH multi-TF | Cross-asset dynamics | Cointegration, lead-lag, dollar-neutral | 3 |
| S06 | Multi-Signal Combination | hard | BTC+ETH+funding | Alpha synthesis | Signal diversification, composite construction | 3 |

**Total new instances**: 5 tasks × 3 personas = **15 new evaluation instances**
**S-series total**: 6 tasks × 3 personas = **18 evaluation instances**

---

## 11. Implementation Checklist

### Phase 1: Tooling
- [ ] Implement `evaluate_signal` in `tools.py` (see §2 for spec)
- [ ] Register in `CORE_TOOLS` as a convenient tool
- [ ] Verify sandbox has required libraries (seaborn, sklearn, pandas_ta, arch)

### Phase 2: Reference Docs
- [ ] Write `alpha_research_methodology.md` (see §4.1 for outline)
- [ ] Write `signal_evaluation.md` (see §4.2 for outline)

### Phase 3: Task JSONs (bench/tasks/layer2/strategy/)
- [ ] S02_trend_following_research.json
- [ ] S03_mean_reversion_research.json
- [ ] S04_volume_microstructure_alpha.json
- [ ] S05_cross_asset_alpha.json
- [ ] S06_multi_signal_combination.json

### Phase 4: Eval Scripts (bench/evaluation/test_scripts/)
- [ ] S02_trend_following_research.py
- [ ] S03_mean_reversion_research.py
- [ ] S04_volume_microstructure_alpha.py
- [ ] S05_cross_asset_alpha.py
- [ ] S06_multi_signal_combination.py

### Phase 5: Scoring Integration
- [ ] Add `strategy` category to `CATEGORY_PROCESS_CRITERIA`
- [ ] Add `strategy` category to `CATEGORY_RESULT_RUBRICS`
- [ ] Add `strategy` category dimension weights to tutor 7D
- [ ] Consider adjusted QR blend weights for strategy category (higher LLM judge weight)

### Phase 6: Reference Oracle
- [ ] Generate reference executions for all 15 instances (5 tasks × 3 personas)
- [ ] Validate that reference outputs include proper research process (not just final signal)
