"""
MCP-3: Macro Calendar & Global Market Server

Provides 3 tools:
  1. get_macro_calendar       — forward-looking economic calendar (today + next N days)
  2. get_us_market_summary    — overnight US indices, commodities, FX
  3. get_china_macro_snapshot  — key Chinese macro indicators (PMI, CPI, LPR, etc.)

All data collection is pure code (AKShare). No LLM calls.
"""

import logging
from datetime import datetime, timedelta

import akshare as ak

from ..utils import classify, has_data, safe_float

logger = logging.getLogger(__name__)


# ============================================================
# Tool 1: Macro economic calendar
# ============================================================


def get_macro_calendar(days: int = 3) -> list[dict]:
    """
    Fetch economic event calendar for today + next N days.
    Uses news_economic_baidu with specific dates to get forward-looking data.

    Each event includes a 'status' field: 'released' or 'upcoming'.
    """
    events = []
    seen = set()  # Deduplicate by (date, event_name)

    for offset in range(0, days + 1):
        target_date = datetime.now() + timedelta(days=offset)
        target_str = target_date.strftime("%Y%m%d")

        try:
            df = ak.news_economic_baidu(date=target_str)
            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                event_name = str(row.get("事件", "")).strip()
                if not event_name:
                    continue

                date_val = row.get("日期", "")
                date_str = (
                    str(date_val)[:10] if date_val else target_date.strftime("%Y-%m-%d")
                )

                importance = row.get("重要性", "")
                try:
                    if int(importance) < 2:
                        continue
                except (ValueError, TypeError):
                    pass

                dedup_key = (date_str, event_name)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                actual_raw = row.get("公布", "")
                actual = str(actual_raw).strip()
                # NaN / nan / -- / empty all mean "not released yet"
                is_released = bool(
                    actual
                    and actual not in ("--", "nan", "NaN", "")
                    and actual != "None"
                )
                event = {
                    "date": date_str,
                    "time": str(row.get("时间", "")).strip(),
                    "event": event_name,
                    "country": str(row.get("地区", "")).strip(),
                    "importance": str(importance).strip(),
                    "actual": actual if is_released else "",
                    "forecast": str(row.get("预期", "")).strip(),
                    "previous": str(row.get("前值", "")).strip(),
                    "status": "released" if is_released else "upcoming",
                }
                events.append(event)

        except Exception as e:
            logger.warning("Failed to fetch calendar for %s: %s", target_str, e)

    # Sort: upcoming first, then by date
    events.sort(key=lambda e: (e["status"] != "upcoming", e["date"]))
    logger.info(
        "Macro calendar: %d events (%d upcoming)",
        len(events),
        sum(1 for e in events if e["status"] == "upcoming"),
    )
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


# ============================================================
# Tool 3: China macro indicator snapshot
# ============================================================


def get_china_macro_snapshot(
    portfolio_sectors: list[str] | None = None,
) -> dict:
    """
    Fetch key Chinese macro indicators relevant to A-share analysis.
    These update monthly/quarterly, so data is not real-time.

    Returns latest available values for: PMI, CPI, PPI, social financing,
    LPR, Shibor, and sector-specific indices (SOX for semiconductor).
    """
    result = {}

    # 1. PMI — manufacturing activity (monthly, ~1st of month)
    # Note: macro_china_pmi() returns newest-first order
    try:
        df = ak.macro_china_pmi()
        if has_data(df):
            latest = df.iloc[0]  # Newest first
            result["pmi"] = {
                "date": str(latest.get("月份", ""))[:10],
                "manufacturing": safe_float(latest.get("制造业-指数")),
                "non_manufacturing": safe_float(latest.get("非制造业-指数")),
            }
            pmi_val = result["pmi"].get("manufacturing")
            result["pmi"]["assessment"] = (
                classify(
                    pmi_val,
                    [
                        (52, "strong_expansion"),
                        (50, "expansion"),
                        (48, "mild_contraction"),
                    ],
                    default="contraction",
                )
                if pmi_val is not None
                else "unknown"
            )
    except Exception as e:
        logger.warning("PMI fetch failed: %s", e)

    # 2. CPI — inflation (monthly, ~10th)
    # Note: macro_china_cpi() returns newest-first order
    try:
        df = ak.macro_china_cpi()
        if has_data(df):
            latest = df.iloc[0]
            result["cpi"] = {
                "date": str(latest.get("月份", ""))[:10],
                "yoy": safe_float(latest.get("全国-同比增长")),
            }
    except Exception as e:
        logger.warning("CPI fetch failed: %s", e)

    # 3. PPI — producer prices (monthly, ~10th)
    # Note: macro_china_ppi() returns newest-first order
    try:
        df = ak.macro_china_ppi()
        if has_data(df):
            latest = df.iloc[0]
            result["ppi"] = {
                "date": str(latest.get("月份", ""))[:10],
                "yoy": safe_float(latest.get("当月同比增长")),
            }
    except Exception as e:
        logger.warning("PPI fetch failed: %s", e)

    # 4. Social financing — credit impulse (monthly, ~10th-15th)
    # Note: macro_china_shrzgm() returns oldest-first order
    try:
        df = ak.macro_china_shrzgm()
        if has_data(df):
            latest = df.iloc[-1]  # Oldest first, so last = newest
            result["social_financing"] = {
                "date": str(latest.get("月份", ""))[:10],
                "value_billion": safe_float(latest.get("社会融资规模增量")),
            }
    except Exception as e:
        logger.warning("Social financing fetch failed: %s", e)

    # 5. LPR — interest rate anchor (monthly, 20th)
    # Note: oldest-first, cols = TRADE_DATE, LPR1Y, LPR5Y
    try:
        df = ak.macro_china_lpr()
        if has_data(df):
            latest = df.iloc[-1]
            result["lpr"] = {
                "date": str(latest.get("TRADE_DATE", ""))[:10],
                "lpr_1y": safe_float(latest.get("LPR1Y")),
                "lpr_5y": safe_float(latest.get("LPR5Y")),
            }
    except Exception as e:
        logger.warning("LPR fetch failed: %s", e)

    # 6. Shibor — overnight interbank rate (daily)
    # Note: oldest-first, cols = 日期, O/N-定价, 1W-定价, ...
    try:
        df = ak.macro_china_shibor_all()
        if has_data(df):
            latest = df.iloc[-1]
            result["shibor"] = {
                "date": str(latest.get("日期", ""))[:10],
                "overnight": safe_float(
                    latest.get("O/N-定价"),
                    decimals=4,
                ),
                "1w": safe_float(
                    latest.get("1W-定价"),
                    decimals=4,
                ),
            }
    except Exception as e:
        logger.warning("Shibor fetch failed: %s", e)

    # 7. Philadelphia Semiconductor Index (daily) — for semiconductor holdings
    sectors = set(portfolio_sectors or [])
    if not sectors or "semiconductor" in sectors or "it_services" in sectors:
        try:
            df = ak.macro_global_sox_index()
            if has_data(df):
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) >= 2 else None
                price = safe_float(latest.iloc[-1] if len(latest) > 0 else None)
                prev_price = safe_float(
                    prev.iloc[-1] if prev is not None and len(prev) > 0 else None
                )
                change_pct = (
                    round((price - prev_price) / prev_price * 100, 2)
                    if price and prev_price
                    else None
                )
                result["sox_index"] = {
                    "price": price,
                    "change_pct": change_pct,
                }
        except Exception as e:
            logger.warning("SOX index fetch failed: %s", e)

    logger.info("China macro snapshot: %d indicators", len(result))
    return result
