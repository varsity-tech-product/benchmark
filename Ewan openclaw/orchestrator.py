"""
Evening Report / Weekly Report / Chat Query Orchestrator

Supports three modes:
  1. daily   — evening report (every day 18:30, non-trading days skip stock data)
  2. weekly  — weekly summary report (Sunday 20:00)
  3. chat    — ad-hoc user queries (stock lookup, news, macro)

Each LLM call reads its model from config/models.yaml.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ---- Paths ----
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
PROMPTS_DIR = BASE_DIR / "prompts"

# ---- MCP server imports ----
sys.path.insert(0, str(BASE_DIR))
from mcp_servers.macro.server import get_macro_calendar, get_us_market_summary
from mcp_servers.market_data.server import (
    get_fund_flow,
    get_market_breadth,
    get_northbound_flow,
    get_sentiment_scores,
    get_stock_fundamentals,
    get_stock_indicators,
    get_stock_realtime,
    get_volume_analysis,
)
from mcp_servers.news.server import get_stock_news, get_telegraph_cls

# ============================================================
# Config loaders
# ============================================================


def load_models_config() -> dict:
    with open(CONFIG_DIR / "models.yaml") as f:
        return yaml.safe_load(f)


def load_portfolio() -> dict:
    with open(CONFIG_DIR / "portfolio.json") as f:
        return json.load(f)


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def get_llm_client(config: dict) -> tuple[AsyncOpenAI, str]:
    """
    Create an LLM client. Returns (client, provider) tuple.

    Resolution order:
      1. OAuth direct to Anthropic API (free with Claude Code Max Plan)
      2. OpenRouter (paid/free models)

    The provider string ("oauth" or "openrouter") determines how model names
    are resolved in _call_llm.
    """
    # --- 1. Try OAuth (Claude Code Max Plan) ---
    try:
        sys.path.insert(0, str(CONFIG_DIR))
        from auth import get_oauth_token

        oauth_token = get_oauth_token()
        if oauth_token:
            logger.info("Using OAuth direct → Anthropic API (free with Max Plan)")
            client = AsyncOpenAI(
                base_url="https://api.anthropic.com/v1/",
                api_key=oauth_token,
            )
            return client, "oauth"
    except Exception as e:
        logger.debug("OAuth not available: %s", e)

    # --- 2. Fallback to OpenRouter ---
    api_key = os.environ.get(config.get("api_key_env", "OPENROUTER_API_KEY"))
    if not api_key:
        raise EnvironmentError(
            f"Missing API key env var: {config.get('api_key_env', 'OPENROUTER_API_KEY')}"
        )
    logger.info("Using OpenRouter API")
    client = AsyncOpenAI(
        base_url=config.get("base_url", "https://openrouter.ai/api/v1"),
        api_key=api_key,
    )
    return client, "openrouter"


# ============================================================
# Trading day check
# ============================================================


def is_trading_day() -> bool:
    """Check if today is an A-share trading day."""
    today = datetime.now()
    if today.weekday() >= 5:
        return False
    try:
        import akshare as ak

        cal = ak.tool_trade_date_hist_sina()
        today_str = today.strftime("%Y-%m-%d")
        if cal is not None and not cal.empty:
            trade_dates = set(str(d)[:10] for d in cal.iloc[:, 0])
            return today_str in trade_dates
    except Exception:
        pass
    return True


# ============================================================
# Phase 1: Data collection (no LLM)
# ============================================================


def collect_data(
    symbols: list[str], trading_day: bool = True, portfolio: dict | None = None
) -> dict:
    """
    Run all MCP tool calls and return aggregated raw data.
    On non-trading days, skip real-time market data (quotes, indicators,
    breadth, volume) — only collect news and macro.
    """
    # Pass full stock info for news relevance filtering (name, keywords, related_stocks)
    portfolio_stocks = portfolio.get("stocks", []) if portfolio else []
    data = {
        "realtime": {},
        "indicators": {},
        "fundamentals": {},
        "breadth": {},
        "volume": {},
        "fund_flow": {},
        "northbound": {},
        "sentiment": {},
        "telegraph": [],
        "stock_news": {},
        "calendar": [],
        "us_market": {},
        "is_trading_day": trading_day,
    }

    def _safe(label, fn, default=None):
        """Run a data-fetch function with error tolerance."""
        try:
            return fn()
        except Exception as e:
            logger.warning("  %s FAILED: %s (continuing with default)", label, e)
            return default if default is not None else {}

    if trading_day:
        logger.info("[Phase 1] Collecting market data (trading day)...")

        data["realtime"] = _safe("Realtime", lambda: get_stock_realtime(symbols))
        logger.info("  Realtime quotes: %d stocks", len(data["realtime"]))

        for sym in symbols:
            data["indicators"][sym] = _safe(
                f"Indicators({sym})", lambda s=sym: get_stock_indicators(s)
            )
        logger.info("  Technical indicators: %d stocks", len(data["indicators"]))

        for sym in symbols:
            data["fundamentals"][sym] = _safe(
                f"Fundamentals({sym})", lambda s=sym: get_stock_fundamentals(s)
            )
        logger.info("  Fundamentals: %d stocks", len(data["fundamentals"]))

        data["breadth"] = _safe("Breadth", get_market_breadth)
        logger.info(
            "  Market breadth: %s up / %s down",
            data["breadth"].get("up", "?"),
            data["breadth"].get("down", "?"),
        )

        data["volume"] = _safe("Volume", get_volume_analysis)
        logger.info("  Volume analysis: %s", data["volume"].get("verdict", "?"))

        data["fund_flow"] = _safe("FundFlow", lambda: get_fund_flow(symbols))
        logger.info("  Fund flow: %d stocks", len(data["fund_flow"]))

        data["northbound"] = _safe("Northbound", get_northbound_flow)
        logger.info("  Northbound flow: %s", list(data["northbound"].keys()))

        data["sentiment"] = _safe("Sentiment", lambda: get_sentiment_scores(symbols))
        logger.info("  Sentiment scores: %d stocks", len(data["sentiment"]))
    else:
        logger.info("[Phase 1] Non-trading day — skipping market data.")

    logger.info("[Phase 1] Collecting news...")
    data["telegraph"] = _safe("Telegraph", get_telegraph_cls, default=[])
    logger.info("  CLS telegraph: %d bulletins", len(data["telegraph"]))

    data["stock_news"] = _safe(
        "StockNews",
        lambda: get_stock_news(
            symbols,
            portfolio_stocks=portfolio_stocks,
            telegraph_items=data["telegraph"],
        ),
    )
    total_news = (
        sum(len(v) for v in data["stock_news"].values())
        if isinstance(data["stock_news"], dict)
        else 0
    )
    logger.info("  Stock news: %d relevant articles", total_news)

    logger.info("[Phase 1] Collecting macro data...")
    data["calendar"] = _safe("Calendar", lambda: get_macro_calendar(days=3), default=[])
    logger.info("  Macro calendar: %d events", len(data["calendar"]))

    data["us_market"] = get_us_market_summary()
    logger.info("  US market: %d indices", len(data["us_market"].get("indices", {})))

    return data


# ============================================================
# Shared LLM call helper
# ============================================================

# OpenRouter model → Anthropic native model name mapping
_OR_TO_NATIVE = {
    "anthropic/claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "anthropic/claude-sonnet-4.6": "claude-sonnet-4-6-20260320",
    "anthropic/claude-opus-4.6": "claude-opus-4-6-20260320",
}

OAUTH_BETA_HEADER = "oauth-2025-04-20"


async def _call_llm(
    client: AsyncOpenAI,
    config: dict,
    model_key: str,
    prompt_name: str,
    template_data: dict,
) -> str:
    """Call a single LLM. Supports both OAuth (Anthropic API) and OpenRouter."""
    model_cfg = config["models"][model_key]
    prompt_template = load_prompt(prompt_name)

    prompt = prompt_template
    for key, val in template_data.items():
        prompt = prompt.replace("{{" + key + "}}", val)

    provider = config.get("_provider", "openrouter")
    model_name = model_cfg["model"]
    temperature = model_cfg.get("temperature", 0.3)
    max_tokens = model_cfg.get("max_tokens", 4000)

    if provider == "oauth":
        # Direct Anthropic API via OAuth (free with Max Plan)
        content = await _call_anthropic_oauth(
            config["_oauth_token"], model_name, prompt, temperature, max_tokens
        )
    else:
        # OpenRouter (OpenAI-compatible)
        response = await client.chat.completions.create(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content

    if not content:
        logger.warning("Empty response from %s (model=%s)", model_key, model_name)
        return f"[{model_key}] No response received."
    return content


async def _call_anthropic_oauth(
    token: str,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Call Anthropic Messages API directly with OAuth token."""
    import httpx

    # Convert OpenRouter model name to native Anthropic name
    native_model = _OR_TO_NATIVE.get(model, model)
    # Strip "anthropic/" prefix if present
    if native_model.startswith("anthropic/"):
        native_model = native_model[len("anthropic/") :]

    async with httpx.AsyncClient(timeout=120) as http:
        resp = await http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": OAUTH_BETA_HEADER,
                "content-type": "application/json",
            },
            json={
                "model": native_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    text = data["content"][0]["text"]
    usage = data.get("usage", {})
    logger.info(
        "  OAuth call: model=%s, in=%d out=%d tokens",
        native_model,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )
    return text


# ============================================================
# Phase 2: Per-dimension LLM analysis (parallel)
# ============================================================


async def run_phase2(
    client: AsyncOpenAI, config: dict, data: dict, portfolio: dict
) -> dict:
    """Run LLM analysis calls in parallel. Skip technical on non-trading days."""
    logger.info("[Phase 2] Running per-dimension LLM analysis...")

    portfolio_str = json.dumps(portfolio["stocks"], ensure_ascii=False, indent=2)
    trading_day = data.get("is_trading_day", True)

    tasks = []
    task_names = []

    # Technical analysis — only on trading days
    if trading_day:
        technical_data = {
            "realtime": json.dumps(data["realtime"], ensure_ascii=False, indent=2),
            "market_breadth": json.dumps(data["breadth"], ensure_ascii=False, indent=2),
            "volume_analysis": json.dumps(data["volume"], ensure_ascii=False, indent=2),
            "indicators": json.dumps(data["indicators"], ensure_ascii=False, indent=2),
            "fund_flow": json.dumps(data["fund_flow"], ensure_ascii=False, indent=2),
            "northbound": json.dumps(data["northbound"], ensure_ascii=False, indent=2),
            "sentiment": json.dumps(data["sentiment"], ensure_ascii=False, indent=2),
        }
        tasks.append(
            _call_llm(client, config, "technical_analysis", "technical", technical_data)
        )
        task_names.append("technical")

    # News analysis — always
    news_data = {
        "telegraph": json.dumps(data["telegraph"], ensure_ascii=False, indent=2),
        "stock_news": json.dumps(data["stock_news"], ensure_ascii=False, indent=2),
        "portfolio": portfolio_str,
    }
    tasks.append(_call_llm(client, config, "news_analysis", "news", news_data))
    task_names.append("news")

    # Macro analysis — always
    macro_data = {
        "calendar": json.dumps(data["calendar"], ensure_ascii=False, indent=2),
        "us_market": json.dumps(data["us_market"], ensure_ascii=False, indent=2),
        "portfolio": portfolio_str,
    }
    tasks.append(_call_llm(client, config, "macro_analysis", "macro", macro_data))
    task_names.append("macro")

    results = await asyncio.gather(*tasks)
    analyses = dict(zip(task_names, results))

    if not trading_day:
        analyses["technical"] = "今日非交易日，无行情数据。"

    logger.info("  Phase 2 done: %s", ", ".join(task_names))
    return analyses


# ============================================================
# Phase 3: Final report generation
# ============================================================


async def run_phase3(
    client: AsyncOpenAI,
    config: dict,
    analyses: dict,
    raw_data: dict,
) -> str:
    """Generate the final evening report from all analyses."""
    logger.info("[Phase 3] Generating final report...")

    raw_summary = {
        "realtime": raw_data["realtime"],
        "fundamentals": raw_data["fundamentals"],
        "breadth": raw_data["breadth"],
        "volume": raw_data["volume"],
        "is_trading_day": raw_data.get("is_trading_day", True),
    }

    template_data = {
        "technical": analyses["technical"],
        "news": analyses["news"],
        "macro": analyses["macro"],
        "raw_data": json.dumps(raw_summary, ensure_ascii=False, indent=2),
    }

    report = await _call_llm(client, config, "report", "report", template_data)
    logger.info("  Report generation: done")
    return report


# ============================================================
# Weekly report
# ============================================================


def collect_weekly_data(symbols: list[str]) -> dict:
    """Collect weekly summary data: 5-day history, weekly news, macro review."""
    logger.info("[Weekly] Collecting weekly data...")

    import akshare as ak

    weekly_quotes = {}
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    for sym in symbols:
        try:
            df = ak.stock_zh_a_hist(
                symbol=sym,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
            if df is not None and not df.empty:
                records = []
                for _, row in df.iterrows():
                    records.append(
                        {
                            "date": str(row.get("日期", "")),
                            "open": float(row.get("开盘", 0)),
                            "close": float(row.get("收盘", 0)),
                            "high": float(row.get("最高", 0)),
                            "low": float(row.get("最低", 0)),
                            "volume": float(row.get("成交量", 0)),
                            "change_pct": float(row.get("涨跌幅", 0)),
                        }
                    )
                weekly_quotes[sym] = records
        except Exception as e:
            logger.warning("Failed to get weekly data for %s: %s", sym, e)
            weekly_quotes[sym] = []

    logger.info("  Weekly quotes: %d stocks", len(weekly_quotes))

    telegraph = get_telegraph_cls()
    stock_news = get_stock_news(symbols)
    calendar = get_macro_calendar(days=7)
    us_market = get_us_market_summary()

    return {
        "weekly_quotes": weekly_quotes,
        "telegraph": telegraph,
        "stock_news": stock_news,
        "calendar": calendar,
        "us_market": us_market,
    }


async def run_weekly_report(client: AsyncOpenAI, config: dict, portfolio: dict) -> str:
    """Generate the weekly summary report."""
    symbols = [s["symbol"] for s in portfolio["stocks"]]
    data = collect_weekly_data(symbols)

    template_data = {
        "weekly_quotes": json.dumps(
            data["weekly_quotes"], ensure_ascii=False, indent=2
        ),
        "telegraph": json.dumps(data["telegraph"], ensure_ascii=False, indent=2),
        "stock_news": json.dumps(data["stock_news"], ensure_ascii=False, indent=2),
        "calendar": json.dumps(data["calendar"], ensure_ascii=False, indent=2),
        "us_market": json.dumps(data["us_market"], ensure_ascii=False, indent=2),
        "portfolio": json.dumps(portfolio["stocks"], ensure_ascii=False, indent=2),
    }

    report = await _call_llm(client, config, "report", "weekly_report", template_data)
    return report


# ============================================================
# Chat handler — ad-hoc queries
# ============================================================


def parse_chat_intent(message: str) -> dict:
    """
    Parse user message into a structured intent.

    Returns:
        {"intent": "stock_query", "symbols": ["002639"]}
        {"intent": "news_query"}
        {"intent": "macro_query"}
        {"intent": "portfolio_show"}
        {"intent": "portfolio_add", "symbol": "...", "name": "..."}
        {"intent": "portfolio_remove", "symbol": "..."}
        {"intent": "general", "message": "..."}
    """
    import re

    msg = message.strip()

    # Portfolio management
    if msg in ("持仓", "portfolio", "我的持仓"):
        return {"intent": "portfolio_show"}

    add_match = re.match(r"(?:add|添加|加入)\s+(\d{6})\s*(.*)", msg, re.IGNORECASE)
    if add_match:
        return {
            "intent": "portfolio_add",
            "symbol": add_match.group(1),
            "name": add_match.group(2).strip() or "Unknown",
        }

    rm_match = re.match(r"(?:remove|删除|移除)\s+(\d{6})", msg, re.IGNORECASE)
    if rm_match:
        return {"intent": "portfolio_remove", "symbol": rm_match.group(1)}

    # Stock query — detect 6-digit code or keywords
    code_match = re.findall(r"\b(\d{6})\b", msg)
    stock_keywords = (
        "查",
        "分析",
        "看看",
        "怎么样",
        "行情",
        "数据",
        "stock",
        "analyze",
    )
    if code_match:
        return {"intent": "stock_query", "symbols": code_match}
    if any(kw in msg for kw in stock_keywords):
        # Try to find stock name in portfolio
        portfolio = load_portfolio()
        for s in portfolio["stocks"]:
            if s["name"] in msg or s.get("name_cn", "") in msg:
                return {"intent": "stock_query", "symbols": [s["symbol"]]}

    # News query
    news_keywords = ("新闻", "消息", "news", "电报", "财联社")
    if any(kw in msg for kw in news_keywords):
        return {"intent": "news_query"}

    # Macro query
    macro_keywords = (
        "宏观",
        "macro",
        "经济",
        "美股",
        "美元",
        "利率",
        "CPI",
        "PMI",
        "日历",
    )
    if any(kw in msg for kw in macro_keywords):
        return {"intent": "macro_query"}

    # Manual report triggers
    if any(kw in msg for kw in ("晚报", "日报", "evening report", "generate report")):
        return {"intent": "daily_report"}
    if any(kw in msg for kw in ("周报", "weekly report", "本周")):
        return {"intent": "weekly_report"}

    return {"intent": "general", "message": msg}


async def handle_chat(message: str, client: AsyncOpenAI, config: dict) -> str:
    """
    Handle an ad-hoc user message. Returns text to send back via Telegram.
    """
    intent = parse_chat_intent(message)
    portfolio = load_portfolio()
    logger.info("[Chat] Intent: %s", intent)

    if intent["intent"] == "portfolio_show":
        lines = ["**当前持仓：**"]
        for s in portfolio["stocks"]:
            lines.append(f"• {s['symbol']} {s['name']} ({s.get('sector', '')})")
        return "\n".join(lines)

    if intent["intent"] == "portfolio_add":
        portfolio["stocks"].append(
            {
                "symbol": intent["symbol"],
                "name": intent["name"],
                "sector": "unknown",
            }
        )
        with open(CONFIG_DIR / "portfolio.json", "w") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)
        return f"已添加 {intent['symbol']} {intent['name']} 到持仓。"

    if intent["intent"] == "portfolio_remove":
        before = len(portfolio["stocks"])
        portfolio["stocks"] = [
            s for s in portfolio["stocks"] if s["symbol"] != intent["symbol"]
        ]
        if len(portfolio["stocks"]) < before:
            with open(CONFIG_DIR / "portfolio.json", "w") as f:
                json.dump(portfolio, f, ensure_ascii=False, indent=2)
            return f"已从持仓移除 {intent['symbol']}。"
        return f"持仓中未找到 {intent['symbol']}。"

    if intent["intent"] == "stock_query":
        symbols = intent["symbols"]
        logger.info("[Chat] Stock query for: %s", symbols)

        realtime = get_stock_realtime(symbols)
        indicators = {}
        for sym in symbols:
            indicators[sym] = get_stock_indicators(sym)
        news = get_stock_news(symbols)

        query_data = {
            "realtime": json.dumps(realtime, ensure_ascii=False, indent=2),
            "indicators": json.dumps(indicators, ensure_ascii=False, indent=2),
            "stock_news": json.dumps(news, ensure_ascii=False, indent=2),
            "query": message,
        }
        return await _call_llm(client, config, "report", "chat_stock", query_data)

    if intent["intent"] == "news_query":
        telegraph = get_telegraph_cls()
        symbols = [s["symbol"] for s in portfolio["stocks"]]
        stock_news = get_stock_news(symbols)

        query_data = {
            "telegraph": json.dumps(telegraph, ensure_ascii=False, indent=2),
            "stock_news": json.dumps(stock_news, ensure_ascii=False, indent=2),
            "query": message,
        }
        return await _call_llm(client, config, "report", "chat_news", query_data)

    if intent["intent"] == "macro_query":
        calendar = get_macro_calendar(days=7)
        us_market = get_us_market_summary()

        query_data = {
            "calendar": json.dumps(calendar, ensure_ascii=False, indent=2),
            "us_market": json.dumps(us_market, ensure_ascii=False, indent=2),
            "query": message,
        }
        return await _call_llm(client, config, "report", "chat_macro", query_data)

    if intent["intent"] == "daily_report":
        return "TRIGGER_DAILY_REPORT"

    if intent["intent"] == "weekly_report":
        return "TRIGGER_WEEKLY_REPORT"

    # General conversation — pass directly to LLM
    query_data = {"query": message}
    return await _call_llm(client, config, "report", "chat_general", query_data)


# ============================================================
# Telegram output
# ============================================================


def save_report(report: str, report_type: str = "daily"):
    """Save report to local file. report_type: 'daily' or 'weekly'."""
    report_dir = BASE_DIR / "report" / report_type
    report_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}_{report_type}.md"
    filepath = report_dir / filename

    filepath.write_text(report, encoding="utf-8")
    logger.info("Report saved to %s", filepath)
    return filepath


def push_to_telegram(report: str, report_type: str = "daily"):
    """Save report locally, then split into Telegram-friendly chunks and print."""
    # Save first
    filepath = save_report(report, report_type)

    # Then push
    TELEGRAM_LIMIT = 4096
    chunks = _split_report(report, TELEGRAM_LIMIT)

    logger.info("[Phase 4] Pushing %d message(s) to Telegram...", len(chunks))
    for i, chunk in enumerate(chunks):
        print(f"\n{'='*60}")
        print(f"[Message {i+1}/{len(chunks)}]")
        print(f"{'='*60}")
        print(chunk)

    logger.info("Report saved at: %s", filepath)


def _split_report(text: str, limit: int) -> list[str]:
    """Split text into chunks, preferring to break at section boundaries."""
    if len(text) <= limit:
        return [text]

    chunks = []
    sections = text.split("\n###")
    current = ""

    for i, section in enumerate(sections):
        prefix = "###" if i > 0 else ""
        candidate = prefix + section

        if len(current) + len(candidate) + 1 <= limit:
            current = current + "\n" + candidate if current else candidate
        else:
            if current:
                chunks.append(current.strip())
            current = candidate

    if current:
        chunks.append(current.strip())

    final = []
    for chunk in chunks:
        while len(chunk) > limit:
            final.append(chunk[:limit])
            chunk = chunk[limit:]
        if chunk:
            final.append(chunk)

    return final


# ============================================================
# Main entry points
# ============================================================


def run_daily():
    """Daily evening report pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_models_config()
    portfolio = load_portfolio()
    symbols = [s["symbol"] for s in portfolio["stocks"]]
    client, provider = get_llm_client(config)
    config["_provider"] = provider
    if provider == "oauth":
        from config.auth import get_oauth_token

        config["_oauth_token"] = get_oauth_token()
    trading_day = is_trading_day()

    logger.info(
        "Generating evening report for %d stocks (trading_day=%s)",
        len(symbols),
        trading_day,
    )

    data = collect_data(symbols, trading_day=trading_day, portfolio=portfolio)

    async def _run():
        analyses = await run_phase2(client, config, data, portfolio)
        report = await run_phase3(client, config, analyses, data)
        return report

    report = asyncio.run(_run())
    push_to_telegram(report)
    logger.info("[Done] Evening report generated and pushed.")


def run_weekly():
    """Weekly summary report pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_models_config()
    portfolio = load_portfolio()
    client, provider = get_llm_client(config)
    config["_provider"] = provider
    if provider == "oauth":
        from config.auth import get_oauth_token

        config["_oauth_token"] = get_oauth_token()

    logger.info("Generating weekly report...")
    report = asyncio.run(run_weekly_report(client, config, portfolio))
    push_to_telegram(report, report_type="weekly")
    logger.info("[Done] Weekly report generated and pushed.")


def run_chat(message: str):
    """Handle a single chat message and print the response."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_models_config()
    client, provider = get_llm_client(config)
    config["_provider"] = provider
    if provider == "oauth":
        from config.auth import get_oauth_token

        config["_oauth_token"] = get_oauth_token()

    response = asyncio.run(handle_chat(message, client, config))

    if response == "TRIGGER_DAILY_REPORT":
        run_daily()
        return
    if response == "TRIGGER_WEEKLY_REPORT":
        run_weekly()
        return

    push_to_telegram(response)


def main():
    """Entry point — dispatch based on CLI argument."""
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "daily":
            run_daily()
        elif mode == "weekly":
            run_weekly()
        elif mode == "chat":
            message = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
            if message:
                run_chat(message)
            else:
                print("Usage: python orchestrator.py chat <message>")
        else:
            print(f"Unknown mode: {mode}. Use: daily | weekly | chat <msg>")
    else:
        # Default: daily report
        run_daily()


if __name__ == "__main__":
    main()
