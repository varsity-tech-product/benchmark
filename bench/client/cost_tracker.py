"""Agent cost tracking for QuantTutorBench baseline client.

Aggregates token usage from adapter.get_token_records() and saves
agent_cost.json to the client-side result directory.

Server does not see this file — it is purely client-side bookkeeping.

"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def aggregate_cost(token_records: list) -> dict:
    """Aggregate a list of TokenRecord objects into a summary dict.

    Args:
        token_records: List of TokenRecord (from adapter.get_token_records()).

    Returns:
        Dict with input_tokens, output_tokens, cost_usd, api_calls, model.
    """
    if not token_records:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "api_calls": 0,
            "model": "unknown",
        }
    return {
        "input_tokens": sum(r.input_tokens for r in token_records),
        "output_tokens": sum(r.output_tokens for r in token_records),
        "cost_usd": sum(r.cost_usd for r in token_records),
        "api_calls": len(token_records),
        "model": token_records[0].model if token_records else "unknown",
    }


def save_agent_cost(result_dir: Path, cost: dict) -> Path:
    """Save agent_cost.json to the given directory.

    Args:
        result_dir: Directory to write to (created if needed).
        cost: Aggregated cost dict from aggregate_cost().

    Returns:
        Path to the written file.
    """
    result_dir = Path(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    path = result_dir / "agent_cost.json"
    path.write_text(json.dumps(cost, indent=2), encoding="utf-8")
    logger.info("Saved agent_cost.json to %s", path)
    return path
