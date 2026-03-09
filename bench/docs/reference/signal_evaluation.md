# Signal Evaluation: Metrics and Methods

Signal evaluation asks a simple question: does a feature contain usable information about future returns?

This reference focuses on the metrics most commonly used during alpha research. These are research-stage diagnostics, not a substitute for a full execution-aware backtest.

---

## 1. Information Coefficient (IC)

The Information Coefficient measures the relationship between a signal and future returns.

### Formula

For a signal observed at time `t` and forward return over the next `h` periods:

$$
IC = \mathrm{corr}(signal_t, return_{t \rightarrow t+h})
$$

Many researchers prefer **Spearman rank correlation** because it is less sensitive to outliers and focuses on ordering rather than absolute scale.

### Interpretation

| IC Mean | Interpretation |
|---------|----------------|
| > 0.05 | Strong |
| 0.02 to 0.05 | Useful |
| 0.01 to 0.02 | Weak but possibly tradable |
| < 0.01 | Likely noise |

Magnitude alone is not enough. A small but consistent IC can be valuable.

### Python Example

```python
from scipy.stats import spearmanr

aligned = df[["signal", "forward_return"]].dropna()
ic_value = spearmanr(aligned["signal"], aligned["forward_return"]).correlation
```

---

## 2. IC Volatility, IR, and t-Statistic

If you compute IC repeatedly across windows or dates, you can summarize stability.

$$
ICIR = \frac{\mathrm{mean}(IC)}{\mathrm{std}(IC)}
$$

Useful interpretations:

- **High mean, low volatility**: signal is stable
- **High mean, high volatility**: unstable or regime-dependent
- **Low mean, low volatility**: weak but consistent
- **Low mean, high volatility**: likely not useful

For a rolling or repeated IC series, also inspect:

- Mean
- Standard deviation
- t-statistic
- p-value

Do not over-interpret p-values after testing many ideas.

---

## 3. IC Decay

IC decay checks how predictive power changes as the forecast horizon grows.

Example:

- Horizon 1: next-bar or next-day return
- Horizon 2: two-day forward return
- Horizon 5: one-week forward return

Interpretation:

- **Fast decay**: very short-horizon signal, often high turnover
- **Slow decay**: signal may support slower trading or holding periods
- **Sign flip**: timing mismatch or unstable effect

### Python Example

```python
from scipy.stats import spearmanr

decay = []
for horizon in range(1, 6):
    forward = df["close"].shift(-horizon) / df["close"] - 1
    aligned = pd.concat([df["signal"], forward], axis=1).dropna()
    decay.append(
        spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1]).correlation
    )
```

---

## 4. Quantile Analysis

Quantile analysis asks whether stronger signals correspond to better returns.

Basic workflow:

1. Rank observations by signal
2. Split them into quantiles
3. Compute mean forward return per bucket

What to look for:

- **Monotonic ordering**: top quantile beats lower quantiles consistently
- **Large top-minus-bottom spread**: stronger economic value
- **Only extreme buckets matter**: signal may be nonlinear

### Python Example

```python
aligned = df[["signal", "forward_return"]].dropna().copy()
aligned["bucket"] = pd.qcut(aligned["signal"], q=5, labels=False, duplicates="drop")
quantile_returns = aligned.groupby("bucket")["forward_return"].mean()
long_short_spread = quantile_returns.iloc[-1] - quantile_returns.iloc[0]
```

Quantile analysis is often easier to interpret than a raw correlation coefficient.

---

## 5. Turnover

Turnover measures how fast the signal changes.

A simple proxy is:

$$
\mathrm{turnover} = \mathrm{mean}(|signal_t - signal_{t-1}|)
$$

High turnover matters because:

- It implies more trades
- It increases fee and slippage sensitivity
- It often indicates a short-lived signal

A high-IC signal can still be unattractive if turnover is extreme.

---

## 6. Rough PnL Estimation

Research-stage PnL is a quick merit check, not a production backtest.

Typical construction:

$$
strategy\_return_t = signal_{t-1} \times return_t
$$

The shift is critical. It avoids using today's signal on today's realized move.

### Python Example

```python
df["returns"] = df["close"].pct_change()
df["strategy_return"] = df["signal"].shift(1) * df["returns"]

equity = (1 + df["strategy_return"].fillna(0)).cumprod()
total_return = equity.iloc[-1] - 1
sharpe = (
    df["strategy_return"].mean() / df["strategy_return"].std() * (252 ** 0.5)
)
```

Remember the limitations:

- No execution engine
- Usually no slippage model
- Often no fee or capacity model
- Position sizing may be simplistic

Use rough PnL to answer "is this worth deeper work?" not "is this deployable?"

---

## 7. Coverage and Diagnostics

Useful diagnostic checks:

- **Signal coverage**: how often is the signal defined?
- **Signal mean/std**: is the scale sensible?
- **Signal autocorrelation**: is it too sticky or too noisy?
- **Correlation with absolute returns**: is it really a direction signal, or just a volatility proxy?

These diagnostics help distinguish a predictive signal from a mislabeled risk or volatility feature.

---

## 8. Practical Interpretation Framework

A research signal is more promising when:

- IC is positive and reasonably stable
- Quantile returns are monotonic
- Rough PnL is directionally positive
- Turnover is not absurd for the horizon
- Failure modes are understood

A research signal is less convincing when:

- Results come from one isolated subperiod
- Nearby parameters collapse
- Quantile spreads are noisy or inverted
- PnL depends on same-bar execution
- The signal is only a disguised volatility bet

Signal evaluation should narrow the field. It should not be treated as proof.
