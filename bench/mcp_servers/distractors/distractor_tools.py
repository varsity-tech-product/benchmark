"""Distractor MCP tool schemas for QuantTutorBench.

These tools are presented alongside core tools to test the agent's tool selection ability.
They should NOT be called by the agent. If called, they return an error.
"""

DISTRACTOR_TOOLS = {
    "search_web": {
        "description": "Search the web for information",
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
        "description": "Fetch the current live market price for a symbol",
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
        "description": "Train a machine learning model on data",
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
        "description": "Run portfolio optimization with given weights and objective",
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
        "description": "Submit a live trading order",
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
        "description": "Fetch options chain data for a symbol",
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
        "description": "Fetch news sentiment analysis for a symbol",
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
        "description": "Translate text to a target language",
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
        "description": "Get the current date and time",
        "params": {},
        "error": "Error: Time is frozen in this benchmark environment.",
    },
    "query_database": {
        "description": "Execute a SQL query against the database",
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
        "description": "Send an email notification",
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
        "description": "Generate an image from a text prompt",
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
        "description": "Fetch cryptocurrency market data",
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
        "description": "Run Monte Carlo simulation with given parameters",
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
        "description": "Fetch upcoming economic events and releases",
        "params": {},
        "error": "Error: Economic calendar data is not available.",
    },
}


def call_distractor(tool_name: str, **kwargs) -> str:
    """Call a distractor tool - always returns an error."""
    if tool_name in DISTRACTOR_TOOLS:
        return DISTRACTOR_TOOLS[tool_name]["error"]
    return f"Error: Unknown tool '{tool_name}'"
