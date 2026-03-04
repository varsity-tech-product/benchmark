"""Evaluation script for D11: Realtime Data Fetch.

QR Programmatic: checks tool outputs and workspace for evidence that
realtime architecture was demonstrated, quote/trade handling is present,
data quality handling was addressed, and captured data was saved.
(network-enabled sandbox)
"""

import json
import os


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    results = {
        "realtime_architecture_demonstrated": False,
        "quote_trade_handling": False,
        "data_quality_handling": False,
        "data_captured_to_workspace": False,
        "score": 0.0,
    }

    combined = _collect_evidence(workspace_path, tool_logs)
    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # 1. Realtime architecture demonstrated (0.25)
    rt_kws = [
        "websocket",
        "socket",
        "asyncio",
        "polling",
        "stream",
        "subscribe",
        "callback",
        "event loop",
        "sse",
        "server-sent",
        "long_poll",
    ]
    has_rt_code = any(f.endswith(".py") for f in workspace_files) and _has_keywords(
        combined, rt_kws
    )
    if has_rt_code or _has_keywords(combined, rt_kws):
        results["realtime_architecture_demonstrated"] = True

    # 2. Quote/trade handling (0.30)
    qt_kws = [
        "bid",
        "ask",
        "trade",
        "quote",
        "spread",
        "mid-price",
        "mid_price",
        "order book",
        "order_book",
        "tick",
    ]
    if _has_keywords(combined, qt_kws):
        results["quote_trade_handling"] = True

    # 3. Data quality handling (0.25)
    dq_kws = [
        "latency",
        "out-of-order",
        "out_of_order",
        "stale",
        "duplicate",
        "sequence",
        "dedup",
        "sort",
        "timestamp",
        "timezone",
        "session",
        "market_hours",
        "market hours",
    ]
    if _has_keywords(combined, dq_kws):
        results["data_quality_handling"] = True

    # 4. Data captured to workspace (0.20)
    csv_files = [f for f in workspace_files if f.endswith(".csv")]
    for csv_f in csv_files:
        try:
            fpath = os.path.join(workspace_path, csv_f)
            with open(fpath) as fh:
                header = fh.readline().lower()
                lines = fh.readlines()
            has_ts = any(
                kw in header for kw in ["timestamp", "time", "date", "datetime"]
            )
            if has_ts and len(lines) >= 3:
                results["data_captured_to_workspace"] = True
                break
            if len(lines) >= 5:
                results["data_captured_to_workspace"] = True
                break
        except (IOError, UnicodeDecodeError):
            pass

    _checklist = [
        {
            "item": "realtime_architecture_demonstrated",
            "weight": 0.25,
            "passed": results["realtime_architecture_demonstrated"],
        },
        {
            "item": "quote_trade_handling",
            "weight": 0.30,
            "passed": results["quote_trade_handling"],
        },
        {
            "item": "data_quality_handling",
            "weight": 0.25,
            "passed": results["data_quality_handling"],
        },
        {
            "item": "data_captured_to_workspace",
            "weight": 0.20,
            "passed": results["data_captured_to_workspace"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])
    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


def _collect_evidence(workspace_path: str, tool_logs: list) -> str:
    parts = []
    for log in tool_logs or []:
        parts.append(log.name)
        parts.append(str(log.args))
        parts.append(str(log.result or ""))
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if fname.endswith((".txt", ".json", ".md", ".csv", ".log")):
                try:
                    with open(os.path.join(workspace_path, fname)) as f:
                        parts.append(f.read()[:2000])
                except (IOError, UnicodeDecodeError):
                    pass
    return " ".join(parts).lower()


def _has_keywords(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
