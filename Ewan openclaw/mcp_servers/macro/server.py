"""
MCP-3: Macro Calendar & Global Market Server

Provides 2 tools:
  1. get_macro_calendar    — upcoming economic data releases (next N days)
  2. get_us_market_summary — overnight US indices, commodities, FX

All data collection is pure code (AKShare). No LLM calls.
"""

import logging

import akshare as ak

from ..utils import safe_float

logger = logging.getLogger(__name__)


# ============================================================
# Tool 1: Macro economic calendar
# ============================================================


def get_macro_calendar(days: int = 7) -> list[dict]:
    """
    Fetch recent economic event calendar from Baidu Finance.

    Note: AKShare Baidu calendar provides recent historical events rather than
    a forward-looking schedule. We return the most recent events and let the
    LLM assess relevance.

    Columns: 日期, 时间, 地区, 事件, 公布, 预期, 前值, 重要性
    """
    events = []

    try:
        df = ak.news_economic_baidu()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                event_name = str(row.get("事件", "")).strip()
                if not event_name:
                    continue

                date_val = row.get("日期", "")
                date_str = str(date_val)[:10] if date_val else ""

                importance = row.get("重要性", "")
                # Only keep importance >= 2 to reduce noise
                try:
                    if int(importance) < 2:
                        continue
                except (ValueError, TypeError):
                    pass

                event = {
                    "date": date_str,
                    "time": str(row.get("时间", "")).strip(),
                    "event": event_name,
                    "country": str(row.get("地区", "")).strip(),
                    "importance": str(importance).strip(),
                    "actual": str(row.get("公布", "")).strip(),
                    "forecast": str(row.get("预期", "")).strip(),
                    "previous": str(row.get("前值", "")).strip(),
                }
                events.append(event)
    except Exception as e:
        logger.warning("Failed to fetch macro calendar: %s", e)

    logger.info("Macro calendar: %d events collected", len(events))
    return events


# ============================================================
# Tool 2: US & global market overnight summary
# ============================================================

# Keywords to match in futures_global_spot_em "名称" column
_COMMODITY_KEYWORDS = {
    "COMEX黄金": "Gold (COMEX)",
    "NYMEX原油": "Crude Oil (WTI)",
    "布伦特原油": "Crude Oil (Brent)",
}


def get_us_market_summary() -> dict:
    """
    Fetch overnight US market, commodities, and FX data.

    Returns:
        dict with sections: indices, commodities, fx
    """
    result = {"indices": {}, "commodities": {}, "fx": {}}

    # --- US stock indices + DXY from one index_global_spot_em call ---
    try:
        global_idx = ak.index_global_spot_em()
        if global_idx is not None and not global_idx.empty:
            targets = {
                "Dow Jones": ["道琼斯", "DJI", "Dow"],
                "Nasdaq": ["纳斯达克", "NASDAQ", "Nasdaq"],
                "S&P 500": ["标普500", "S&P", "SPX"],
                "DXY": ["美元指数"],
            }
            for _, row in global_idx.iterrows():
                name = str(row.get("名称", ""))
                for en_name, keywords in targets.items():
                    if any(kw in name for kw in keywords):
                        entry = {
                            "close": safe_float(row.get("最新价")),
                            "change_pct": safe_float(row.get("涨跌幅")),
                        }
                        if en_name == "DXY":
                            result["fx"]["DXY"] = safe_float(row.get("最新价"))
                        else:
                            result["indices"][en_name] = entry
                        break
    except Exception as e:
        logger.warning("Failed to fetch global indices: %s", e)

    # --- Commodities: futures_foreign_hist with exchange symbols (~0.5s each) ---
    for symbol, en_name in [
        ("GC", "Gold (COMEX)"),
        ("CL", "Crude Oil (WTI)"),
        ("SI", "Silver (COMEX)"),
        ("HG", "Copper (COMEX)"),
    ]:
        try:
            df = ak.futures_foreign_hist(symbol=symbol)
            if df is not None and len(df) >= 2:
                last = df.iloc[-1]
                prev = df.iloc[-2]
                price = safe_float(last.get("close"))
                prev_price = safe_float(prev.get("close"))
                change_pct = (
                    round((price - prev_price) / prev_price * 100, 2)
                    if price and prev_price
                    else None
                )
                result["commodities"][en_name] = {
                    "price": price,
                    "change_pct": change_pct,
                }
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", en_name, e)

    # --- FX rates via fx_spot_quote ---
    try:
        fx_df = ak.fx_spot_quote()
        if fx_df is not None and not fx_df.empty:
            for _, row in fx_df.iterrows():
                pair = str(row.get("货币对", ""))
                if pair == "USD/CNY":
                    result["fx"]["USD/CNY"] = safe_float(row.get("买报价"))
                    break
    except Exception as e:
        logger.warning("Failed to fetch FX rates: %s", e)

    logger.info(
        "US market summary: %d indices, %d commodities, %d fx",
        len(result["indices"]),
        len(result["commodities"]),
        len(result["fx"]),
    )
    return result
