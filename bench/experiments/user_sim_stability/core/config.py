"""Experiment configuration for user simulator stability testing.

Single-phase design: live tutor (gpt-4.1-nano) + user sim (3 models under test).
Judge evaluation via OpenRouter (default: claude-sonnet-4-6).
"""

import sys
from dataclasses import dataclass

from experiments.user_sim_stability.core.paths import BENCH_ROOT

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from server.config.llm_config import (
    STUDENT_SIM_STABILITY_JUDGE_MODELS,
    STUDENT_SIM_STABILITY_PRIMARY_JUDGE_MODEL,
    STUDENT_SIM_STABILITY_USER_MODELS,
    STUDENT_SIM_STABILITY_TUTOR_MODEL,
)

# ---------------------------------------------------------------------------
# User simulator models under test
# ---------------------------------------------------------------------------
USER_MODELS: list[str] = list(STUDENT_SIM_STABILITY_USER_MODELS)

# Live tutor model — cheap but verified quality
TUTOR_MODEL: str = STUDENT_SIM_STABILITY_TUTOR_MODEL

# ---------------------------------------------------------------------------
# Experiment parameters
# ---------------------------------------------------------------------------
FIXED_TURNS: int = 8  # Tutor turns per conversation.
GENERATED_STUDENT_TURNS: int = FIXED_TURNS - 1
REPEATS: int = 3
TEMPERATURE: float = 0.0  # Matches production (EVAL_JUDGE_TEMPERATURE)
TUTOR_TEMPERATURES: list[float] = [0.0, 1.0]  # Ablation: consistent vs diverse tutor
MAX_WORKERS: int = 100  # Parallel trial execution (OpenRouter paid = no rate limit)

# Conversation turn provenance. Judge prompts for S1-S3 and S6 use only
# generated user turns, never fixture/control opening turns.
FIXTURE_OPENING_SOURCE: str = "fixture_opening"
CONTROL_OPENING_SOURCE: str = "control_neutral_opening"
USER_MODEL_SOURCE: str = "user_model"
TUTOR_MODEL_SOURCE: str = "tutor_model"
NEUTRAL_CONTROL_OPENING: str = "Hi, I need help understanding this task."

# ---------------------------------------------------------------------------
# Judge configuration
# ---------------------------------------------------------------------------
JUDGE_MODELS: list[str] = list(STUDENT_SIM_STABILITY_JUDGE_MODELS)
JUDGE_LABELS: tuple[str, str, str] = ("sonnet", "gpt54", "gemini")
if len(JUDGE_MODELS) != 3 or len(JUDGE_LABELS) != 3:
    raise RuntimeError("panel-3 SSOT invariant violated")
JUDGE_LABEL_BY_MODEL_ID: dict[str, str] = dict(zip(JUDGE_MODELS, JUDGE_LABELS))
PANEL_JUDGES: tuple[tuple[str, str], ...] = tuple(zip(JUDGE_MODELS, JUDGE_LABELS))


def judge_label(model_id: str) -> str:
    """Map a fully-qualified model id to its short panel label."""
    try:
        return JUDGE_LABEL_BY_MODEL_ID[model_id]
    except KeyError as exc:
        raise ValueError(f"unknown panel judge: {model_id!r}") from exc


JUDGE_MODEL: str = STUDENT_SIM_STABILITY_PRIMARY_JUDGE_MODEL
JUDGE_TEMPERATURE: float = 0.0
JUDGE_MAX_WORKERS: int = 6

# S1 is sampled for the final report because S1 is a per-message persona
# adherence check. The full rendered S1 prompt set can still be generated for
# audit, but the judged/reporting sample is live tutor t=0, repeat 0.
S1_SAMPLE_POLICY: str = "live-r0-tt0"

# ---------------------------------------------------------------------------
# Task selection
# ---------------------------------------------------------------------------
EXPERIMENT_TASKS: list[dict] = [
    {
        "task_id": "I01_implement_sma",
        "category": "implementation",
        "path": "implementation/I01_implement_sma.json",
    },
    {
        "task_id": "S03_mean_reversion_research",
        "category": "strategy",
        "path": "strategy/S03_mean_reversion_research.json",
    },
    {
        "task_id": "X02_lookahead",
        "category": "debug",
        "path": "debug/X02_lookahead.json",
    },
    {
        "task_id": "D05_return_computation",
        "category": "data_analysis",
        "path": "data_analysis/D05_return_computation.json",
    },
    {
        "task_id": "A03_sharpe_misconception",
        "category": "adversarial",
        "path": "adversarial/A03_sharpe_misconception.json",
    },
    {
        "task_id": "E01_build_ma_system",
        "category": "end_to_end",
        "path": "end_to_end/E01_build_ma_system.json",
    },
]

# ---------------------------------------------------------------------------
# Persona pairs per task
# ---------------------------------------------------------------------------
TASK_PERSONA_MAP: dict[str, list[str]] = {
    "I01_implement_sma": ["developer_crossover", "fullstack_practitioner"],
    "S03_mean_reversion_research": ["finance_veteran", "double_novice"],
    "X02_lookahead": ["developer_crossover", "fullstack_practitioner"],
    "D05_return_computation": ["finance_veteran", "double_novice"],
    "A03_sharpe_misconception": ["finance_veteran", "double_novice"],
    "E01_build_ma_system": ["developer_crossover", "fullstack_practitioner"],
}

# Per-persona openings used by stability trials. Owned by this experiment so
# that #122 collapsing task JSON to a single (persona_id, user_opening) does
# not strip the alternate-persona fixtures the experiment compares against.
STABILITY_TASK_OPENINGS: dict[str, dict[str, str]] = {
    "I01_implement_sma": {
        "fullstack_practitioner": "I've backtested SMA strategies in Python before but need to port one to LEAN C# for production. SMA(20) on BTCUSDT daily as a smoke test. I understand the trading logic — just need help with the LEAN API specifics: AddCrypto, indicator registration, SetHoldings.",
        "developer_crossover": "I need to write a C# algorithm for a trading engine called LEAN. I'm a Python dev so C# syntax isn't a huge leap, but I have no idea what a 'moving average strategy' is supposed to do. What's the trading logic, and how do I translate it into LEAN's API?",
    },
    "S03_mean_reversion_research": {
        "fullstack_practitioner": "I'm exploring how to research mean-reversion alpha in BTCUSDT daily data. I have experience with simpler strategies — what are the key trade-offs here?",
        "finance_veteran": "I want to research mean-reversion alpha in BTCUSDT daily data. I understand the strategy logic from my trading experience — but I need help coding and backtesting it.",
        "developer_crossover": "I want to research mean-reversion alpha in BTCUSDT daily data. I can write the code — but I need to understand the trading logic first. Why does this work?",
        "double_novice": "I heard about how to research mean-reversion alpha in BTCUSDT daily data in class and want to try it. I'm new to both coding and trading — can you walk me through it step by step?",
    },
    "X02_lookahead": {
        "fullstack_practitioner": "I'm debugging an issue: find and fix a look-ahead bias in an SMA crossover strategy. I've narrowed it down but want a second pair of eyes on the root cause.",
        "developer_crossover": "I'm looking at code that should find and fix a look-ahead bias in an SMA crossover strategy. The code reads fine to me, but I'm not sure the financial logic is correct. What should it be doing?",
    },
    "D05_return_computation": {
        "fullstack_practitioner": "I want to compute and compare simple returns vs log returns rigorously. I know the basics — let's focus on methodology and potential pitfalls.",
        "finance_veteran": "I need to compute and compare simple returns vs log returns in Python. I understand what the data means financially — I just need help with the pandas side.",
        "developer_crossover": "I need to compute and compare simple returns vs log returns. I can use pandas fine — but what do these financial metrics actually mean? Why would a trader care?",
        "double_novice": "My professor asked us to compute and compare simple returns vs log returns. I'm new to both Python and finance — can you start from the basics?",
    },
    "A03_sharpe_misconception": {
        "fullstack_practitioner": "My parameter sweep over 200 SMA configurations found one with Sharpe 5.0 on AAPL. Statistical significance looks solid. I'm ready to deploy — any final checks?",
        "finance_veteran": "After testing 200 parameter combinations, I found an SMA crossover with Sharpe 5.0. That's extraordinary for a daily strategy — should I size up and run it live?",
        "developer_crossover": "I ran a grid search over 200 SMA parameters and the best one has a Sharpe ratio of 5.0. My code is correct — is this a good result?",
        "double_novice": "I tested 200 different settings for my trading strategy and one of them has something called a Sharpe ratio of 5.0. My friend says that's really good. Is it?",
    },
    "E01_build_ma_system": {
        "fullstack_practitioner": "I need to build a complete moving average crossover trading system from scratch. I've done similar work in Python before — let's focus on getting the architecture and edge cases right.",
        "developer_crossover": "I need to build a complete moving average crossover trading system. I can handle the code, but I don't understand the financial logic behind it. What should this do and why?",
    },
}

# ---------------------------------------------------------------------------
# Paths (relative to bench/)
# ---------------------------------------------------------------------------
OUTPUT_DIR = "experiments/user_sim_stability/results/main"


@dataclass
class TrialKey:
    """Unique identifier for a single experiment trial."""

    phase: str  # "live" | "control"
    task_id: str
    persona_id: str
    user_model: str
    repeat_index: int

    tutor_temperature: float = 0.0

    @property
    def key(self) -> str:
        model_short = self.user_model.split("/")[-1]
        t_tag = f"_tt{self.tutor_temperature:.0f}" if self.phase == "live" else ""
        return (
            f"{self.phase}__{self.task_id}__{self.persona_id}"
            f"__{model_short}__r{self.repeat_index}{t_tag}"
        )


def expected_artifact_counts(profile: str = "full") -> dict[str, int]:
    """Canonical per-dimension expected artifact counts for the experiment.

    The shape is profile-independent: pilot consumers branch on profile
    separately (the pilot runs a subset whose exact size depends on
    command-line flags that aren't visible here), so this returns the
    full-profile numbers in either case.
    """
    del profile
    # Lazy import: probes is part of the pipeline layer and importing it at
    # module top would create a config -> pipeline cycle.
    from experiments.user_sim_stability.pipeline.probes import PROBES

    combos = sum(len(v) for v in TASK_PERSONA_MAP.values())
    n_models = len(USER_MODELS)
    n_temps = len(TUTOR_TEMPERATURES)
    n_personas = len({pid for ids in TASK_PERSONA_MAP.values() for pid in ids})
    live = combos * n_models * REPEATS * n_temps
    control = combos * n_models
    return {
        "live": live,
        "control": control,
        "conversations": live + control,
        "S1_sample": combos * n_models * GENERATED_STUDENT_TURNS,
        "S1_full": (live + control) * GENERATED_STUDENT_TURNS,
        "S2": live + control,
        "S3": combos * n_models * n_temps,
        "S4": live,
        "S5": n_personas * len(PROBES) * n_models,
        "S6": control,
    }


def compute_trial_count() -> dict[str, int]:
    """Compute total trial counts."""
    n_models = len(USER_MODELS)
    n_temps = len(TUTOR_TEMPERATURES)
    n_combinations = sum(len(v) for v in TASK_PERSONA_MAP.values())  # 12

    live = n_combinations * n_models * REPEATS * n_temps
    # Control is per task/persona/model at tutor t=0.
    control = n_combinations * n_models

    return {
        "live": live,
        "control": control,
        "total": live + control,
        "user_messages": (live + control) * GENERATED_STUDENT_TURNS,
        "tutor_messages": (live + control) * FIXED_TURNS,
    }
