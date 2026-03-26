"""
MCP-1: Market Data Server

Provides tools for A-share market data and technical analysis.
All computations are pure Python/pandas — no LLM calls.

Tools:
  1. get_stock_realtime      — latest price, change, volume for given symbols
  2. get_stock_indicators    — full technical indicator set for one symbol
  3. get_stock_fundamentals  — PE/PB/ROE and basic financials
  4. get_market_breadth      — all-A rising/falling counts and sentiment
  5. get_volume_analysis     — market-wide turnover vs historical averages
  6. get_fund_flow           — per-stock main capital flow (主力资金)
  7. get_northbound_flow     — northbound capital summary (北向资金)
  8. get_sentiment_scores    — institutional participation + composite score
"""

import logging
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

from ..utils import classify, coerce_numeric, has_data, safe_float, safe_int
from .indicators import compute_all_indicators

logger = logging.getLogger(__name__)


# ============================================================
# Tool 1: Real-time (latest) stock quotes
# ============================================================

_HIST_COL_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "turnover",
    "换手率": "turnover_rate",
    "涨跌幅": "change_pct",
    "涨跌额": "change_amt",
    "股票代码": "code",
}


def _market_prefix(symbol: str) -> str:
    """Return 'sh' or 'sz' prefix for sina-style symbol."""
    return "sh" + symbol if symbol.startswith("6") else "sz" + symbol


def get_stock_realtime(symbols: list[str]) -> dict:
    """
    Fetch latest quote for given A-share symbols.
    Primary: stock_zh_a_hist (eastmoney). Fallback: stock_zh_a_daily (sina).

    Returns:
        dict keyed by symbol with close, open, high, low, volume, etc.
    """
    today = datetime.now().strftime("%Y%m%d")
    result = {}

    for sym in symbols:
        # Try eastmoney first
        try:
            df = ak.stock_zh_a_hist(
                symbol=sym,
                period="daily",
                start_date=today,
                end_date=today,
                adjust="qfq",
            )
            if df is not None and not df.empty:
                df = df.rename(columns=_HIST_COL_MAP)
                row = df.iloc[-1]
                result[sym] = {
                    "close": safe_float(row.get("close")),
                    "open": safe_float(row.get("open")),
                    "high": safe_float(row.get("high")),
                    "low": safe_float(row.get("low")),
                    "change_pct": safe_float(row.get("change_pct")),
                    "change_amt": safe_float(row.get("change_amt")),
                    "volume": safe_int(row.get("volume")),
                    "turnover": safe_float(row.get("turnover")),
                    "turnover_rate": safe_float(row.get("turnover_rate")),
                }
                continue
        except Exception:
            pass

        # Fallback: sina source
        try:
            sina_sym = _market_prefix(sym)
            df = ak.stock_zh_a_daily(symbol=sina_sym, adjust="qfq")
            if df is not None and not df.empty:
                row = df.iloc[-1]
                prev_close = float(df.iloc[-2]["close"]) if len(df) >= 2 else None
                close_val = safe_float(row.get("close"))
                change_pct = (
                    round((close_val - prev_close) / prev_close * 100, 2)
                    if close_val and prev_close
                    else None
                )
                result[sym] = {
                    "close": close_val,
                    "open": safe_float(row.get("open")),
                    "high": safe_float(row.get("high")),
                    "low": safe_float(row.get("low")),
                    "change_pct": change_pct,
                    "change_amt": (
                        round(close_val - prev_close, 2)
                        if close_val and prev_close
                        else None
                    ),
                    "volume": safe_int(row.get("volume")),
                    "turnover": safe_float(row.get("amount")),
                    "turnover_rate": safe_float(row.get("turnover")),
                    "source": "sina_fallback",
                }
                logger.info("  %s: used sina fallback", sym)
                continue
        except Exception as e2:
            logger.warning(
                "Failed to fetch realtime for %s (both sources): %s", sym, e2
            )

        result[sym] = {"error": "no data from any source"}

    logger.info("Realtime quotes fetched for %d/%d symbols", len(result), len(symbols))
    return result


# ============================================================
# Tool 2: Full technical indicators for one symbol
# ============================================================


def get_stock_indicators(symbol: str, period: str = "daily") -> dict:
    """
    Compute all technical indicators for a single stock.
    Primary: stock_zh_a_hist (eastmoney). Fallback: stock_zh_a_daily (sina).
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")

    df = None
    # Try eastmoney
    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
    except Exception:
        pass

    # Fallback: sina (only supports daily, no period param)
    if not has_data(df):
        try:
            sina_sym = _market_prefix(symbol)
            df = ak.stock_zh_a_daily(symbol=sina_sym, adjust="qfq")
            if has_data(df):
                # sina columns: date,open,high,low,close,volume,amount,outstanding_share,turnover
                df = df.rename(
                    columns={"amount": "turnover", "turnover": "turnover_rate"}
                )
                logger.info("  %s indicators: used sina fallback", symbol)
        except Exception as e:
            logger.warning(
                "Failed to fetch indicators for %s (both sources): %s", symbol, e
            )

    if not has_data(df):
        return {"error": f"no data for {symbol}"}

    df = df.rename(columns=_HIST_COL_MAP)
    coerce_numeric(df, ["open", "close", "high", "low", "volume"])
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    indicators = compute_all_indicators(df)
    indicators["symbol"] = symbol
    logger.info("Indicators computed for %s (%d bars)", symbol, len(df))
    return indicators


# ============================================================
# Tool 3: Fundamental data for one symbol
# ============================================================

_FUNDAMENTAL_KEY_MAP = {
    "总市值": "total_market_cap",
    "流通市值": "circulating_market_cap",
    "市盈率(动态)": "pe_ttm",
    "市净率": "pb",
}


def get_stock_fundamentals(symbol: str) -> dict:
    """
    Fetch basic fundamental data for a single stock.

    Args:
        symbol: 6-digit A-share code

    Returns:
        dict with PE, PB, market cap, ROE, revenue growth, etc.
    """
    try:
        df = ak.stock_individual_info_em(symbol=symbol)
    except Exception as e:
        logger.warning("Failed to fetch fundamentals for %s: %s", symbol, e)
        return {"error": str(e)}

    if not has_data(df):
        return {"error": f"no fundamental data for {symbol}"}

    info = {}
    for _, row in df.iterrows():
        key = str(row.iloc[0]).strip()
        info[key] = row.iloc[1]

    result = {"symbol": symbol}
    for cn_key, en_key in _FUNDAMENTAL_KEY_MAP.items():
        if cn_key in info:
            result[en_key] = safe_float(info[cn_key])

    return result


# ============================================================
# Tool 3b: Extended fundamental data (financial ratios + shareholders + ratings)
# ============================================================


def get_stock_fundamentals_extended(symbol: str) -> dict:
    """
    Fetch extended fundamentals: financial ratios, top shareholders,
    and analyst ratings.

    Supplements get_stock_fundamentals() which only provides PE/PB/market cap.
    Called once per stock per day for the evening report.
    """
    result = {"symbol": symbol}

    # 1. Core financial ratios (Sina source — richest single-call function)
    try:
        df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year="2023")
        if has_data(df):
            latest = df.iloc[0]  # Most recent period first
            result["report_period"] = str(latest.get("日期", ""))[:10]
            result["roe"] = safe_float(latest.get("净资产收益率(%)"))
            result["roa"] = safe_float(latest.get("总资产收益率(%)"))
            result["gross_margin"] = safe_float(latest.get("销售毛利率(%)"))
            result["net_margin"] = safe_float(latest.get("销售净利率(%)"))
            result["debt_ratio"] = safe_float(latest.get("资产负债率(%)"))
            result["current_ratio"] = safe_float(latest.get("流动比率"))
            result["eps"] = safe_float(latest.get("基本每股收益(元)"), decimals=4)
            result["bps"] = safe_float(latest.get("每股净资产_调整后(元)"))
            result["ocf_per_share"] = safe_float(latest.get("每股经营性现金流(元)"))
            result["revenue_growth"] = safe_float(latest.get("主营业务收入增长率(%)"))
            result["profit_growth"] = safe_float(latest.get("净利润增长率(%)"))

            # Health classification for LLM
            roe = result.get("roe")
            result["roe_grade"] = (
                classify(
                    roe,
                    [
                        (20, "excellent"),
                        (15, "good"),
                        (10, "average"),
                        (5, "below_average"),
                    ],
                    default="poor",
                )
                if roe is not None
                else "unknown"
            )

            debt = result.get("debt_ratio")
            result["debt_grade"] = (
                classify(
                    debt,
                    [
                        (70, "high_leverage"),
                        (50, "moderate"),
                        (30, "conservative"),
                    ],
                    default="very_conservative",
                )
                if debt is not None
                else "unknown"
            )
    except Exception as e:
        logger.warning("Financial ratios failed for %s: %s", symbol, e)

    # 2. Top 5 free-float shareholders (latest quarter, Sina source)
    try:
        df = ak.stock_circulate_stock_holder(symbol=symbol)
        if has_data(df):
            # Get the latest report date
            latest_date = df["截止日期"].iloc[0]
            latest_df = df[df["截止日期"] == latest_date].head(5)
            holders = []
            for _, row in latest_df.iterrows():
                holders.append(
                    {
                        "name": str(row.get("股东名称", ""))[:20],
                        "ratio": safe_float(row.get("占流通股比例")),
                        "type": str(row.get("股本性质", "")).strip(),
                    }
                )
            result["top_shareholders"] = holders
            result["shareholder_report_date"] = str(latest_date)[:10]
    except Exception as e:
        logger.warning("Shareholders failed for %s: %s", symbol, e)

    # 3. Latest analyst ratings + target price
    try:
        df = ak.stock_research_report_em(symbol=symbol)
        if has_data(df):
            recent = df.head(3)  # Latest 3 reports
            ratings = []
            for _, row in recent.iterrows():
                ratings.append(
                    {
                        "institution": str(row.get("机构", ""))[:10],
                        "rating": str(row.get("评级", "")).strip(),
                        "target_price": safe_float(row.get("目标价")),
                        "date": str(row.get("日期", ""))[:10],
                    }
                )
            result["analyst_ratings"] = ratings
    except Exception as e:
        logger.warning("Analyst ratings failed for %s: %s", symbol, e)

    logger.info("Extended fundamentals for %s: %d fields", symbol, len(result))
    return result


# ============================================================
# Tool 4: All-A market breadth (rising/falling counts)
# ============================================================


def get_market_breadth() -> dict:
    """
    Fetch market breadth using stock_market_activity_legu (~1.4s).
    Returns up/down/flat counts, limit-up/down counts, and sentiment.
    """
    try:
        df = ak.stock_market_activity_legu()
    except Exception as e:
        logger.warning("Failed to fetch market breadth: %s", e)
        return {"error": str(e)}

    # Parse into a lookup dict: {"上涨": 877.0, "涨停": 52.0, ...}
    lookup = {}
    for _, row in df.iterrows():
        lookup[str(row["item"]).strip()] = row["value"]

    up = int(lookup.get("上涨", 0))
    down = int(lookup.get("下跌", 0))
    flat = int(lookup.get("平盘", 0))
    limit_up = int(lookup.get("涨停", 0))
    limit_down = int(lookup.get("跌停", 0))
    real_limit_up = int(lookup.get("真实涨停", 0))
    real_limit_down = int(lookup.get("真实跌停", 0))
    total = up + down + flat

    up_down_ratio = round(up / down, 2) if down > 0 else float("inf")

    sentiment = classify(
        up_down_ratio,
        [
            (3.0, "strongly_bullish"),
            (1.5, "bullish"),
            (0.8, "neutral"),
            (0.33, "bearish"),
        ],
        default="strongly_bearish",
    )

    logger.info("Market breadth: %d up / %d down, sentiment=%s", up, down, sentiment)

    return {
        "total": total,
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "real_limit_up": real_limit_up,
        "real_limit_down": real_limit_down,
        "up_down_ratio": up_down_ratio,
        "limit_diff": limit_up - limit_down,
        "sentiment": sentiment,
    }


# ============================================================
# Tool 5: Market-wide volume analysis vs historical averages
# ============================================================


def get_volume_analysis() -> dict:
    """
    Analyze today's total A-share turnover against historical averages.
    Compares with 5d/20d/60d moving averages and computes 1-year percentile.
    Also includes yesterday's turnover for direct comparison.
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")

    try:
        sh = ak.stock_zh_index_daily_em(
            symbol="sh000001", start_date=start_date, end_date=end_date
        )
        sz = ak.stock_zh_index_daily_em(
            symbol="sz399001", start_date=start_date, end_date=end_date
        )
    except Exception as e:
        logger.warning("Failed to fetch index data for volume analysis: %s", e)
        return {"error": str(e)}

    def _get_turnover(idx_df: pd.DataFrame) -> pd.DataFrame:
        col_map = {"date": "date", "日期": "date", "成交额": "turnover"}
        idx_df = idx_df.rename(columns=col_map)
        if "turnover" not in idx_df.columns:
            for col in idx_df.columns:
                if "额" in str(col) or "amount" in str(col).lower():
                    idx_df = idx_df.rename(columns={col: "turnover"})
                    break
        idx_df["turnover"] = pd.to_numeric(idx_df["turnover"], errors="coerce")
        return idx_df[["date", "turnover"]].dropna()

    sh_t = _get_turnover(sh)
    sz_t = _get_turnover(sz)

    merged = pd.merge(sh_t, sz_t, on="date", suffixes=("_sh", "_sz"))
    merged["total"] = merged["turnover_sh"] + merged["turnover_sz"]
    merged = merged.sort_values("date").reset_index(drop=True)

    if len(merged) < 10:
        return {"error": "insufficient historical data"}

    today_amount = float(merged["total"].iloc[-1])
    yesterday_amount = float(merged["total"].iloc[-2]) if len(merged) >= 2 else None
    avg_5d = float(merged["total"].iloc[-6:-1].mean())
    avg_20d = float(merged["total"].tail(21).head(20).mean())
    avg_60d = float(merged["total"].tail(61).head(60).mean())

    vs_5d = round(today_amount / avg_5d, 2) if avg_5d > 0 else None
    vs_20d = round(today_amount / avg_20d, 2) if avg_20d > 0 else None
    vs_60d = round(today_amount / avg_60d, 2) if avg_60d > 0 else None
    vs_yesterday = (
        round(today_amount / yesterday_amount, 2)
        if yesterday_amount and yesterday_amount > 0
        else None
    )

    one_year = merged.tail(252)
    percentile = None
    if len(one_year) > 10:
        percentile = int(
            (one_year["total"] <= today_amount).sum() / len(one_year) * 100
        )

    verdict = classify(
        vs_5d,
        [
            (2.0, "extreme_volume"),
            (1.5, "heavy_volume"),
            (1.2, "moderate_increase"),
            (0.8, "normal"),
            (0.5, "light_volume"),
        ],
        default="extreme_shrink" if vs_5d is not None else "unknown",
    )

    logger.info("Volume analysis: vs_5d=%.2f, verdict=%s", vs_5d or 0, verdict)

    return {
        "today_amount": round(today_amount, 0),
        "yesterday_amount": round(yesterday_amount, 0) if yesterday_amount else None,
        "avg_5d": round(avg_5d, 0),
        "avg_20d": round(avg_20d, 0),
        "avg_60d": round(avg_60d, 0),
        "vs_yesterday": vs_yesterday,
        "vs_5d": vs_5d,
        "vs_20d": vs_20d,
        "vs_60d": vs_60d,
        "percentile_1y": percentile,
        "verdict": verdict,
    }


# ============================================================
# Tool 6: Per-stock main capital flow (主力资金流向)
# ============================================================


def get_fund_flow(symbols: list[str]) -> dict:
    """
    Fetch per-stock capital flow data (main/super-large/large/medium/small).
    Returns today's and recent days' net inflow from stock_individual_fund_flow.
    """
    result = {}
    for sym in symbols:
        market = "sh" if sym.startswith("6") else "sz"
        try:
            df = ak.stock_individual_fund_flow(stock=sym, market=market)
            if df is None or df.empty:
                result[sym] = {"error": "no data"}
                continue

            last = df.iloc[-1]
            # Also compute 3-day and 5-day cumulative main flow
            main_3d = (
                float(df["主力净流入-净额"].tail(3).sum()) if len(df) >= 3 else None
            )
            main_5d = (
                float(df["主力净流入-净额"].tail(5).sum()) if len(df) >= 5 else None
            )

            result[sym] = {
                "date": str(last.get("日期", ""))[:10],
                "main_net_inflow": safe_float(last.get("主力净流入-净额")),
                "main_net_pct": safe_float(last.get("主力净流入-净占比")),
                "super_large_net": safe_float(last.get("超大单净流入-净额")),
                "super_large_pct": safe_float(last.get("超大单净流入-净占比")),
                "large_net": safe_float(last.get("大单净流入-净额")),
                "large_pct": safe_float(last.get("大单净流入-净占比")),
                "small_net": safe_float(last.get("小单净流入-净额")),
                "small_pct": safe_float(last.get("小单净流入-净占比")),
                "main_3d_cumulative": round(main_3d, 0) if main_3d else None,
                "main_5d_cumulative": round(main_5d, 0) if main_5d else None,
            }
        except Exception as e:
            logger.warning("Failed to fetch fund flow for %s: %s", sym, e)
            result[sym] = {"error": str(e)}

    logger.info("Fund flow fetched for %d symbols", len(result))
    return result


# ============================================================
# Tool 7: Northbound capital summary (北向资金)
# ============================================================


def get_northbound_flow() -> dict:
    """
    Fetch today's northbound + southbound capital flow summary.
    Columns: 交易日,类型,板块,资金方向,交易状态,成交净买额,资金净流入,当日资金余额,上涨数,持平数,下跌数,相关指数,指数涨跌幅
    """
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is None or df.empty:
            return {"error": "no data"}
    except Exception as e:
        logger.warning("Failed to fetch northbound flow: %s", e)
        return {"error": str(e)}

    result = {"northbound": {}, "southbound": {}}
    for _, row in df.iterrows():
        direction = str(row.get("资金方向", ""))
        board = str(row.get("板块", ""))
        entry = {
            "net_buy": safe_float(row.get("成交净买额")),
            "net_inflow": safe_float(row.get("资金净流入")),
            "up_count": safe_int(row.get("上涨数")),
            "down_count": safe_int(row.get("下跌数")),
            "index_change_pct": safe_float(row.get("指数涨跌幅")),
        }
        if direction == "北向":
            result["northbound"][board] = entry
        elif direction == "南向":
            result["southbound"][board] = entry

    logger.info(
        "HSGT flow: northbound=%s, southbound=%s",
        list(result["northbound"].keys()),
        list(result["southbound"].keys()),
    )
    return result


# ============================================================
# Tool 8: Sentiment scores (机构参与度 + 综合评分)
# ============================================================


def get_sentiment_scores(symbols: list[str]) -> dict:
    """
    Fetch per-stock sentiment metrics:
      - institutional_participation (机构参与度)
      - composite_score (综合评分)
    """
    result = {}
    for sym in symbols:
        entry = {}

        # Institutional participation
        try:
            df = ak.stock_comment_detail_zlkp_jgcyd_em(symbol=sym)
            if df is not None and not df.empty:
                entry["institutional_participation"] = safe_float(df.iloc[-1].iloc[-1])
        except Exception as e:
            logger.warning("Failed to fetch inst. participation for %s: %s", sym, e)

        # Composite score
        try:
            df = ak.stock_comment_detail_zhpj_lspf_em(symbol=sym)
            if df is not None and not df.empty:
                entry["composite_score"] = safe_float(df.iloc[-1].iloc[-1])
        except Exception as e:
            logger.warning("Failed to fetch composite score for %s: %s", sym, e)

        result[sym] = entry

    logger.info("Sentiment scores fetched for %d symbols", len(result))
    return result
