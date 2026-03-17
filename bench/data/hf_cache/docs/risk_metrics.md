# Risk Metrics: Formulas, Interpretation, and Python Code

Risk metrics quantify the risk-return trade-off of a portfolio or trading strategy. They are essential for comparing strategies, sizing positions, and communicating performance to stakeholders.

---

## 1. Sharpe Ratio

### Definition

The Sharpe Ratio measures the excess return per unit of total risk (standard deviation). It was introduced by William F. Sharpe in 1966 and remains the most widely used risk-adjusted performance measure.

### Formula

$$
\text{Sharpe Ratio} = \frac{R_p - R_f}{\sigma_p}
$$

where:
- $R_p$ = portfolio return (annualized)
- $R_f$ = risk-free rate (annualized, e.g., Treasury bill rate)
- $\sigma_p$ = standard deviation of portfolio returns (annualized)

### Annualization

When working with daily returns, annualize as follows:

$$
R_{p,\text{annual}} = \bar{r}_{\text{daily}} \times 252
$$

$$
\sigma_{p,\text{annual}} = \sigma_{\text{daily}} \times \sqrt{252}
$$

$$
\text{Sharpe Ratio} = \frac{\bar{r}_{\text{daily}} \times 252 - R_f}{\sigma_{\text{daily}} \times \sqrt{252}}
$$

Alternatively, if you treat the risk-free rate as a daily value:

$$
\text{Sharpe Ratio} = \frac{\bar{r}_{\text{daily}} - r_{f,\text{daily}}}{\sigma_{\text{daily}}} \times \sqrt{252}
$$

where $r_{f,\text{daily}} = R_f / 252$.

### Interpretation

| Sharpe Ratio | Interpretation             |
|--------------|----------------------------|
| < 0          | Strategy loses money       |
| 0 - 0.5      | Sub-par risk-adjusted return |
| 0.5 - 1.0    | Acceptable                 |
| 1.0 - 2.0    | Good                       |
| 2.0 - 3.0    | Very good                  |
| > 3.0        | Excellent (verify for errors) |

### Python Implementation

```python
import numpy as np
import pandas as pd

def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0,
                 periods_per_year: int = 252) -> float:
    """
    Calculate the annualized Sharpe Ratio.

    Parameters
    ----------
    returns : pd.Series
        Periodic (e.g., daily) returns of the strategy.
    risk_free_rate : float
        Annualized risk-free rate (e.g., 0.05 for 5%).
    periods_per_year : int
        Number of trading periods in a year.
        252 for daily, 52 for weekly, 12 for monthly.

    Returns
    -------
    float
        Annualized Sharpe Ratio.
    """
    excess_returns = returns - risk_free_rate / periods_per_year
    mean_excess = excess_returns.mean()
    std_excess = excess_returns.std(ddof=1)

    if std_excess == 0:
        return 0.0

    return (mean_excess / std_excess) * np.sqrt(periods_per_year)
```

### Limitations

- Assumes returns are normally distributed (penalizes upside volatility equally).
- Sensitive to the choice of risk-free rate.
- Can be misleading for strategies with infrequent but large losses (fat tails).
- Does not distinguish between upside and downside volatility.

---

## 2. Maximum Drawdown

### Definition

Maximum Drawdown (MDD) measures the largest peak-to-trough decline in portfolio value before a new peak is established. It represents the worst-case loss an investor would have experienced.

### Formula

$$
\text{Drawdown}_t = \frac{V_t - V_{\text{peak}}}{V_{\text{peak}}}
$$

where $V_{\text{peak}} = \max_{s \leq t} V_s$ is the running maximum portfolio value up to time $t$.

$$
\text{Maximum Drawdown} = \min_t \left( \text{Drawdown}_t \right)
$$

Note: Maximum Drawdown is typically expressed as a negative percentage (e.g., -25%).

### Python Implementation

```python
def max_drawdown(returns: pd.Series) -> dict:
    """
    Calculate the Maximum Drawdown and related statistics.

    Parameters
    ----------
    returns : pd.Series
        Periodic returns of the strategy.

    Returns
    -------
    dict
        Dictionary with keys:
        - 'max_drawdown': float, the maximum drawdown (negative value)
        - 'peak_date': Timestamp, date of the peak before the drawdown
        - 'trough_date': Timestamp, date of the trough
        - 'recovery_date': Timestamp or None, date when peak is recovered
        - 'duration_days': int, number of days from peak to trough
        - 'drawdown_series': pd.Series, the full drawdown time series
    """
    cum_returns = (1 + returns).cumprod()
    running_max = cum_returns.cummax()
    drawdown_series = (cum_returns - running_max) / running_max

    max_dd = drawdown_series.min()
    trough_date = drawdown_series.idxmin()

    # Find the peak before the trough
    peak_date = cum_returns.loc[:trough_date].idxmax()

    # Find recovery date (if any)
    peak_value = cum_returns.loc[peak_date]
    post_trough = cum_returns.loc[trough_date:]
    recovery_mask = post_trough >= peak_value
    recovery_date = recovery_mask.idxmax() if recovery_mask.any() else None

    duration = (trough_date - peak_date).days if hasattr(trough_date, 'day') else None

    return {
        'max_drawdown': max_dd,
        'peak_date': peak_date,
        'trough_date': trough_date,
        'recovery_date': recovery_date,
        'duration_days': duration,
        'drawdown_series': drawdown_series,
    }
```

### Visualization

```python
import matplotlib.pyplot as plt

def plot_drawdown(returns: pd.Series, title: str = 'Drawdown Chart'):
    """Plot the drawdown curve."""
    cum_returns = (1 + returns).cumprod()
    running_max = cum_returns.cummax()
    drawdown = (cum_returns - running_max) / running_max

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    axes[0].plot(cum_returns.index, cum_returns, label='Cumulative Return')
    axes[0].plot(running_max.index, running_max, label='Running Max',
                 linestyle='--', alpha=0.7)
    axes[0].set_title('Portfolio Value')
    axes[0].legend()

    axes[1].fill_between(drawdown.index, drawdown, 0, color='red', alpha=0.4)
    axes[1].set_title(title)
    axes[1].set_ylabel('Drawdown')

    plt.tight_layout()
    plt.show()
```

### Interpretation

- MDD of -10% means the portfolio lost 10% from its peak before recovering.
- Typical thresholds: < -20% is considered significant; < -50% is severe.
- Important for investor psychology: large drawdowns cause panic selling.

---

## 3. Sortino Ratio

### Definition

The Sortino Ratio is a modification of the Sharpe Ratio that only penalizes downside volatility. It uses the downside deviation (standard deviation of negative returns) instead of total standard deviation. This is more appropriate when return distributions are skewed or when investors are primarily concerned with losses.

### Formula

$$
\text{Sortino Ratio} = \frac{R_p - R_f}{\sigma_d}
$$

where $\sigma_d$ is the downside deviation:

$$
\sigma_d = \sqrt{\frac{1}{N} \sum_{i=1}^{N} \min(r_i - \text{MAR}, 0)^2}
$$

and MAR is the Minimum Acceptable Return (often set to 0 or the risk-free rate).

### Python Implementation

```python
def sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.0,
                  mar: float = 0.0, periods_per_year: int = 252) -> float:
    """
    Calculate the annualized Sortino Ratio.

    Parameters
    ----------
    returns : pd.Series
        Periodic (e.g., daily) returns of the strategy.
    risk_free_rate : float
        Annualized risk-free rate.
    mar : float
        Minimum Acceptable Return per period. If 0, uses 0 as threshold.
    periods_per_year : int
        Number of trading periods in a year.

    Returns
    -------
    float
        Annualized Sortino Ratio.
    """
    excess_returns = returns - risk_free_rate / periods_per_year

    # Downside returns: only consider returns below the MAR
    downside_returns = returns[returns < mar] - mar
    downside_deviation = np.sqrt((downside_returns ** 2).mean())

    if downside_deviation == 0:
        return np.inf if excess_returns.mean() > 0 else 0.0

    mean_excess = excess_returns.mean()
    return (mean_excess / downside_deviation) * np.sqrt(periods_per_year)
```

### Sharpe vs. Sortino

| Aspect               | Sharpe Ratio              | Sortino Ratio              |
|-----------------------|---------------------------|----------------------------|
| Volatility used       | Total (upside + downside) | Downside only              |
| Assumption            | Symmetric returns         | Asymmetric returns OK      |
| Penalizes upside vol  | Yes                       | No                         |
| Better for            | Symmetric distributions   | Skewed distributions       |

---

## 4. Calmar Ratio

### Definition

The Calmar Ratio compares the annualized rate of return to the maximum drawdown. It was created by Terry W. Young in 1991 and is typically calculated over a trailing 36-month period.

### Formula

$$
\text{Calmar Ratio} = \frac{R_{p,\text{annual}}}{|\text{Maximum Drawdown}|}
$$

where $R_{p,\text{annual}}$ is the compound annualized return and $|\text{Maximum Drawdown}|$ is the absolute value of the maximum drawdown.

### Python Implementation

```python
def calmar_ratio(returns: pd.Series, periods_per_year: int = 252) -> float:
    """
    Calculate the Calmar Ratio.

    Parameters
    ----------
    returns : pd.Series
        Periodic (e.g., daily) returns of the strategy.
    periods_per_year : int
        Number of trading periods in a year.

    Returns
    -------
    float
        The Calmar Ratio.
    """
    # Compound annualized return
    total_return = (1 + returns).prod() - 1
    n_years = len(returns) / periods_per_year
    if n_years <= 0:
        return 0.0
    ann_return = (1 + total_return) ** (1 / n_years) - 1

    # Maximum drawdown
    cum_returns = (1 + returns).cumprod()
    running_max = cum_returns.cummax()
    drawdown = (cum_returns - running_max) / running_max
    mdd = abs(drawdown.min())

    if mdd == 0:
        return np.inf if ann_return > 0 else 0.0

    return ann_return / mdd
```

### Interpretation

| Calmar Ratio | Interpretation                             |
|--------------|--------------------------------------------|
| < 0.5        | Poor: large drawdowns relative to returns  |
| 0.5 - 1.0    | Moderate                                   |
| 1.0 - 3.0    | Good                                       |
| > 3.0        | Excellent risk-adjusted performance        |

---

## 5. Additional Risk Metrics

### Value at Risk (VaR)

The maximum expected loss over a given time horizon at a specified confidence level.

```python
def var_historical(returns: pd.Series, confidence: float = 0.95) -> float:
    """
    Historical Value at Risk.

    Parameters
    ----------
    returns : pd.Series
        Periodic returns.
    confidence : float
        Confidence level (e.g., 0.95 for 95%).

    Returns
    -------
    float
        The VaR as a positive number representing the loss threshold.
    """
    return -np.percentile(returns, (1 - confidence) * 100)
```

### Conditional VaR (Expected Shortfall)

The expected loss given that the loss exceeds the VaR threshold.

```python
def cvar(returns: pd.Series, confidence: float = 0.95) -> float:
    """
    Conditional Value at Risk (Expected Shortfall).

    Parameters
    ----------
    returns : pd.Series
        Periodic returns.
    confidence : float
        Confidence level.

    Returns
    -------
    float
        The CVaR as a positive number.
    """
    var = var_historical(returns, confidence)
    return -returns[returns <= -var].mean()
```

### Information Ratio

Measures excess return per unit of tracking error relative to a benchmark.

```python
def information_ratio(portfolio_returns: pd.Series,
                      benchmark_returns: pd.Series,
                      periods_per_year: int = 252) -> float:
    """
    Calculate the annualized Information Ratio.
    """
    active_return = portfolio_returns - benchmark_returns
    tracking_error = active_return.std(ddof=1) * np.sqrt(periods_per_year)

    if tracking_error == 0:
        return 0.0

    ann_active_return = active_return.mean() * periods_per_year
    return ann_active_return / tracking_error
```

---

## 6. Comprehensive Risk Report

```python
def risk_report(returns: pd.Series, risk_free_rate: float = 0.0,
                periods_per_year: int = 252) -> pd.DataFrame:
    """
    Generate a comprehensive risk report.

    Parameters
    ----------
    returns : pd.Series
        Periodic (e.g., daily) returns.
    risk_free_rate : float
        Annualized risk-free rate.
    periods_per_year : int
        Number of trading periods per year.

    Returns
    -------
    pd.DataFrame
        DataFrame with metric names and values.
    """
    total_return = (1 + returns).prod() - 1
    n_years = len(returns) / periods_per_year
    ann_return = (1 + total_return) ** (1 / n_years) - 1
    ann_vol = returns.std(ddof=1) * np.sqrt(periods_per_year)

    # Drawdown
    cum = (1 + returns).cumprod()
    dd = (cum - cum.cummax()) / cum.cummax()
    mdd = dd.min()

    # Downside deviation
    downside = returns[returns < 0]
    downside_dev = np.sqrt((downside ** 2).mean()) * np.sqrt(periods_per_year)

    metrics = {
        'Total Return': f"{total_return:.2%}",
        'Annualized Return': f"{ann_return:.2%}",
        'Annualized Volatility': f"{ann_vol:.2%}",
        'Sharpe Ratio': f"{sharpe_ratio(returns, risk_free_rate, periods_per_year):.2f}",
        'Sortino Ratio': f"{sortino_ratio(returns, risk_free_rate, 0, periods_per_year):.2f}",
        'Calmar Ratio': f"{calmar_ratio(returns, periods_per_year):.2f}",
        'Max Drawdown': f"{mdd:.2%}",
        'VaR (95%)': f"{var_historical(returns, 0.95):.2%}",
        'CVaR (95%)': f"{cvar(returns, 0.95):.2%}",
        'Skewness': f"{returns.skew():.2f}",
        'Kurtosis': f"{returns.kurtosis():.2f}",
        'Best Day': f"{returns.max():.2%}",
        'Worst Day': f"{returns.min():.2%}",
        'Positive Days': f"{(returns > 0).sum()} / {len(returns)}",
        'Win Rate': f"{(returns > 0).mean():.2%}",
    }

    return pd.DataFrame(list(metrics.items()), columns=['Metric', 'Value'])
```

---

## 7. Choosing the Right Metric

| Scenario                                          | Recommended Metric      |
|---------------------------------------------------|-------------------------|
| General risk-adjusted performance comparison      | Sharpe Ratio            |
| Strategy with asymmetric returns                  | Sortino Ratio           |
| Concerned about worst-case losses                 | Maximum Drawdown        |
| Comparing return to worst-case drawdown           | Calmar Ratio            |
| Regulatory risk reporting                         | VaR / CVaR              |
| Performance relative to a benchmark               | Information Ratio       |
| Tail risk assessment                              | CVaR, Skewness, Kurtosis|

---

## 8. Practical Considerations

1. **Annualization consistency**: Always annualize both return and risk using the same assumption (252 trading days, 365 calendar days, etc.).
2. **Lookback period**: Metrics are sensitive to the evaluation period. Report the time range explicitly.
3. **Stationarity**: Risk metrics assume some degree of stationarity. Regime changes can invalidate historical estimates.
4. **Compounding effects**: For multi-year analysis, use geometric (compound) returns, not arithmetic.
5. **Sample size**: Metrics computed on small samples have high estimation error. Aim for at least 2-3 years of daily data.
