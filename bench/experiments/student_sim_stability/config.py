"""Experiment configuration for student simulator stability testing.

Defines tasks, personas, models, and parameters for the two-phase experiment.
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

# Live tutor model (Phase 2) — fixed to isolate student-side variance
TUTOR_MODEL: str = "anthropic/claude-sonnet-4-6"

# LLM judge model — strong model for reliable evaluation
JUDGE_MODEL: str = "anthropic/claude-opus-4-6"

# ---------------------------------------------------------------------------
# Experiment parameters
# ---------------------------------------------------------------------------
FIXED_TURNS: int = 8  # Fixed conversation length
REPEATS: int = 3  # Runs per (task, persona, model) combination
TEMPERATURE: float = 0.7  # Student simulator temperature (non-zero for variance)
TUTOR_TEMPERATURE: float = 0.3  # Live tutor temperature (lower for consistency)
JUDGE_TEMPERATURE: float = 0.0  # Judge temperature (deterministic)

# ---------------------------------------------------------------------------
# Task selection
# ---------------------------------------------------------------------------
# Each entry: (task_id, category, json_path_relative_to_bench/tasks/layer2/)
EXPERIMENT_TASKS: list[dict] = [
    {
        "task_id": "I01_implement_sma",
        "category": "implementation",
        "path": "implementation/I01_implement_sma.json",
        "difficulty": "easy",
    },
    {
        "task_id": "S03_mean_reversion_research",
        "category": "strategy",
        "path": "strategy/S03_mean_reversion_research.json",
        "difficulty": "medium",
    },
    {
        "task_id": "X02_lookahead",
        "category": "debug",
        "path": "debug/X02_lookahead.json",
        "difficulty": "easy",
    },
    {
        "task_id": "D05_return_computation",
        "category": "data_analysis",
        "path": "data_analysis/D05_return_computation.json",
        "difficulty": "medium",
    },
    {
        "task_id": "A03_sharpe_misconception",
        "category": "adversarial",
        "path": "adversarial/A03_sharpe_misconception.json",
        "difficulty": "medium",
    },
    {
        "task_id": "E01_build_ma_system",
        "category": "end_to_end",
        "path": "end_to_end/E01_build_ma_system.json",
        "difficulty": "medium",
    },
]

# ---------------------------------------------------------------------------
# Persona pairs per task
# ---------------------------------------------------------------------------
# Primary pair: finance_veteran + double_novice (maximum contrast)
# Fallback pair: developer_crossover + fullstack_practitioner
# Mapping: task_id -> list of persona_ids to test
TASK_PERSONA_MAP: dict[str, list[str]] = {
    "I01_implement_sma": ["developer_crossover", "fullstack_practitioner"],
    "S03_mean_reversion_research": ["finance_veteran", "double_novice"],
    "X02_lookahead": ["developer_crossover", "fullstack_practitioner"],
    "D05_return_computation": ["finance_veteran", "double_novice"],
    "A03_sharpe_misconception": ["finance_veteran", "double_novice"],
    "E01_build_ma_system": ["developer_crossover", "fullstack_practitioner"],
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BENCH_ROOT = "bench"
TASKS_DIR = "bench/tasks/layer2"
PERSONAS_DIR = "bench/personas"
OUTPUT_DIR = "bench/experiments/student_sim_stability/results"


@dataclass
class TrialKey:
    """Unique identifier for a single experiment trial."""

    phase: str  # "scripted" | "live" | "control"
    task_id: str
    persona_id: str
    student_model: str
    repeat_index: int

    @property
    def key(self) -> str:
        model_short = self.student_model.split("/")[-1]
        return f"{self.phase}__{self.task_id}__{self.persona_id}__{model_short}__r{self.repeat_index}"


def compute_trial_count() -> dict[str, int]:
    """Compute total trial counts per phase."""
    n_tasks = len(EXPERIMENT_TASKS)
    n_models = len(STUDENT_MODELS)
    avg_personas = 2  # 2 personas per task

    scripted = n_tasks * avg_personas * n_models * REPEATS
    live = scripted
    control = n_tasks * n_models * 1  # 1 repeat, no persona

    return {
        "scripted": scripted,
        "live": live,
        "control": control,
        "total": scripted + live + control,
        "total_messages": (scripted + live + control) * FIXED_TURNS,
    }
