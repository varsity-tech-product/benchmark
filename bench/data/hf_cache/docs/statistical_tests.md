# Statistical Tests for Quantitative Finance

Statistical tests are essential in quantitative finance for validating assumptions about data, identifying tradeable relationships, and ensuring the robustness of strategies. This reference covers the most commonly used tests with mathematical foundations and Python implementations.

---

## 1. Augmented Dickey-Fuller (ADF) Test for Stationarity

### Background

A time series is **stationary** if its statistical properties (mean, variance, autocorrelation) do not change over time. Most statistical models and many trading strategies require stationarity. Stock prices are typically non-stationary (they trend), but stock returns are usually stationary.

### The Unit Root Hypothesis

The ADF test checks for the presence of a **unit root** in a time series. A unit root implies the series is non-stationary (integrated of order 1 or higher).

Consider the autoregressive model:

$$
y_t = \rho \cdot y_{t-1} + \epsilon_t
$$

- If $|\rho| = 1$, the series has a unit root (random walk, non-stationary).
- If $|\rho| < 1$, the series is stationary.

The ADF test reparameterizes this as:

$$
\Delta y_t = \alpha + \beta t + \gamma y_{t-1} + \sum_{i=1}^{p} \delta_i \Delta y_{t-i} + \epsilon_t
$$

where:
- $\alpha$ is a constant (drift)
- $\beta t$ is a deterministic time trend (optional)
- $\gamma = \rho - 1$ is the coefficient being tested
- The lagged difference terms $\Delta y_{t-i}$ account for serial correlation

### Hypotheses

- **$H_0$ (null hypothesis)**: $\gamma = 0$ -- the series has a unit root (non-stationary)
- **$H_1$ (alternative hypothesis)**: $\gamma < 0$ -- the series is stationary

### Interpretation

- If the **p-value < significance level** (typically 0.05), reject $H_0$ and conclude the series is stationary.
- If the **ADF test statistic** is more negative than the critical value, reject $H_0$.

### Python Implementation

```python
from statsmodels.tsa.stattools import adfuller
import pandas as pd

def adf_test(series: pd.Series, name: str = 'Series',
             significance: float = 0.05) -> dict:
    """
    Perform the Augmented Dickey-Fuller test for stationarity.

    Parameters
    ----------
    series : pd.Series
        The time series to test.
    name : str
        Name of the series (for display purposes).
    significance : float
        Significance level for the test (default 0.05).

    Returns
    -------
    dict
        Dictionary with test results.
    """
    result = adfuller(series.dropna(), autolag='AIC')

    output = {
        'series_name': name,
        'test_statistic': result[0],
        'p_value': result[1],
        'lags_used': result[2],
        'n_observations': result[3],
        'critical_values': result[4],
        'is_stationary': result[1] < significance,
    }

    # Print summary
    print(f"Augmented Dickey-Fuller Test: {name}")
    print(f"  Test Statistic: {result[0]:.4f}")
    print(f"  P-Value:        {result[1]:.6f}")
    print(f"  Lags Used:      {result[2]}")
    print(f"  Observations:   {result[3]}")
    for key, value in result[4].items():
        print(f"  Critical Value ({key}): {value:.4f}")
    print(f"  Conclusion: {'Stationary' if output['is_stationary'] else 'Non-stationary'}"
          f" (at {significance:.0%} significance)")
    print()

    return output
```

### Practical Examples

```python
import numpy as np
import pandas as pd

# Example 1: Stock prices (expected: non-stationary)
# prices = pd.Series(...)  # Your price data
# adf_test(prices, name='Stock Price')

# Example 2: Stock returns (expected: stationary)
# returns = prices.pct_change().dropna()
# adf_test(returns, name='Stock Returns')

# Example 3: Spread between two cointegrated stocks
# spread = prices_A - beta * prices_B
# adf_test(spread, name='Pair Spread')

# Simulated example
np.random.seed(42)
random_walk = pd.Series(np.cumsum(np.random.randn(1000)))
stationary_series = pd.Series(np.random.randn(1000))

adf_test(random_walk, name='Random Walk')       # Non-stationary
adf_test(stationary_series, name='White Noise')  # Stationary
```

### Choosing the Regression Type

The `adfuller` function's `regression` parameter controls which deterministic terms are included:

| Parameter | Model                | Use When                                   |
|-----------|----------------------|--------------------------------------------|
| `'c'`     | Constant only        | Default; series has a non-zero mean        |
| `'ct'`    | Constant + trend     | Series may have a deterministic trend      |
| `'ctt'`   | Constant + two trends| Rare; quadratic trend                      |
| `'n'`     | No constant or trend | Series is known to have zero mean          |

---

## 2. Engle-Granger Cointegration Test

### Background

Two time series are **cointegrated** if they are individually non-stationary (I(1)) but a linear combination of them is stationary (I(0)). This is the foundation of **pairs trading**: if two asset prices are cointegrated, their spread is mean-reverting.

### The Concept

If $X_t$ and $Y_t$ are both I(1) (have unit roots), they are cointegrated if there exists a coefficient $\beta$ such that:

$$
Z_t = Y_t - \beta X_t
$$

is stationary (I(0)).

### Engle-Granger Two-Step Procedure

1. **Step 1**: Regress $Y_t$ on $X_t$ using OLS to estimate $\hat{\beta}$:
   $$Y_t = \alpha + \hat{\beta} X_t + \hat{\epsilon}_t$$

2. **Step 2**: Apply the ADF test to the residuals $\hat{\epsilon}_t$. If the residuals are stationary, the series are cointegrated.

### Hypotheses

- **$H_0$**: No cointegration (residuals have a unit root)
- **$H_1$**: Cointegration exists (residuals are stationary)

**Important**: The critical values for testing residuals differ from standard ADF critical values because $\hat{\beta}$ is estimated from the data. Use the Engle-Granger specific critical values or the `coint()` function which handles this automatically.

### Python Implementation

```python
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm

def engle_granger_test(series_y: pd.Series, series_x: pd.Series,
                       significance: float = 0.05) -> dict:
    """
    Perform the Engle-Granger two-step cointegration test.

    Parameters
    ----------
    series_y : pd.Series
        Dependent variable (e.g., price of asset Y).
    series_x : pd.Series
        Independent variable (e.g., price of asset X).
    significance : float
        Significance level for the test.

    Returns
    -------
    dict
        Dictionary with test results including hedge ratio.
    """
    # Step 1: OLS regression to find hedge ratio
    X = sm.add_constant(series_x)
    model = sm.OLS(series_y, X).fit()
    hedge_ratio = model.params.iloc[1]
    intercept = model.params.iloc[0]

    # Residuals (spread)
    spread = series_y - hedge_ratio * series_x - intercept

    # Step 2: Cointegration test (uses Engle-Granger critical values)
    coint_stat, p_value, crit_values = coint(series_y, series_x)

    output = {
        'test_statistic': coint_stat,
        'p_value': p_value,
        'critical_values': {
            '1%': crit_values[0],
            '5%': crit_values[1],
            '10%': crit_values[2],
        },
        'hedge_ratio': hedge_ratio,
        'intercept': intercept,
        'spread': spread,
        'is_cointegrated': p_value < significance,
        'ols_r_squared': model.rsquared,
    }

    # Print summary
    print("Engle-Granger Cointegration Test")
    print(f"  Test Statistic: {coint_stat:.4f}")
    print(f"  P-Value:        {p_value:.6f}")
    print(f"  Critical Values: 1%={crit_values[0]:.4f}, "
          f"5%={crit_values[1]:.4f}, 10%={crit_values[2]:.4f}")
    print(f"  Hedge Ratio:    {hedge_ratio:.4f}")
    print(f"  Intercept:      {intercept:.4f}")
    print(f"  OLS R-squared:  {model.rsquared:.4f}")
    print(f"  Conclusion: {'Cointegrated' if output['is_cointegrated'] else 'Not cointegrated'}"
          f" (at {significance:.0%} significance)")
    print()

    return output
```

### Pairs Trading Application

```python
def pairs_trading_signals(spread: pd.Series, window: int = 20,
                          entry_z: float = 2.0, exit_z: float = 0.5) -> pd.Series:
    """
    Generate pairs trading signals based on z-score of the spread.

    Parameters
    ----------
    spread : pd.Series
        The spread (residuals) between two cointegrated series.
    window : int
        Lookback window for computing rolling mean and std.
    entry_z : float
        Z-score threshold for entering a position.
    exit_z : float
        Z-score threshold for exiting a position.

    Returns
    -------
    pd.Series
        Signal: +1 (long spread), -1 (short spread), 0 (no position).
    """
    rolling_mean = spread.rolling(window).mean()
    rolling_std = spread.rolling(window).std()
    z_score = (spread - rolling_mean) / rolling_std

    signal = pd.Series(0, index=spread.index)
    signal[z_score < -entry_z] = 1    # Spread is too low -> buy spread
    signal[z_score > entry_z] = -1    # Spread is too high -> sell spread
    signal[abs(z_score) < exit_z] = 0  # Close position near mean

    return signal
```

### Visualization

```python
import matplotlib.pyplot as plt

def plot_cointegration(series_y, series_x, spread, name_y='Y', name_x='X'):
    """Visualize cointegrated series and their spread."""
    fig, axes = plt.subplots(3, 1, figsize=(14, 10))

    axes[0].plot(series_y.index, series_y, label=name_y)
    axes[0].plot(series_x.index, series_x, label=name_x)
    axes[0].set_title('Price Series')
    axes[0].legend()

    axes[1].plot(spread.index, spread, color='purple')
    axes[1].axhline(y=spread.mean(), color='black', linestyle='--', label='Mean')
    axes[1].axhline(y=spread.mean() + 2*spread.std(), color='red',
                     linestyle='--', label='+2 Std')
    axes[1].axhline(y=spread.mean() - 2*spread.std(), color='green',
                     linestyle='--', label='-2 Std')
    axes[1].set_title('Spread')
    axes[1].legend()

    z_score = (spread - spread.rolling(20).mean()) / spread.rolling(20).std()
    axes[2].plot(z_score.index, z_score, color='orange')
    axes[2].axhline(y=0, color='black', linestyle='-')
    axes[2].axhline(y=2, color='red', linestyle='--')
    axes[2].axhline(y=-2, color='green', linestyle='--')
    axes[2].set_title('Z-Score of Spread')

    plt.tight_layout()
    plt.show()
```

---

## 3. Correlation Matrix and Interpretation

### Pearson Correlation

The Pearson correlation coefficient measures the linear relationship between two variables:

$$
r_{XY} = \frac{\text{Cov}(X, Y)}{\sigma_X \cdot \sigma_Y} = \frac{\sum_{i=1}^{n}(x_i - \bar{x})(y_i - \bar{y})}{\sqrt{\sum_{i=1}^{n}(x_i - \bar{x})^2 \cdot \sum_{i=1}^{n}(y_i - \bar{y})^2}}
$$

### Interpretation

| Range of $r$      | Interpretation              |
|--------------------|-----------------------------|
| 0.8 to 1.0        | Very strong positive        |
| 0.6 to 0.8        | Strong positive             |
| 0.4 to 0.6        | Moderate positive           |
| 0.2 to 0.4        | Weak positive               |
| -0.2 to 0.2       | Negligible / no correlation |
| -0.4 to -0.2      | Weak negative               |
| -0.6 to -0.4      | Moderate negative           |
| -0.8 to -0.6      | Strong negative             |
| -1.0 to -0.8      | Very strong negative        |

### Python Implementation

```python
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from scipy import stats

def correlation_analysis(returns_df: pd.DataFrame,
                         method: str = 'pearson') -> dict:
    """
    Perform correlation analysis on a DataFrame of returns.

    Parameters
    ----------
    returns_df : pd.DataFrame
        DataFrame where each column is a return series for an asset.
    method : str
        Correlation method: 'pearson', 'spearman', or 'kendall'.

    Returns
    -------
    dict
        Dictionary with correlation matrix and p-value matrix.
    """
    # Correlation matrix
    corr_matrix = returns_df.corr(method=method)

    # P-value matrix (statistical significance)
    n = len(returns_df)
    p_values = pd.DataFrame(
        np.zeros((len(corr_matrix), len(corr_matrix))),
        index=corr_matrix.index,
        columns=corr_matrix.columns
    )

    for i, col_i in enumerate(returns_df.columns):
        for j, col_j in enumerate(returns_df.columns):
            if i == j:
                p_values.iloc[i, j] = 0.0
            else:
                if method == 'pearson':
                    stat, pval = stats.pearsonr(
                        returns_df[col_i].dropna(),
                        returns_df[col_j].dropna()
                    )
                elif method == 'spearman':
                    stat, pval = stats.spearmanr(
                        returns_df[col_i].dropna(),
                        returns_df[col_j].dropna()
                    )
                elif method == 'kendall':
                    stat, pval = stats.kendalltau(
                        returns_df[col_i].dropna(),
                        returns_df[col_j].dropna()
                    )
                p_values.iloc[i, j] = pval

    return {
        'correlation_matrix': corr_matrix,
        'p_values': p_values,
        'method': method,
    }
```

### Visualization

```python
def plot_correlation_matrix(corr_matrix: pd.DataFrame,
                            title: str = 'Correlation Matrix'):
    """
    Plot a heatmap of the correlation matrix.
    """
    fig, ax = plt.subplots(figsize=(10, 8))

    mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)

    sns.heatmap(
        corr_matrix,
        mask=mask,
        annot=True,
        fmt='.2f',
        cmap='RdBu_r',
        center=0,
        vmin=-1,
        vmax=1,
        square=True,
        linewidths=0.5,
        ax=ax
    )

    ax.set_title(title)
    plt.tight_layout()
    plt.show()
```

### Rolling Correlation

Correlation is not static. Compute rolling correlation to observe how relationships evolve:

```python
def rolling_correlation(series_a: pd.Series, series_b: pd.Series,
                        window: int = 60) -> pd.Series:
    """
    Compute rolling Pearson correlation between two series.

    Parameters
    ----------
    series_a, series_b : pd.Series
        Two return series with aligned indices.
    window : int
        Rolling window size.

    Returns
    -------
    pd.Series
        Rolling correlation values.
    """
    return series_a.rolling(window).corr(series_b)
```

### Correlation vs. Cointegration

| Aspect          | Correlation                    | Cointegration                      |
|-----------------|--------------------------------|------------------------------------|
| What it measures | Linear co-movement of returns | Long-run equilibrium of prices     |
| Input data      | Returns (stationary)           | Prices (non-stationary)            |
| Implies trading | Not directly                   | Yes, via mean-reverting spread     |
| Can be zero     | Even if cointegrated           | Even if highly correlated          |
| Time-varying    | Often unstable                 | More stable structural relationship|

### Spearman Rank Correlation

Measures monotonic (not necessarily linear) relationships. Robust to outliers.

```python
from scipy.stats import spearmanr

rho, p_value = spearmanr(series_a, series_b)
print(f"Spearman rho: {rho:.4f}, p-value: {p_value:.6f}")
```

---

## 4. Additional Statistical Tests

### Jarque-Bera Test for Normality

Tests whether returns follow a normal distribution by examining skewness and kurtosis.

```python
from scipy.stats import jarque_bera

def normality_test(returns: pd.Series, name: str = 'Returns') -> dict:
    """
    Test if returns are normally distributed using Jarque-Bera test.

    H0: Returns are normally distributed.
    H1: Returns are not normally distributed.
    """
    jb_stat, p_value = jarque_bera(returns.dropna())
    skew = returns.skew()
    kurt = returns.kurtosis()  # Excess kurtosis (normal = 0)

    print(f"Jarque-Bera Test: {name}")
    print(f"  JB Statistic: {jb_stat:.4f}")
    print(f"  P-Value:      {p_value:.6f}")
    print(f"  Skewness:     {skew:.4f}")
    print(f"  Kurtosis:     {kurt:.4f} (excess)")
    print(f"  Conclusion:   {'Not Normal' if p_value < 0.05 else 'Normal'}")
    print()

    return {
        'jb_statistic': jb_stat,
        'p_value': p_value,
        'skewness': skew,
        'kurtosis': kurt,
        'is_normal': p_value >= 0.05,
    }
```

### Granger Causality Test

Tests whether one time series is useful in forecasting another. Note: this tests predictive ability, not true causation.

```python
from statsmodels.tsa.stattools import grangercausalitytests

def granger_causality(data: pd.DataFrame, x_col: str, y_col: str,
                      max_lag: int = 5) -> dict:
    """
    Test if x Granger-causes y.

    Parameters
    ----------
    data : pd.DataFrame
        DataFrame containing both series.
    x_col : str
        Column name of the potential cause.
    y_col : str
        Column name of the potential effect.
    max_lag : int
        Maximum number of lags to test.

    Returns
    -------
    dict
        P-values for each lag.
    """
    test_data = data[[y_col, x_col]].dropna()
    results = grangercausalitytests(test_data, maxlag=max_lag, verbose=True)

    p_values = {}
    for lag in range(1, max_lag + 1):
        f_test_pvalue = results[lag][0]['ssr_ftest'][1]
        p_values[f'lag_{lag}'] = f_test_pvalue

    return p_values
```

### Ljung-Box Test for Autocorrelation

Tests whether a series exhibits significant autocorrelation (useful for validating model residuals).

```python
from statsmodels.stats.diagnostic import acorr_ljungbox

def ljung_box_test(series: pd.Series, lags: int = 10,
                   name: str = 'Series') -> pd.DataFrame:
    """
    Perform the Ljung-Box test for autocorrelation.

    H0: No autocorrelation up to lag k.
    H1: Autocorrelation exists at some lag <= k.
    """
    result = acorr_ljungbox(series.dropna(), lags=lags, return_df=True)

    print(f"Ljung-Box Test: {name}")
    print(result.to_string())
    print()

    return result
```

---

## 5. Practical Workflow

A typical statistical analysis workflow for a pairs trading strategy:

```python
import pandas as pd
import numpy as np
from statsmodels.tsa.stattools import adfuller, coint

# 1. Load price data for two candidate assets
prices_a = pd.read_csv('asset_a.csv', index_col='Date', parse_dates=True)['Close']
prices_b = pd.read_csv('asset_b.csv', index_col='Date', parse_dates=True)['Close']

# 2. Test individual series for stationarity (should be non-stationary)
print("=== Stationarity Tests on Prices ===")
adf_a = adf_test(prices_a, name='Asset A Price')
adf_b = adf_test(prices_b, name='Asset B Price')

# 3. Test returns for stationarity (should be stationary)
returns_a = prices_a.pct_change().dropna()
returns_b = prices_b.pct_change().dropna()

print("=== Stationarity Tests on Returns ===")
adf_ret_a = adf_test(returns_a, name='Asset A Returns')
adf_ret_b = adf_test(returns_b, name='Asset B Returns')

# 4. Check correlation of returns
corr = returns_a.corr(returns_b)
print(f"Return Correlation: {corr:.4f}")

# 5. Test for cointegration (the key test for pairs trading)
print("=== Cointegration Test ===")
coint_result = engle_granger_test(prices_a, prices_b)

# 6. If cointegrated, generate trading signals
if coint_result['is_cointegrated']:
    spread = coint_result['spread']
    signals = pairs_trading_signals(spread, window=20, entry_z=2.0, exit_z=0.5)
    print(f"Number of long signals:  {(signals == 1).sum()}")
    print(f"Number of short signals: {(signals == -1).sum()}")
else:
    print("Series are not cointegrated. Pairs trading not recommended.")
```

---

## 6. Common Pitfalls

1. **Testing prices instead of returns for correlation**: Prices are non-stationary; their correlation is spurious. Always compute correlation on returns.
2. **Confusing correlation with cointegration**: Two highly correlated stocks may not be cointegrated, and two cointegrated stocks may have low return correlation.
3. **Multiple testing problem**: When scanning many pairs for cointegration, apply Bonferroni or FDR correction to avoid false positives.
4. **Ignoring structural breaks**: Cointegration relationships can break down. Use rolling-window tests to monitor stability.
5. **Using standard ADF critical values for Engle-Granger**: The residuals-based test requires different critical values. Always use `coint()` from statsmodels.
6. **Small sample sizes**: Statistical tests require sufficient data. Aim for at least 250 observations (one year of daily data) for reliable results.
