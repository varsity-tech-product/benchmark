# Alpha Research Methodology: A Systematic Guide

Alpha research is the disciplined process of turning raw market data into a testable trading hypothesis. The goal is not to find any pattern that happened to work in sample. The goal is to identify a signal with a plausible mechanism, measurable predictive power, and enough robustness to justify deeper validation.

---

## 1. What Is Alpha?

In practice, alpha means return that is not explained by broad market exposure alone.

- **Beta**: return from being exposed to the market or a common risk factor
- **Alpha**: return from a specific edge, feature, or structural inefficiency

Common alpha sources include:

- Slow information diffusion or investor underreaction
- Short-term overreaction followed by reversal
- Liquidity imbalances and forced flows
- Relative-value dislocations across related assets
- Carry effects such as funding or basis

Alpha is fragile. Once a pattern becomes crowded or market structure changes, signal quality can decay quickly.

---

## 2. The Research Process

A strong research loop usually looks like this:

1. **Explore the data**
   Understand the dataset before proposing a signal.
2. **Form a hypothesis**
   State why the pattern should exist.
3. **Construct a signal**
   Define a computable feature or score.
4. **Evaluate signal quality**
   Measure IC, decay, quantile spread, turnover, and coverage.
5. **Run a rough PnL check**
   Ask whether the signal is directionally useful at all.
6. **Assess robustness**
   Check stability across subperiods, regimes, and parameter choices.
7. **Document the result**
   Record both what worked and what failed.

Skipping the early exploration step often leads to cargo-cult strategy building.

---

## 3. Exploratory Data Analysis for Alpha

Before building a signal, inspect the raw data for structure.

### 3.1 Return Distribution

Look at:

- Mean and standard deviation
- Skewness and kurtosis
- Tail behavior and extreme moves
- Differences between calm and volatile periods

### 3.2 Autocorrelation and Persistence

For price-based research, inspect:

- Return autocorrelation at multiple lags
- Volatility clustering
- Trend persistence versus reversal
- Rolling regime behavior

### 3.3 Volume and Flow Features

For richer datasets, inspect:

- Volume spikes
- Quote volume versus base volume
- Trade count anomalies
- Taker buy imbalance

### 3.4 Cross-Asset Structure

When multiple assets are available, inspect:

- Rolling correlation
- Relative performance
- Spread behavior
- Lead-lag patterns
- Cointegration candidates

---

## 4. Hypothesis-Driven Research

Research should be hypothesis-driven, not parameter-mining driven.

### Good Hypothesis

"Trend-following may work in crypto because new information diffuses gradually, retail participation amplifies momentum, and 24/7 markets can sustain persistent moves."

### Weak Hypothesis

"I tested a lot of indicators and one of them looked good."

Useful hypothesis qualities:

- **Causal story**: why the pattern should exist
- **Testability**: what evidence would support or reject it
- **Specificity**: what horizon, asset class, or regime it should affect
- **Falsifiability**: what failure mode would invalidate it

If you cannot explain why a signal should work, you should assume it is noise until proven otherwise.

---

## 5. Signal Construction Best Practices

Signals should be:

- **Simple**: avoid too many moving parts
- **Explainable**: you can articulate the mechanism
- **Normalized**: comparable across time and assets
- **Leakage-safe**: only use information available at signal time

Common signal forms:

- Raw feature values
- Rolling z-scores
- Cross-sectional or time-series ranks
- Binary thresholds
- Weighted composites

Example:

```python
import pandas as pd

df["returns_20d"] = df["close"].pct_change(20)
df["signal"] = (df["returns_20d"] - df["returns_20d"].rolling(60).mean()) / (
    df["returns_20d"].rolling(60).std()
)
```

Be explicit about the exact formula and lag discipline.

---

## 6. Signal Evaluation Metrics

Useful signal-evaluation questions:

- Does the signal correlate with future returns?
- Does the signal decay immediately or persist?
- Is performance concentrated in only one bucket or regime?
- Is turnover so high that costs would likely erase the edge?

Core metrics:

- **Information Coefficient (IC)**: correlation between signal and forward return
- **IC Information Ratio**: mean IC divided by IC volatility
- **IC decay**: predictive power at longer horizons
- **Quantile spread**: return difference between strong and weak signal buckets
- **Turnover**: how fast the signal changes
- **Coverage**: fraction of periods with a usable signal
- **Rough PnL**: signal shifted by one period times realized returns

No single metric is sufficient. A modest IC with low turnover can be more valuable than a noisy high-turnover signal.

---

## 7. Robustness Assessment

Signals often look strongest when evaluated only on the period that produced them.

Check robustness through:

- **Subperiod analysis**: bull, bear, and sideways regimes
- **Parameter sensitivity**: nearby parameter values should not collapse
- **Out-of-sample splits**: test on later periods
- **Failure-mode review**: identify when the signal breaks

Examples:

- Trend signals often weaken in choppy, range-bound markets
- Mean-reversion signals often fail during strong directional breaks
- Cross-asset signals can fail when relationships structurally change
- Microstructure signals often decay too fast to survive costs

---

## 8. Research Notes Template

A compact research note should include:

1. Dataset and period
2. Research question
3. Hypothesis
4. Signal definition
5. Evaluation metrics
6. Rough PnL summary
7. Robustness and failure modes
8. Next step: iterate, discard, or hand off to full backtesting

Good research is iterative. A rejected hypothesis is still useful if it was tested cleanly and documented clearly.
