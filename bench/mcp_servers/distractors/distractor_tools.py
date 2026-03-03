"""Distractor MCP tool schemas for QuantTutorBench.

These tools are presented alongside core tools to test the agent's tool selection ability.
They should NOT be called by the agent. If called, they return an error.
"""

DISTRACTOR_TOOLS = {
    "search_web": {
        "description": "Search the public internet for information. Requires network access.",
        "params": {
            "query": {
                "type": "string",
                "description": "Search query string, e.g. 'Python pandas tutorial'",
                "required": True,
            },
        },
        "error": "Error: No network access available in this sandbox environment.",
    },
    "fetch_live_price": {
        "description": "Fetch the current real-time market price for a symbol via live exchange feed. Requires network access.",
        "params": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol, e.g. 'AAPL'",
                "required": True,
            },
        },
        "error": "Error: No network access. All market data is pre-loaded in /data/.",
    },
    "train_ml_model": {
        "description": "Train a supervised ML model (linear regression, random forest, LSTM) for price prediction. Requires labeled training data with feature and target columns.",
        "params": {
            "model_type": {
                "type": "string",
                "description": "Model type: 'linear_regression', 'random_forest', 'lstm', etc.",
                "required": True,
            },
            "data": {
                "type": "string",
                "description": "Path to training data CSV file",
                "required": True,
            },
        },
        "error": "Error: ML model training is not available for this task.",
    },
    "optimize_portfolio": {
        "description": "Run Markowitz mean-variance portfolio optimization across multiple assets. Requires returns data for multiple securities.",
        "params": {
            "weights": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Initial portfolio weights, e.g. [0.5, 0.3, 0.2]",
                "required": True,
            },
            "objective": {
                "type": "string",
                "description": "Optimization objective: 'max_sharpe', 'min_variance', 'max_return'",
                "required": True,
            },
        },
        "error": "Error: Portfolio optimization is not relevant to this task.",
    },
    "submit_order": {
        "description": "Submit a LIVE trading order to a brokerage for real-money execution. Not for backtesting or educational use.",
        "params": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol, e.g. 'AAPL'",
                "required": True,
            },
            "qty": {
                "type": "integer",
                "description": "Number of shares to trade",
                "required": True,
            },
            "side": {
                "type": "string",
                "description": "Order side: 'buy' or 'sell'",
                "required": True,
            },
        },
        "error": "Error: This is a backtesting environment. Live trading is not available.",
    },
    "fetch_options_chain": {
        "description": "Fetch options chain data (calls/puts, strikes, expiries) for a symbol from a live options exchange feed.",
        "params": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol, e.g. 'AAPL'",
                "required": True,
            },
            "expiry": {
                "type": "string",
                "description": "Option expiry date in YYYY-MM-DD format",
                "required": True,
            },
        },
        "error": "Error: Options data is not available in this environment.",
    },
    "fetch_news_sentiment": {
        "description": "Fetch recent news articles for a ticker and compute NLP-based sentiment scores. Requires internet access to news APIs.",
        "params": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol, e.g. 'AAPL'",
                "required": True,
            },
            "date_range": {
                "type": "string",
                "description": "Date range string, e.g. '2024-01-01 to 2024-03-01'",
                "required": False,
            },
        },
        "error": "Error: News sentiment data is not available.",
    },
    "translate_text": {
        "description": "Translate text to a target language using a translation API. Not related to financial analysis.",
        "params": {
            "text": {
                "type": "string",
                "description": "Text to translate",
                "required": True,
            },
            "target_lang": {
                "type": "string",
                "description": "Target language code, e.g. 'zh', 'es', 'fr'",
                "required": True,
            },
        },
        "error": "Error: Translation service is not available.",
    },
    "get_current_time": {
        "description": "Get the current system date and time. Not needed for historical data analysis.",
        "params": {},
        "error": "Error: Time is frozen in this benchmark environment.",
    },
    "query_database": {
        "description": "Execute a SQL query against a relational database. Data in this environment is file-based (CSV), not in a database.",
        "params": {
            "sql": {
                "type": "string",
                "description": "SQL query string, e.g. 'SELECT * FROM prices WHERE symbol = \"AAPL\"'",
                "required": True,
            },
        },
        "error": "Error: No database is available. Use file-based data in /data/.",
    },
    "send_email": {
        "description": "Send an email notification to an external recipient. Not related to tutoring or data analysis.",
        "params": {
            "to": {
                "type": "string",
                "description": "Recipient email address",
                "required": True,
            },
            "subject": {
                "type": "string",
                "description": "Email subject line",
                "required": True,
            },
            "body": {
                "type": "string",
                "description": "Email body content",
                "required": True,
            },
        },
        "error": "Error: Email service is not available.",
    },
    "generate_image": {
        "description": "Generate an AI image from a text prompt using a generative model. Not related to financial charting — use plot_chart for data visualizations.",
        "params": {
            "prompt": {
                "type": "string",
                "description": "Text description of the image to generate",
                "required": True,
            },
        },
        "error": "Error: Image generation is not available.",
    },
    "fetch_crypto_data": {
        "description": "Fetch cryptocurrency (BTC, ETH) market data from a crypto exchange. This environment focuses on equities — use fetch_market_data for stock data.",
        "params": {
            "symbol": {
                "type": "string",
                "description": "Cryptocurrency symbol, e.g. 'BTC', 'ETH'",
                "required": True,
            },
        },
        "error": "Error: Cryptocurrency data is not available for equity-focused tasks.",
    },
    "run_monte_carlo": {
        "description": "Run Monte Carlo price simulation with configurable parameters. For advanced stochastic modeling, not basic strategy backtesting.",
        "params": {
            "params": {
                "type": "object",
                "description": 'Simulation parameters as JSON object, e.g. {"n_simulations": 10000, "time_horizon": 252}',
                "required": True,
            },
        },
        "error": "Error: Monte Carlo simulation is not needed for this task.",
    },
    "fetch_economic_calendar": {
        "description": "Fetch upcoming economic events (FOMC, NFP, CPI releases) from a macro data provider. Requires internet access.",
        "params": {},
        "error": "Error: Economic calendar data is not available.",
    },
    "compute_var": {
        "description": "Compute Value-at-Risk (VaR) and Conditional VaR (CVaR) for a portfolio using historical simulation or parametric methods.",
        "params": {
            "portfolio": {
                "type": "string",
                "description": "Path to portfolio holdings CSV or JSON file",
                "required": True,
            },
            "confidence": {
                "type": "number",
                "description": "Confidence level, e.g. 0.95 or 0.99",
                "required": False,
            },
        },
        "error": "Error: VaR computation is not available for this task.",
    },
    "backtest_pairs_trade": {
        "description": "Run a statistical pairs trading backtest (cointegration-based) across two correlated securities. Requires price data for both legs.",
        "params": {
            "symbol_a": {
                "type": "string",
                "description": "First ticker symbol, e.g. 'AAPL'",
                "required": True,
            },
            "symbol_b": {
                "type": "string",
                "description": "Second ticker symbol, e.g. 'MSFT'",
                "required": True,
            },
            "lookback": {
                "type": "integer",
                "description": "Lookback period in days for cointegration test",
                "required": False,
            },
        },
        "error": "Error: Pairs trading backtest is not relevant to this task.",
    },
    "fetch_fundamentals": {
        "description": "Fetch fundamental financial data (P/E ratio, EPS, revenue) for a stock from a financial data provider. Requires internet access.",
        "params": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol, e.g. 'AAPL'",
                "required": True,
            },
        },
        "error": "Error: Fundamental data is not available in this environment.",
    },
    "compare_series": {
        "description": "Compare two time series using statistical tests (correlation, Granger causality, cointegration). Requires aligned date ranges.",
        "params": {
            "series_a": {
                "type": "string",
                "description": "Path to first time series CSV",
                "required": True,
            },
            "series_b": {
                "type": "string",
                "description": "Path to second time series CSV",
                "required": True,
            },
        },
        "error": "Error: Series comparison tool is not available for this task.",
    },
}


def call_distractor(tool_name: str, **kwargs) -> str:
    """Call a distractor tool - always returns an error."""
    if tool_name in DISTRACTOR_TOOLS:
        return DISTRACTOR_TOOLS[tool_name]["error"]
    return f"Error: Unknown tool '{tool_name}'"
