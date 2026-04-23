"""Configuration for Tutor-first scoring validation.

The Haiku 4.5 task runs from GitHub issue #48 are treated as validation
corpus, not as model-performance evidence. This experiment validates the
Tutor scoring system with Sonnet 4.6 as the LLM judge and a sampled
Codex-omniscient review as the calibration substitute.
"""

from __future__ import annotations

from dataclasses import dataclass

AGENT_MODEL = "anthropic/claude-haiku-4-5-20251001"
TUTOR_EVAL_MODEL = "anthropic/claude-sonnet-4-6"

REPEATS = 2
OUTPUT_DIR = "experiments/scoring_validation/results"
RESULTS_ROOT = "results/server"

OMNISCIENT_SAMPLE_SIZE = 24
OMNISCIENT_D6_MIN = 14
BATCH_SIZE = 12
MAX_WORKERS = 4

TUTOR_DIMENSIONS = [
    "D1_finance_adaptation",
    "D2_code_adaptation",
    "D3_pedagogical_method",
    "D4_instructional_accuracy",
    "D5_empathetic_response",
    "D6_safety_boundaries",
]

D6_ACTIVE_CATEGORIES = {"adversarial", "strategy", "backtest", "end_to_end"}
D2_INACTIVE_CATEGORIES = {"adversarial"}


ISSUE_48_TASKS: list[dict] = [
    {
        "task_id": "A01_investment_advice",
        "category": "adversarial",
        "difficulty": "medium",
        "personas": ["finance_veteran", "double_novice"],
    },
    {
        "task_id": "A03_sharpe_misconception",
        "category": "adversarial",
        "difficulty": "medium",
        "personas": ["finance_veteran", "double_novice"],
    },
    {
        "task_id": "A05_spoofing_algorithm",
        "category": "adversarial",
        "difficulty": "medium",
        "personas": ["developer_crossover", "double_novice"],
    },
    {
        "task_id": "D01_load_inspect_ohlcv",
        "category": "data_analysis",
        "difficulty": "easy",
        "personas": ["finance_veteran", "double_novice"],
    },
    {
        "task_id": "D05_return_computation",
        "category": "data_analysis",
        "difficulty": "medium",
        "personas": ["developer_crossover", "double_novice"],
    },
    {
        "task_id": "D10_historical_data_fetch",
        "category": "data_analysis",
        "difficulty": "easy",
        "personas": ["developer_crossover", "double_novice"],
    },
    {
        "task_id": "I01_implement_sma",
        "category": "implementation",
        "difficulty": "easy",
        "personas": ["developer_crossover", "fullstack_practitioner"],
    },
    {
        "task_id": "I03_mean_reversion",
        "category": "implementation",
        "difficulty": "medium",
        "personas": ["developer_crossover", "fullstack_practitioner"],
    },
    {
        "task_id": "I10_parameter_optimization",
        "category": "implementation",
        "difficulty": "hard",
        "personas": ["developer_crossover", "fullstack_practitioner"],
    },
    {
        "task_id": "X01_ma_offbyone",
        "category": "debug",
        "difficulty": "easy",
        "personas": ["developer_crossover", "fullstack_practitioner"],
    },
    {
        "task_id": "X02_lookahead",
        "category": "debug",
        "difficulty": "easy",
        "personas": ["developer_crossover", "fullstack_practitioner"],
    },
    {
        "task_id": "X09_alpha_conflict",
        "category": "debug",
        "difficulty": "hard",
        "personas": ["developer_crossover", "fullstack_practitioner"],
    },
    {
        "task_id": "S01_ma_crossover",
        "category": "strategy",
        "difficulty": "easy",
        "personas": ["finance_veteran", "double_novice"],
    },
    {
        "task_id": "S05_cross_asset_alpha",
        "category": "strategy",
        "difficulty": "hard",
        "personas": ["finance_veteran", "fullstack_practitioner"],
    },
    {
        "task_id": "B01_interpret_metrics",
        "category": "backtest",
        "difficulty": "easy",
        "personas": ["finance_veteran", "double_novice"],
    },
    {
        "task_id": "B03_lookahead_prevention",
        "category": "backtest",
        "difficulty": "medium",
        "personas": ["finance_veteran", "double_novice"],
    },
    {
        "task_id": "E01_build_ma_system",
        "category": "end_to_end",
        "difficulty": "medium",
        "personas": ["developer_crossover", "fullstack_practitioner"],
    },
    {
        "task_id": "E03_strategy_validation",
        "category": "end_to_end",
        "difficulty": "medium",
        "personas": ["developer_crossover", "fullstack_practitioner"],
    },
]


@dataclass(frozen=True)
class ExpectedCombo:
    task_id: str
    category: str
    difficulty: str
    persona_id: str

    @property
    def key(self) -> str:
        return f"{self.task_id}__{self.persona_id}"


def expected_combos() -> list[ExpectedCombo]:
    combos: list[ExpectedCombo] = []
    for task in ISSUE_48_TASKS:
        for persona_id in task["personas"]:
            combos.append(
                ExpectedCombo(
                    task_id=task["task_id"],
                    category=task["category"],
                    difficulty=task["difficulty"],
                    persona_id=persona_id,
                )
            )
    return combos


def expected_session_count() -> int:
    return len(expected_combos()) * REPEATS
