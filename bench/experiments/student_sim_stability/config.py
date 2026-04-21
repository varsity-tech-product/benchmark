"""Experiment configuration for student simulator stability testing.

Single-phase design: live tutor (gpt-4.1-nano) + student sim (3 models under test).
Judge evaluation via OpenRouter (default: claude-sonnet-4-6).
"""

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Student simulator models under test
# ---------------------------------------------------------------------------
STUDENT_MODELS: list[str] = [
    "openai/gpt-5.4",
    "anthropic/claude-sonnet-4-6",
    "google/gemini-3.1-pro-preview",
]

# Live tutor model — cheap but verified quality
TUTOR_MODEL: str = "openai/gpt-4.1-nano"

# ---------------------------------------------------------------------------
# Experiment parameters
# ---------------------------------------------------------------------------
FIXED_TURNS: int = 8
REPEATS: int = 3
TEMPERATURE: float = 0.0  # Matches production (EVAL_JUDGE_TEMPERATURE)
TUTOR_TEMPERATURES: list[float] = [0.0, 1.0]  # Ablation: consistent vs diverse tutor
MAX_WORKERS: int = 100  # Parallel trial execution (OpenRouter paid = no rate limit)

# ---------------------------------------------------------------------------
# Judge configuration
# ---------------------------------------------------------------------------
JUDGE_MODEL: str = "anthropic/claude-sonnet-4-6"
JUDGE_TEMPERATURE: float = 0.0
JUDGE_MAX_WORKERS: int = 6

# D1 is sampled for the final report because D1 is a per-message persona
# adherence check. The full rendered D1 prompt set can still be generated for
# audit, but the judged/reporting sample is live tutor t=0, repeat 0.
D1_SAMPLE_POLICY: str = "live-r0-tt0"

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

# ---------------------------------------------------------------------------
# Paths (relative to bench/)
# ---------------------------------------------------------------------------
OUTPUT_DIR = "experiments/student_sim_stability/results"


@dataclass
class TrialKey:
    """Unique identifier for a single experiment trial."""

    phase: str  # "live" | "control"
    task_id: str
    persona_id: str
    student_model: str
    repeat_index: int

    tutor_temperature: float = 0.0

    @property
    def key(self) -> str:
        model_short = self.student_model.split("/")[-1]
        t_tag = f"_tt{self.tutor_temperature:.0f}" if self.phase == "live" else ""
        return (
            f"{self.phase}__{self.task_id}__{self.persona_id}"
            f"__{model_short}__r{self.repeat_index}{t_tag}"
        )


def compute_trial_count() -> dict[str, int]:
    """Compute total trial counts."""
    n_models = len(STUDENT_MODELS)
    n_temps = len(TUTOR_TEMPERATURES)
    n_combinations = sum(len(v) for v in TASK_PERSONA_MAP.values())  # 12

    live = n_combinations * n_models * REPEATS * n_temps
    control = len(EXPERIMENT_TASKS) * n_models  # control only at t=0

    return {
        "live": live,
        "control": control,
        "total": live + control,
        "student_messages": (live + control) * (FIXED_TURNS - 1),
        "tutor_messages": (live + control) * FIXED_TURNS,
    }
