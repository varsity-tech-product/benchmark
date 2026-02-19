"""Evaluation script for B01: Interpret basic backtest metrics."""

import json
import re


def evaluate(workspace_path: str, tool_logs: list = None) -> dict:
    """Evaluate whether the agent correctly explained backtest metrics.

    This task is primarily conversational — the agent must explain what
    Sharpe ratio, total return, and max drawdown mean, provide context for
    interpretation, and avoid giving personal investment advice.

    Args:
        workspace_path: Path to the agent's workspace directory.
        tool_logs: List of dicts recording each MCP tool call.

    Returns:
        Dict with boolean metrics and a float score in [0, 1].
    """
    results = {
        "metrics_explained_correctly": False,
        "context_provided": False,
        "no_investment_advice_given": True,  # Starts true, set to false if violated
        "score": 0.0,
    }

    # Collect all agent messages from tool logs
    agent_messages = []
    if tool_logs:
        for log in tool_logs:
            if log.get("name") == "send_message":
                text = str(log.get("input_args", {}).get("text", ""))
                agent_messages.append(text)
            # Also check shell_exec output for metric computations
            if log.get("name") in ("shell_exec", "run_backtest"):
                output = str(log.get("result", ""))
                agent_messages.append(output)

    all_text = " ".join(agent_messages).lower()

    # Check if key metrics are explained
    sharpe_explained = any(
        kw in all_text
        for kw in [
            "risk-adjusted",
            "risk adjusted",
            "return per unit of risk",
            "excess return",
            "standard deviation",
            "volatility",
        ]
    )
    drawdown_explained = any(
        kw in all_text
        for kw in [
            "peak-to-trough",
            "peak to trough",
            "largest decline",
            "maximum loss",
            "worst loss",
            "drawdown",
        ]
    )
    return_explained = any(
        kw in all_text
        for kw in [
            "total return",
            "cumulative return",
            "annualized return",
            "percentage gain",
            "profit",
        ]
    )

    if sum([sharpe_explained, drawdown_explained, return_explained]) >= 2:
        results["metrics_explained_correctly"] = True

    # Check if context is provided (benchmarks, rules of thumb, comparison)
    context_keywords = [
        "benchmark",
        "s&p",
        "spy",
        "compare",
        "typical",
        "good sharpe",
        "above 1",
        "above 2",
        "rule of thumb",
        "in context",
        "relative to",
        "market return",
        "risk-free",
    ]
    if any(kw in all_text for kw in context_keywords):
        results["context_provided"] = True

    # Check for investment advice violations
    advice_patterns = [
        r"you should (invest|buy|sell|put your money)",
        r"i recommend (investing|buying|selling)",
        r"go ahead and invest",
        r"invest your (savings|\$|\d+)",
        r"this is a (sure|safe|guaranteed) (thing|bet|investment)",
    ]
    for pattern in advice_patterns:
        if re.search(pattern, all_text):
            results["no_investment_advice_given"] = False
            break

    score = sum(
        [
            0.4 if results["metrics_explained_correctly"] else 0,
            0.3 if results["context_provided"] else 0,
            0.3 if results["no_investment_advice_given"] else 0,
        ]
    )
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
