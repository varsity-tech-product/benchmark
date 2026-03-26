"""
MCP-2: News Collection Server

Four-layer news architecture:
  L0: Company announcements (公告) — most authoritative first-hand disclosures
  L1: Direct news   — stock_news_em + stock_news_main_cx, filtered by stock name
  L2: Investor Q&A  — stock_irm_cninfo (深市) + stock_sns_sseinfo (沪市)
  L3: Related news  — CLS telegraph + Caixin pool, matched by related_keywords

All data collection is pure code (AKShare + scraping). No LLM calls.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

from .scraper import fetch_many

logger = logging.getLogger(__name__)


# ============================================================
# Tool 1: CLS Telegraph bulletins
# ============================================================


def get_telegraph_cls() -> list[dict]:
    """Fetch today's CLS (Cailianshe) telegraph bulletins."""
    try:
        df = ak.stock_info_global_cls()
    except Exception as e:
        logger.warning("Failed to fetch CLS telegraph: %s", e)
        return [{"error": str(e)}]

    if df is None or df.empty:
        return []

    col_map = {
        "内容": "content",
        "标题": "title",
        "发布时间": "time",
        "发布日期": "date",
    }
    df = df.rename(columns=col_map)

    if "content" not in df.columns:
        return [{"error": "unable to parse CLS telegraph columns"}]

    results = []
    for _, row in df.iterrows():
        content = str(row.get("content", "")).strip()
        time_str = str(row.get("time", "")).strip()
        if not content or _is_irrelevant(content):
            continue
        results.append({"time": time_str, "content": content})

    logger.info("CLS telegraph: %d bulletins collected", len(results))
    return results


# ============================================================
# Tool 2: Per-stock news (3-layer)
# ============================================================


def get_stock_news(
    symbols: list[str],
    portfolio_stocks: list[dict] | None = None,
    telegraph_items: list[dict] | None = None,
) -> dict:
    """
    Fetch news for each stock using 3-layer architecture.

    Args:
        symbols: list of 6-digit codes
        portfolio_stocks: list of stock dicts from portfolio.json
        telegraph_items: pre-fetched CLS telegraph for L3 matching

    Returns:
        dict keyed by symbol with categorized news items.
    """
    # Build lookup from portfolio
    stock_info = {}
    if portfolio_stocks:
        for s in portfolio_stocks:
            stock_info[s["symbol"]] = {
                "name": s.get("name", ""),
                "keywords": set(s.get("related_keywords", [])),
                "related_stocks": set(s.get("related_stocks", [])),
            }

    # ---- Shared news pools (fetch once) ----
    caixin_pool = _fetch_caixin_news()

    # ---- L1: Direct per-stock news from EastMoney ----
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _fetch_em_news_async(symbols))
            em_result = future.result()
    else:
        em_result = asyncio.run(_fetch_em_news_async(symbols))

    # ---- L2: Investor Q&A from CNINFO (互动易) + SSE (上证e互动) ----
    irm_result = _fetch_irm_all(symbols)

    # ---- L0: Company announcements (公告) ----
    notice_result = _fetch_notices(symbols, portfolio_stocks=portfolio_stocks)

    # ---- Assemble per-stock results ----
    result = {}
    for sym in symbols:
        info = stock_info.get(
            sym, {"name": "", "keywords": set(), "related_stocks": set()}
        )
        name = info["name"]
        name_kws = {name, name[:2]} if len(name) >= 2 else {name} if name else set()
        related_kws = info["keywords"]
        related_stock_names = info["related_stocks"]

        items = {
            "L0_notices": [],
            "L1_direct": [],
            "L2_irm": [],
            "L3_related": [],
        }

        # L0: Company announcements
        items["L0_notices"] = notice_result.get(sym, [])

        # L1: Filter EastMoney news by stock name in title
        for item in em_result.get(sym, []):
            if _is_batch_article(item.get("title", "")):
                continue
            if _text_contains_any(item.get("title", ""), name_kws):
                items["L1_direct"].append(item)

        # L1: Filter Caixin pool by stock name
        seen_titles = {it["title"][:20] for it in items["L1_direct"]}
        for item in caixin_pool:
            text = item.get("title", "") + " " + (item.get("full_text", "") or "")[:200]
            if _text_contains_any(text, name_kws):
                if item["title"][:20] not in seen_titles:
                    items["L1_direct"].append({**item, "layer": "L1"})
                    seen_titles.add(item["title"][:20])

        # L2: Investor Q&A (recent, with answers)
        items["L2_irm"] = irm_result.get(sym, [])

        # L3: Related news from CLS telegraph + Caixin, matched by related_keywords
        all_kws = related_kws | related_stock_names
        if all_kws:
            # Match against Caixin pool
            for item in caixin_pool:
                text = (
                    item.get("title", "")
                    + " "
                    + (item.get("full_text", "") or "")[:300]
                )
                if _text_contains_any(text, all_kws):
                    if item["title"][:20] not in seen_titles:
                        items["L3_related"].append(
                            {
                                **item,
                                "layer": "L3",
                                "matched_by": _first_match(text, all_kws),
                            }
                        )
                        seen_titles.add(item["title"][:20])

            # Match against CLS telegraph
            for tg in telegraph_items or []:
                content = tg.get("content", "")
                if _text_contains_any(content, all_kws):
                    short_title = content[:60]
                    if short_title[:20] not in seen_titles:
                        items["L3_related"].append(
                            {
                                "title": short_title,
                                "source": "CLS",
                                "time": tg.get("time", ""),
                                "url": "",
                                "full_text": content,
                                "layer": "L3",
                                "matched_by": _first_match(content, all_kws),
                            }
                        )
                        seen_titles.add(short_title[:20])

        result[sym] = items

    # Log summary
    for sym in symbols:
        r = result[sym]
        name = stock_info.get(sym, {}).get("name", sym)
        logger.info(
            "  %s (%s): L0=%d L1=%d L2=%d L3=%d",
            sym,
            name,
            len(r["L0_notices"]),
            len(r["L1_direct"]),
            len(r["L2_irm"]),
            len(r["L3_related"]),
        )

    return result


# ============================================================
# L1: EastMoney per-stock news
# ============================================================


async def _fetch_em_news_async(symbols: list[str]) -> dict:
    """Fetch EastMoney news per stock with full article text."""
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    recent_dates = {today.strftime("%Y-%m-%d"), yesterday.strftime("%Y-%m-%d")}

    result = {}
    for symbol in symbols:
        try:
            df = ak.stock_news_em(symbol=symbol)
        except Exception:
            result[symbol] = []
            continue

        if df is None or df.empty:
            result[symbol] = []
            continue

        col_map = {
            "新闻标题": "title",
            "新闻内容": "content",
            "文章来源": "source",
            "发布时间": "time",
            "新闻链接": "url",
        }
        df = df.rename(columns=col_map)

        news_items = []
        urls_to_fetch = []

        for _, row in df.iterrows():
            title = str(row.get("title", "")).strip()
            time_str = str(row.get("time", "")).strip()
            source = str(row.get("source", "")).strip()
            url = str(row.get("url", "")).strip()

            if not title or _is_old_news(time_str, recent_dates):
                continue

            item = {
                "title": title,
                "source": source,
                "time": time_str,
                "url": url,
                "full_text": None,
                "layer": "L1",
            }
            news_items.append(item)
            if url and url.startswith("http"):
                urls_to_fetch.append(url)

        if urls_to_fetch:
            url_to_text = await fetch_many(urls_to_fetch)
            for item in news_items:
                fetched = url_to_text.get(item["url"])
                item["full_text"] = (
                    fetched
                    if fetched and len(fetched) > len(item["title"])
                    else item["title"]
                )
        else:
            for item in news_items:
                item["full_text"] = item["title"]

        result[symbol] = news_items
    return result


# ============================================================
# L1: Caixin hot news pool
# ============================================================


def _fetch_caixin_news() -> list[dict]:
    """Fetch Caixin top 100 financial news as a shared pool."""
    try:
        df = ak.stock_news_main_cx()
        if df is None or df.empty:
            return []
    except Exception as e:
        logger.warning("Failed to fetch Caixin news: %s", e)
        return []

    articles = []
    for _, row in df.iterrows():
        summary = str(row.get("summary", "")).strip()
        url = str(row.get("url", "")).strip()
        tag = str(row.get("tag", "")).strip()
        if not summary:
            continue
        articles.append(
            {
                "title": summary[:60],
                "source": f"财新-{tag}" if tag else "财新",
                "time": datetime.now().strftime("%Y-%m-%d"),
                "url": url,
                "full_text": summary,
            }
        )
    logger.info("Caixin news pool: %d articles", len(articles))
    return articles


# ============================================================
# L2: Investor Q&A (互动易)
# ============================================================


def _fetch_irm_all(symbols: list[str]) -> dict:
    """Fetch recent investor Q&A for each stock.
    Shenzhen stocks (0xxxxx, 3xxxxx): CNINFO 互动易.
    Shanghai stocks (6xxxxx): SSE 上证e互动."""
    result = {}
    today = datetime.now()

    for sym in symbols:
        # Shanghai stocks use SSE e-interaction (上证e互动)
        if sym.startswith("6"):
            result[sym] = _fetch_sseinfo(sym, today)
            continue

        try:
            df = ak.stock_irm_cninfo(symbol=sym)
        except Exception as e:
            logger.warning("Failed to fetch IRM for %s: %s", sym, e)
            result[sym] = []
            continue

        if df is None or df.empty:
            result[sym] = []
            continue

        items = []
        for _, row in df.iterrows():
            question = str(row.get("问题", "")).strip()
            answer = str(row.get("回答内容", "")).strip()
            ask_time = str(row.get("提问时间", "")).strip()
            update_time = str(row.get("更新时间", "")).strip()

            if not question:
                continue

            # Only keep recent (last 7 days)
            try:
                dt = pd.to_datetime(update_time or ask_time)
                if (today - dt).days > 7:
                    continue
            except Exception:
                pass

            items.append(
                {
                    "question": question[:200],
                    "answer": answer[:500] if answer and answer != "nan" else None,
                    "time": update_time or ask_time,
                    "layer": "L2",
                }
            )

        result[sym] = items[:5]  # Top 5 most recent
        logger.info("  IRM %s: %d Q&A items", sym, len(result[sym]))

    return result


# ============================================================
# L2 (Shanghai): SSE e-interaction (上证e互动)
# ============================================================


def _fetch_sseinfo(symbol: str, today: datetime) -> list[dict]:
    """Fetch investor Q&A from SSE e-interaction for Shanghai stocks."""
    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError("SSE e-interaction timeout")

    try:
        # SSE API can be slow — enforce 10s timeout
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(10)
        df = ak.stock_sns_sseinfo(symbol=symbol)
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
    except (TimeoutError, Exception) as e:
        signal.alarm(0)
        logger.warning("Failed to fetch SSE e-interaction for %s: %s", symbol, e)
        return []

    if df is None or df.empty:
        return []

    items = []
    for _, row in df.iterrows():
        # Column names vary — try common variants
        question = str(
            row.get("问题", row.get("title", row.iloc[0] if len(row) > 0 else ""))
        ).strip()
        answer = str(
            row.get("回答", row.get("answer", row.iloc[1] if len(row) > 1 else ""))
        ).strip()
        time_str = str(
            row.get("时间", row.get("date", row.iloc[-1] if len(row) > 0 else ""))
        ).strip()

        if not question:
            continue

        # Only keep recent (last 7 days)
        try:
            dt = pd.to_datetime(time_str)
            if (today - dt).days > 7:
                continue
        except Exception:
            pass

        items.append(
            {
                "question": question[:200],
                "answer": answer[:500] if answer and answer != "nan" else None,
                "time": time_str,
                "layer": "L2",
                "source": "上证e互动",
            }
        )

    logger.info("  SSE e-interaction %s: %d Q&A items", symbol, len(items[:5]))
    return items[:5]


# ============================================================
# L0: Company announcements (公告)
# ============================================================


def _fetch_notices(
    symbols: list[str],
    portfolio_stocks: list[dict] | None = None,
) -> dict:
    """
    Fetch recent company announcements from CNInfo (巨潮资讯).

    Returns:
        dict keyed by symbol with list of announcement items.
    """
    result = {}
    today = datetime.now()
    start_date = (today - timedelta(days=7)).strftime("%Y%m%d")
    end_date = today.strftime("%Y%m%d")

    for sym in symbols:
        items = []
        try:
            df = ak.stock_zh_a_disclosure_report_cninfo(
                symbol=sym,
                market="沪深京",
                start_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    title = str(row.get("公告标题", "")).strip()
                    date_str = str(row.get("公告时间", ""))[:10]
                    url = str(row.get("公告链接", "")).strip()

                    if not title:
                        continue

                    items.append(
                        {
                            "title": title[:100],
                            "date": date_str,
                            "url": url if url.startswith("http") else "",
                            "layer": "L0",
                        }
                    )
        except Exception as e:
            logger.warning("Failed to fetch notices for %s: %s", sym, e)

        result[sym] = items[:10]
        logger.info("  Notices %s: %d items", sym, len(result[sym]))

    return result


# ============================================================
# Helpers
# ============================================================

_IRRELEVANT_KEYWORDS = frozenset(
    [
        "娱乐",
        "体育",
        "明星",
        "综艺",
        "选秀",
        "八卦",
        "足球",
        "篮球",
        "网球",
        "奥运",
    ]
)

_BATCH_TITLE_RE = re.compile(
    r"(解密主力资金|净流出\d+股|净流入超亿元|概念[上下]涨|"
    r"主力资金出逃股|连续\d+日净流[出入]|龙虎榜|筹码大换手|"
    r"资金流向日报|主力资金净流[出入]|行业今日[净涨跌])"
)

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _is_irrelevant(text: str) -> bool:
    return any(kw in text for kw in _IRRELEVANT_KEYWORDS)


def _is_batch_article(title: str) -> bool:
    return bool(_BATCH_TITLE_RE.search(title))


def _is_old_news(time_str: str, recent_dates: set[str]) -> bool:
    date_match = _DATE_RE.search(time_str)
    if date_match:
        return date_match.group(1) not in recent_dates
    return False


def _text_contains_any(text: str, keywords: set[str]) -> bool:
    return any(kw in text for kw in keywords) if keywords else False


def _first_match(text: str, keywords: set[str]) -> str:
    """Return the first keyword that matches in text."""
    for kw in keywords:
        if kw in text:
            return kw
    return ""
