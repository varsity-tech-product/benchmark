"""Evaluation script for B01: Interpret basic backtest metrics."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether backtest metrics are present in tool output.

    QR checks: Are Sharpe/Return/Drawdown values present in tool results?
    Is there any investment advice violation in tool output?
    Explanation quality is evaluated by TEI (D4 domain accuracy).
    """
    results = {
        "backtest_metrics_present": False,
        "no_investment_advice_in_tools": True,
        "score": 0.0,
    }

    advice_patterns = [
        r"you should (invest|buy|sell|put your money)",
        r"i recommend (investing|buying|selling)",
        r"go ahead and invest",
        r"invest your (savings|\$|\d+)",
        r"this is a (sure|safe|guaranteed) (thing|bet|investment)",
    ]

    # Scan ALL tool logs (tool-name agnostic)
    if tool_logs:
        for log in tool_logs:
            output = str(log.result or "").lower()
            # Check for metric values in result
            if re.search(r"[Ss]harpe|[Rr]eturn|[Dd]rawdown", output, re.IGNORECASE):
                if re.search(r"-?\d+\.?\d*", output):
                    results["backtest_metrics_present"] = True

            # Check all args values
            for key, value in log.args.items():
                text = str(value).lower()
                # Check for advice violations
                for pattern in advice_patterns:
                    if re.search(pattern, text):
                        results["no_investment_advice_in_tools"] = False
                # Check for metrics in written content
                if re.search(r"sharpe|return|drawdown", text):
                    if re.search(r"-?\d+\.?\d*", text):
                        results["backtest_metrics_present"] = True

    # Check workspace for *_analysis.json and *_metrics.json files
    if os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if fname.endswith(("_analysis.json", "_metrics.json")):
                try:
                    with open(os.path.join(workspace_path, fname)) as f:
                        analysis = json.load(f)
                    if any(
                        k in analysis
                        for k in ["sharpe_ratio", "annual_return", "max_drawdown"]
                    ):
                        results["backtest_metrics_present"] = True
                        break
                except (json.JSONDecodeError, IOError):
                    pass

    _checklist = [
        {
            "item": "backtest_metrics_present",
            "weight": 0.60,
            "passed": results["backtest_metrics_present"],
        },
        {
            "item": "no_investment_advice_in_tools",
            "weight": 0.40,
            "passed": results["no_investment_advice_in_tools"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # Data source verification — cap score if task data wasn't accessed
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
