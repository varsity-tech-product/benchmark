"""QuantTutorBench Gym — environment interface for agent evaluation.

The agent controls the loop. The environment provides tools and a student.

Quick start::

    from bench.gym import QuantTutorEnv

    env = QuantTutorEnv(use_docker=True)
    obs = env.reset("S01_ma_crossover")

    while not obs.done:
        result = env.call_tool("fetch_market_data", symbol="AAPL")
        obs = env.send_message("Here's what I found...")

    scores = env.evaluate()
    env.close()
"""

from bench.gym.types import Observation, Scores


def __getattr__(name):
    # Lazy import to avoid pulling in heavy deps (deepeval, orchestrator)
    # until the env is actually instantiated.
    if name == "QuantTutorEnv":
        from bench.gym.env import QuantTutorEnv
        return QuantTutorEnv
    raise AttributeError(f"module 'bench.gym' has no attribute {name!r}")


__all__ = ["QuantTutorEnv", "Observation", "Scores"]
