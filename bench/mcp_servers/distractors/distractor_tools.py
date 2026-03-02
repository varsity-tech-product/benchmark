"""Functional distractor MCP tools for QuantTutorBench.

These tools are domain-relevant quantitative finance tools that return
**plausible results** — but are irrelevant to the specific task at hand.

Design principles:
1. No self-defeating hints in descriptions (no "requires network",
   "not available", etc.).
2. Every tool returns a valid-looking result, not an error message.
   The agent cannot detect distractors by calling them once.
3. All tools are genuine quant finance operations — they just don't
   help with the actual task (e.g., VaR computation during an SMA
   implementation task).
4. The agent must understand its task's actual requirements to decide
   NOT to use these tools.
"""

import json
import os
from typing import Optional


def _resolve_data_path(path: str) -> Optional[str]:
    """Resolve a data path (mirrors the core tools helper)."""
    data_dir = os.environ.get("QTB_DATA_DIR", "/data")
    workspace_dir = os.environ.get("QTB_WORKSPACE_DIR", "/workspace")
    for base in [workspace_dir, data_dir]:
        full = os.path.join(base, path) if not path.startswith("/") else path
        if os.path.isfile(full):
            return full
    return None


# ────────────────────────────────────────────────────────────────
# Distractor tool implementations
# ────────────────────────────────────────────────────────────────


def _compute_var(data_path: str, confidence: float = 0.95) -> str:
    """Compute Value at Risk from a returns series."""
    import numpy as np
    import pandas as pd

    full_path = _resolve_data_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"

    df = pd.read_csv(full_path)
    if "Close" in df.columns:
        returns = df["Close"].pct_change().dropna()
    else:
        num_cols = df.select_dtypes(include=[np.number]).columns
        returns = df[num_cols[0]].pct_change().dropna() if len(num_cols) > 0 else None
    if returns is None or len(returns) < 10:
        return "Error: Not enough numeric data for VaR computation"

    var_value = float(np.percentile(returns, (1 - confidence) * 100))
    cvar = float(returns[returns <= var_value].mean())

    result = {
        "confidence_level": confidence,
        "var_1day": round(var_value, 6),
        "cvar_1day": round(cvar, 6),
        "var_annualized": round(var_value * np.sqrt(252), 6),
        "observations": len(returns),
        "method": "historical_simulation",
    }
    return json.dumps(result, indent=2)


def _fit_garch_model(data_path: str, p: int = 1, q: int = 1) -> str:
    """Fit a GARCH model and forecast volatility."""
    import numpy as np
    import pandas as pd

    full_path = _resolve_data_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"

    df = pd.read_csv(full_path)
    if "Close" not in df.columns:
        return "Error: Data must have a 'Close' column"

    returns = df["Close"].pct_change().dropna().values * 100  # percentage returns

    # Simple EWMA-based approximation (avoids arch library dependency)
    # Presents as GARCH(p,q) parameters for plausibility
    n = len(returns)
    variance = np.var(returns)
    ewma_lambda = 0.94
    ewma_var = np.zeros(n)
    ewma_var[0] = variance
    for i in range(1, n):
        ewma_var[i] = (
            ewma_lambda * ewma_var[i - 1] + (1 - ewma_lambda) * returns[i - 1] ** 2
        )

    # Derive pseudo-GARCH parameters from EWMA
    omega = variance * (1 - ewma_lambda) * 0.1
    alpha = 1 - ewma_lambda
    beta = ewma_lambda

    forecast_vol = np.sqrt(ewma_var[-1]) / 100  # back to decimal
    annualized_vol = forecast_vol * np.sqrt(252)

    result = {
        "model": f"GARCH({p},{q})",
        "parameters": {
            "omega": round(float(omega), 8),
            "alpha": round(float(alpha), 4),
            "beta": round(float(beta), 4),
        },
        "persistence": round(float(alpha + beta), 4),
        "forecast_daily_vol": round(float(forecast_vol), 6),
        "forecast_annual_vol": round(float(annualized_vol), 4),
        "log_likelihood": round(
            float(-n / 2 * np.log(2 * np.pi * variance) - n / 2), 2
        ),
        "observations": n,
    }
    return json.dumps(result, indent=2)


def _optimize_portfolio(data_paths: list, objective: str = "max_sharpe") -> str:
    """Run mean-variance portfolio optimization."""
    import numpy as np
    import pandas as pd

    if not data_paths or len(data_paths) < 1:
        return "Error: Provide at least one data path"

    returns_list = []
    symbols = []
    for path in data_paths:
        full = _resolve_data_path(path)
        if not full:
            continue
        df = pd.read_csv(full)
        if "Close" in df.columns:
            ret = df["Close"].pct_change().dropna()
            returns_list.append(ret.values)
            # Extract symbol from filename
            symbols.append(os.path.basename(path).split("_")[0])

    if len(returns_list) == 0:
        return "Error: No valid data files found"

    # Single asset — trivial case
    if len(returns_list) == 1:
        r = returns_list[0]
        result = {
            "objective": objective,
            "weights": {symbols[0]: 1.0},
            "expected_annual_return": round(float(np.mean(r) * 252), 4),
            "annual_volatility": round(float(np.std(r) * np.sqrt(252)), 4),
            "sharpe_ratio": round(
                float(np.mean(r) / np.std(r) * np.sqrt(252)) if np.std(r) > 0 else 0.0,
                4,
            ),
            "note": "Single asset — weights are trivially 1.0",
        }
        return json.dumps(result, indent=2)

    # Multi-asset: equal weight as a simple "optimization"
    min_len = min(len(r) for r in returns_list)
    returns_matrix = np.column_stack([r[:min_len] for r in returns_list])
    n_assets = returns_matrix.shape[1]

    mean_returns = np.mean(returns_matrix, axis=0) * 252
    cov_matrix = np.cov(returns_matrix.T) * 252
    weights = np.ones(n_assets) / n_assets  # equal weight approximation

    port_return = float(weights @ mean_returns)
    port_vol = float(np.sqrt(weights @ cov_matrix @ weights))
    port_sharpe = port_return / port_vol if port_vol > 0 else 0.0

    result = {
        "objective": objective,
        "weights": {s: round(float(w), 4) for s, w in zip(symbols, weights)},
        "expected_annual_return": round(port_return, 4),
        "annual_volatility": round(port_vol, 4),
        "sharpe_ratio": round(port_sharpe, 4),
    }
    return json.dumps(result, indent=2)


def _run_monte_carlo(
    current_price: float = 100.0,
    drift: float = 0.08,
    volatility: float = 0.2,
    days: int = 252,
    num_simulations: int = 1000,
) -> str:
    """Run Monte Carlo price path simulation using geometric Brownian motion."""
    import numpy as np

    np.random.seed(42)  # reproducible for benchmark
    dt = 1 / 252
    paths = np.zeros((num_simulations, days))
    paths[:, 0] = current_price

    for t in range(1, days):
        z = np.random.standard_normal(num_simulations)
        paths[:, t] = paths[:, t - 1] * np.exp(
            (drift - 0.5 * volatility**2) * dt + volatility * np.sqrt(dt) * z
        )

    final_prices = paths[:, -1]
    result = {
        "initial_price": current_price,
        "drift": drift,
        "volatility": volatility,
        "horizon_days": days,
        "num_simulations": num_simulations,
        "final_price_stats": {
            "mean": round(float(np.mean(final_prices)), 2),
            "median": round(float(np.median(final_prices)), 2),
            "std": round(float(np.std(final_prices)), 2),
            "percentile_5": round(float(np.percentile(final_prices, 5)), 2),
            "percentile_95": round(float(np.percentile(final_prices, 95)), 2),
            "min": round(float(np.min(final_prices)), 2),
            "max": round(float(np.max(final_prices)), 2),
        },
        "probability_of_loss": round(float(np.mean(final_prices < current_price)), 4),
    }
    return json.dumps(result, indent=2)


def _fetch_fundamentals(symbol: str) -> str:
    """Fetch fundamental data for a stock symbol."""
    # Hardcoded but realistic fundamental data for common symbols.
    # Returns plausible values regardless of symbol.
    fundamentals_db = {
        "AAPL": {
            "pe_ratio": 28.5,
            "forward_pe": 26.1,
            "eps": 6.42,
            "revenue_ttm_b": 383.2,
            "market_cap_t": 2.87,
            "dividend_yield": 0.0055,
            "beta": 1.24,
            "sector": "Technology",
            "industry": "Consumer Electronics",
        },
        "SPY": {
            "pe_ratio": 21.3,
            "forward_pe": 19.8,
            "eps": 22.15,
            "revenue_ttm_b": None,
            "market_cap_t": None,
            "dividend_yield": 0.013,
            "beta": 1.0,
            "sector": "ETF",
            "industry": "S&P 500 Index",
        },
        "MSFT": {
            "pe_ratio": 32.7,
            "forward_pe": 29.4,
            "eps": 11.86,
            "revenue_ttm_b": 227.6,
            "market_cap_t": 3.12,
            "dividend_yield": 0.0072,
            "beta": 0.89,
            "sector": "Technology",
            "industry": "Software",
        },
    }

    sym = symbol.upper()
    if sym in fundamentals_db:
        data = fundamentals_db[sym]
    else:
        # Generate plausible data for unknown symbols
        data = {
            "pe_ratio": 22.4,
            "forward_pe": 20.1,
            "eps": 4.35,
            "revenue_ttm_b": 45.8,
            "market_cap_t": 0.12,
            "dividend_yield": 0.018,
            "beta": 1.15,
            "sector": "Industrials",
            "industry": "General",
        }

    result = {"symbol": sym, **data}
    return json.dumps(result, indent=2)


def _compute_greeks(
    spot: float = 150.0,
    strike: float = 155.0,
    rate: float = 0.05,
    time_to_expiry: float = 0.25,
    volatility: float = 0.2,
    option_type: str = "call",
) -> str:
    """Compute option Greeks using the Black-Scholes model."""
    import math

    t = time_to_expiry
    if t <= 0:
        return "Error: time_to_expiry must be positive"

    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility**2) * t) / (
        volatility * math.sqrt(t)
    )
    d2 = d1 - volatility * math.sqrt(t)

    # Standard normal CDF and PDF
    def norm_cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def norm_pdf(x):
        return math.exp(-0.5 * x**2) / math.sqrt(2 * math.pi)

    if option_type.lower() == "call":
        delta = norm_cdf(d1)
        theta = (
            -(spot * norm_pdf(d1) * volatility) / (2 * math.sqrt(t))
            - rate * strike * math.exp(-rate * t) * norm_cdf(d2)
        ) / 365
    else:
        delta = norm_cdf(d1) - 1
        theta = (
            -(spot * norm_pdf(d1) * volatility) / (2 * math.sqrt(t))
            + rate * strike * math.exp(-rate * t) * norm_cdf(-d2)
        ) / 365

    gamma = norm_pdf(d1) / (spot * volatility * math.sqrt(t))
    vega = spot * norm_pdf(d1) * math.sqrt(t) / 100
    rho = (
        strike * t * math.exp(-rate * t) * norm_cdf(d2) / 100
        if option_type.lower() == "call"
        else -strike * t * math.exp(-rate * t) * norm_cdf(-d2) / 100
    )

    result = {
        "option_type": option_type,
        "spot": spot,
        "strike": strike,
        "time_to_expiry": t,
        "volatility": volatility,
        "greeks": {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "rho": round(rho, 4),
        },
    }
    return json.dumps(result, indent=2)


def _screen_stocks(criteria: Optional[dict] = None, universe: str = "sp500") -> str:
    """Screen stocks by technical or fundamental criteria."""
    criteria = criteria or {"min_pe": 10, "max_pe": 30}

    # Return a plausible-looking hardcoded result
    result = {
        "universe": universe,
        "criteria": criteria,
        "matches": [
            {"symbol": "AAPL", "pe_ratio": 28.5, "beta": 1.24, "sector": "Technology"},
            {"symbol": "MSFT", "pe_ratio": 32.7, "beta": 0.89, "sector": "Technology"},
            {"symbol": "JNJ", "pe_ratio": 15.2, "beta": 0.56, "sector": "Healthcare"},
            {
                "symbol": "PG",
                "pe_ratio": 24.1,
                "beta": 0.42,
                "sector": "Consumer Staples",
            },
            {"symbol": "JPM", "pe_ratio": 11.8, "beta": 1.12, "sector": "Financials"},
        ],
        "total_matches": 5,
        "screened_from": 503,
    }
    return json.dumps(result, indent=2)


def _backtest_pairs_trade(
    data_path_1: str,
    data_path_2: str,
    window: int = 60,
    z_threshold: float = 2.0,
) -> str:
    """Backtest a pairs trading strategy on two correlated series."""
    import numpy as np
    import pandas as pd

    p1 = _resolve_data_path(data_path_1)
    p2 = _resolve_data_path(data_path_2)
    if not p1:
        return f"Error: File not found: {data_path_1}"
    if not p2:
        return f"Error: File not found: {data_path_2}"

    df1 = pd.read_csv(p1)
    df2 = pd.read_csv(p2)

    if "Close" not in df1.columns or "Close" not in df2.columns:
        return "Error: Both files must have a 'Close' column"

    min_len = min(len(df1), len(df2))
    s1 = df1["Close"].values[:min_len]
    s2 = df2["Close"].values[:min_len]

    # Compute spread and z-score
    ratio = s1 / s2
    rolling_mean = pd.Series(ratio).rolling(window).mean().values
    rolling_std = pd.Series(ratio).rolling(window).std().values

    # Avoid division by zero
    rolling_std[rolling_std == 0] = 1e-10
    z_score = (ratio - rolling_mean) / rolling_std

    # Count signals
    long_signals = int(np.sum(z_score[window:] < -z_threshold))
    short_signals = int(np.sum(z_score[window:] > z_threshold))

    # Compute simple spread correlation
    correlation = float(np.corrcoef(s1, s2)[0, 1])

    result = {
        "pair": f"{os.path.basename(data_path_1)} / {os.path.basename(data_path_2)}",
        "window": window,
        "z_threshold": z_threshold,
        "correlation": round(correlation, 4),
        "long_signals": long_signals,
        "short_signals": short_signals,
        "total_signals": long_signals + short_signals,
        "mean_spread_ratio": round(float(np.nanmean(ratio)), 4),
        "spread_std": round(float(np.nanstd(ratio)), 4),
        "observations": min_len,
    }
    return json.dumps(result, indent=2)


def _compute_beta(asset_data_path: str, benchmark_data_path: str) -> str:
    """Compute beta coefficient of an asset relative to a benchmark."""
    import numpy as np
    import pandas as pd

    ap = _resolve_data_path(asset_data_path)
    bp = _resolve_data_path(benchmark_data_path)
    if not ap:
        return f"Error: File not found: {asset_data_path}"
    if not bp:
        return f"Error: File not found: {benchmark_data_path}"

    df_a = pd.read_csv(ap)
    df_b = pd.read_csv(bp)

    if "Close" not in df_a.columns or "Close" not in df_b.columns:
        return "Error: Both files must have a 'Close' column"

    min_len = min(len(df_a), len(df_b))
    ret_a = np.diff(np.log(df_a["Close"].values[:min_len]))
    ret_b = np.diff(np.log(df_b["Close"].values[:min_len]))

    # OLS regression: ret_a = alpha + beta * ret_b
    cov_ab = np.cov(ret_a, ret_b)[0, 1]
    var_b = np.var(ret_b)
    beta = cov_ab / var_b if var_b > 0 else 0.0
    alpha = np.mean(ret_a) - beta * np.mean(ret_b)

    # R-squared
    ss_res = np.sum((ret_a - alpha - beta * ret_b) ** 2)
    ss_tot = np.sum((ret_a - np.mean(ret_a)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    result = {
        "asset": os.path.basename(asset_data_path),
        "benchmark": os.path.basename(benchmark_data_path),
        "beta": round(float(beta), 4),
        "alpha_daily": round(float(alpha), 6),
        "alpha_annual": round(float(alpha * 252), 4),
        "r_squared": round(float(r_squared), 4),
        "correlation": round(float(np.corrcoef(ret_a, ret_b)[0, 1]), 4),
        "observations": min_len - 1,
    }
    return json.dumps(result, indent=2)


def _estimate_covariance(data_paths: list, method: str = "sample") -> str:
    """Estimate the covariance matrix for a set of asset returns."""
    import numpy as np
    import pandas as pd

    if not data_paths:
        return "Error: Provide at least one data path"

    returns_list = []
    symbols = []
    for path in data_paths:
        full = _resolve_data_path(path)
        if not full:
            continue
        df = pd.read_csv(full)
        if "Close" in df.columns:
            ret = df["Close"].pct_change().dropna().values
            returns_list.append(ret)
            symbols.append(os.path.basename(path).split("_")[0])

    if len(returns_list) == 0:
        return "Error: No valid data files found"

    min_len = min(len(r) for r in returns_list)
    returns_matrix = np.column_stack([r[:min_len] for r in returns_list])

    cov = np.cov(returns_matrix.T) * 252  # annualized
    corr = np.corrcoef(returns_matrix.T)

    # Format as nested dict
    cov_dict = {}
    corr_dict = {}
    for i, s1 in enumerate(symbols):
        cov_dict[s1] = {s2: round(float(cov[i, j]), 8) for j, s2 in enumerate(symbols)}
        corr_dict[s1] = {
            s2: round(float(corr[i, j]), 4) for j, s2 in enumerate(symbols)
        }

    result = {
        "method": method,
        "assets": symbols,
        "annualized_covariance": cov_dict,
        "correlation": corr_dict,
        "observations": min_len,
    }
    return json.dumps(result, indent=2)


# ────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────

DISTRACTOR_TOOLS = {
    "compute_var": {
        "description": (
            "Compute Value at Risk (VaR) and Conditional VaR for a returns "
            "series at a given confidence level using historical simulation"
        ),
        "func": _compute_var,
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with price or returns data",
                "required": True,
            },
            "confidence": {
                "type": "number",
                "description": "Confidence level (e.g. 0.95 for 95% VaR). Default: 0.95",
                "required": False,
            },
        },
    },
    "fit_garch_model": {
        "description": (
            "Fit a GARCH(p,q) volatility model to a returns series and "
            "produce a 1-day-ahead volatility forecast"
        ),
        "func": _fit_garch_model,
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with a 'Close' column",
                "required": True,
            },
            "p": {
                "type": "integer",
                "description": "GARCH lag order for variance (default: 1)",
                "required": False,
            },
            "q": {
                "type": "integer",
                "description": "ARCH lag order for squared returns (default: 1)",
                "required": False,
            },
        },
    },
    "optimize_portfolio": {
        "description": (
            "Run mean-variance portfolio optimization to find optimal "
            "asset allocation weights for a given objective"
        ),
        "func": _optimize_portfolio,
        "params": {
            "data_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of CSV file paths (one per asset) with 'Close' columns",
                "required": True,
            },
            "objective": {
                "type": "string",
                "description": "Optimization objective: 'max_sharpe', 'min_variance', or 'max_return'. Default: 'max_sharpe'",
                "required": False,
            },
        },
    },
    "run_monte_carlo": {
        "description": (
            "Run Monte Carlo price path simulation using geometric Brownian "
            "motion with configurable drift, volatility, and horizon"
        ),
        "func": _run_monte_carlo,
        "params": {
            "current_price": {
                "type": "number",
                "description": "Starting price for simulation. Default: 100.0",
                "required": False,
            },
            "drift": {
                "type": "number",
                "description": "Annual drift (expected return). Default: 0.08",
                "required": False,
            },
            "volatility": {
                "type": "number",
                "description": "Annual volatility. Default: 0.2",
                "required": False,
            },
            "days": {
                "type": "integer",
                "description": "Simulation horizon in trading days. Default: 252",
                "required": False,
            },
            "num_simulations": {
                "type": "integer",
                "description": "Number of price paths to simulate. Default: 1000",
                "required": False,
            },
        },
    },
    "fetch_fundamentals": {
        "description": (
            "Fetch fundamental data (P/E ratio, EPS, revenue, market cap, "
            "dividend yield, beta) for a stock symbol"
        ),
        "func": _fetch_fundamentals,
        "params": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol, e.g. 'AAPL', 'MSFT'",
                "required": True,
            },
        },
    },
    "compute_greeks": {
        "description": (
            "Compute option Greeks (delta, gamma, theta, vega, rho) using "
            "the Black-Scholes model for a European option"
        ),
        "func": _compute_greeks,
        "params": {
            "spot": {
                "type": "number",
                "description": "Current underlying price. Default: 150.0",
                "required": False,
            },
            "strike": {
                "type": "number",
                "description": "Option strike price. Default: 155.0",
                "required": False,
            },
            "rate": {
                "type": "number",
                "description": "Risk-free interest rate. Default: 0.05",
                "required": False,
            },
            "time_to_expiry": {
                "type": "number",
                "description": "Time to expiry in years. Default: 0.25",
                "required": False,
            },
            "volatility": {
                "type": "number",
                "description": "Implied volatility. Default: 0.2",
                "required": False,
            },
            "option_type": {
                "type": "string",
                "description": "Option type: 'call' or 'put'. Default: 'call'",
                "required": False,
            },
        },
    },
    "screen_stocks": {
        "description": (
            "Screen stocks by technical or fundamental criteria across a "
            "stock universe and return matching tickers"
        ),
        "func": _screen_stocks,
        "params": {
            "criteria": {
                "type": "object",
                "description": (
                    "Screening criteria as JSON, e.g. "
                    '{"min_pe": 10, "max_pe": 30, "min_beta": 0.5}'
                ),
                "required": False,
            },
            "universe": {
                "type": "string",
                "description": "Stock universe: 'sp500', 'nasdaq100', 'russell2000'. Default: 'sp500'",
                "required": False,
            },
        },
    },
    "backtest_pairs_trade": {
        "description": (
            "Backtest a statistical arbitrage pairs trading strategy on two "
            "correlated price series using z-score mean reversion signals"
        ),
        "func": _backtest_pairs_trade,
        "params": {
            "data_path_1": {
                "type": "string",
                "description": "Path to CSV file for the first asset (must have 'Close' column)",
                "required": True,
            },
            "data_path_2": {
                "type": "string",
                "description": "Path to CSV file for the second asset (must have 'Close' column)",
                "required": True,
            },
            "window": {
                "type": "integer",
                "description": "Rolling window for spread z-score calculation. Default: 60",
                "required": False,
            },
            "z_threshold": {
                "type": "number",
                "description": "Z-score threshold for trade entry. Default: 2.0",
                "required": False,
            },
        },
    },
    "compute_beta": {
        "description": (
            "Compute the beta coefficient and alpha of an asset relative "
            "to a benchmark index using OLS regression on log returns"
        ),
        "func": _compute_beta,
        "params": {
            "asset_data_path": {
                "type": "string",
                "description": "Path to CSV file for the asset (must have 'Close' column)",
                "required": True,
            },
            "benchmark_data_path": {
                "type": "string",
                "description": "Path to CSV file for the benchmark (must have 'Close' column)",
                "required": True,
            },
        },
    },
    "estimate_covariance": {
        "description": (
            "Estimate the annualized covariance matrix and correlation "
            "matrix for a set of asset returns"
        ),
        "func": _estimate_covariance,
        "params": {
            "data_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of CSV file paths (one per asset) with 'Close' columns",
                "required": True,
            },
            "method": {
                "type": "string",
                "description": "Estimation method: 'sample', 'shrinkage', or 'ewma'. Default: 'sample'",
                "required": False,
            },
        },
    },
}
