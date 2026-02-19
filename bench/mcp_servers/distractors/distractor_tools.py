"""Distractor MCP tool schemas for QuantTutorBench.

These tools are presented alongside core tools to test the agent's tool selection ability.
They should NOT be called by the agent. If called, they return an error.
"""

DISTRACTOR_TOOLS = {
    "search_web": {
        "description": "Search the web for information",
        "params": {"query": "str"},
        "error": "Error: No network access available in this sandbox environment.",
    },
    "fetch_live_price": {
        "description": "Fetch the current live market price for a symbol",
        "params": {"symbol": "str"},
        "error": "Error: No network access. All market data is pre-loaded in /data/.",
    },
    "train_ml_model": {
        "description": "Train a machine learning model on data",
        "params": {"model_type": "str", "data": "str"},
        "error": "Error: ML model training is not available for this task.",
    },
    "optimize_portfolio": {
        "description": "Run portfolio optimization with given weights and objective",
        "params": {"weights": "list", "objective": "str"},
        "error": "Error: Portfolio optimization is not relevant to this task.",
    },
    "submit_order": {
        "description": "Submit a live trading order",
        "params": {"symbol": "str", "qty": "int", "side": "str"},
        "error": "Error: This is a backtesting environment. Live trading is not available.",
    },
    "fetch_options_chain": {
        "description": "Fetch options chain data for a symbol",
        "params": {"symbol": "str", "expiry": "str"},
        "error": "Error: Options data is not available in this environment.",
    },
    "fetch_news_sentiment": {
        "description": "Fetch news sentiment analysis for a symbol",
        "params": {"symbol": "str", "date_range": "str"},
        "error": "Error: News sentiment data is not available.",
    },
    "translate_text": {
        "description": "Translate text to a target language",
        "params": {"text": "str", "target_lang": "str"},
        "error": "Error: Translation service is not available.",
    },
    "get_current_time": {
        "description": "Get the current date and time",
        "params": {},
        "error": "Error: Time is frozen in this benchmark environment.",
    },
    "query_database": {
        "description": "Execute a SQL query against the database",
        "params": {"sql": "str"},
        "error": "Error: No database is available. Use file-based data in /data/.",
    },
    "send_email": {
        "description": "Send an email notification",
        "params": {"to": "str", "subject": "str", "body": "str"},
        "error": "Error: Email service is not available.",
    },
    "generate_image": {
        "description": "Generate an image from a text prompt",
        "params": {"prompt": "str"},
        "error": "Error: Image generation is not available.",
    },
    "fetch_crypto_data": {
        "description": "Fetch cryptocurrency market data",
        "params": {"symbol": "str"},
        "error": "Error: Cryptocurrency data is not available for equity-focused tasks.",
    },
    "run_monte_carlo": {
        "description": "Run Monte Carlo simulation with given parameters",
        "params": {"params": "dict"},
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
