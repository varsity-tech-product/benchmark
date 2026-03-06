"""QuantTutorBench core data models."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TaskCategory(str, Enum):
    # Layer 2 categories (multi-turn tutoring scenarios)
    DATA_ANALYSIS = "data_analysis"
    STRATEGY = "strategy"
    IMPLEMENTATION = "implementation"
    BACKTEST = "backtest"
    DEBUG = "debug"
    END_TO_END = "end_to_end"
    ADVERSARIAL = "adversarial"
    # Layer 1 categories (single-turn knowledge items)
    CONCEPTUAL_QA = "conceptual_qa"
    STRATEGY_EXPLANATION = "strategy_explanation"
    CODE_GENERATION = "code_generation"
    CODE_DEBUGGING = "code_debugging"
    DATA_INTERPRETATION = "data_interpretation"
    MULTI_STEP_REASONING = "multi_step_reasoning"


class TaskType(str, Enum):
    SINGLE_TURN = "single_turn"
    MULTI_TURN = "multi_turn"


class QuantValidation(BaseModel):
    eval_script: str


class GroundTruth(BaseModel):
    expected_outcome: str
    required_capabilities: list[str] = Field(default_factory=list)
    expected_mcp_tools: list[str] = Field(default_factory=list)
    convenient_tools: list[str] = Field(default_factory=list)
    quant_validation: Optional[QuantValidation] = None


class EnvironmentConfig(BaseModel):
    data_files: list[str] = Field(default_factory=list)
    core_mcp_tools: list[str] = Field(default_factory=list)
    docs_available: list[str] = Field(default_factory=list)
    sandbox_image: str = "quant-tutor-env:v2.2"
    # Whether this task requires outbound internet access inside the sandbox.
    # Default is False for reproducibility/safety.
    network_enabled: bool = False


class QuantTutorTask(BaseModel):
    """Unified task format for both Layer 1 (single-turn) and Layer 2 (multi-turn).

    Layer 1 tasks use: description (as question), context, reference_answer,
    synthetic_response, and minimal ground_truth.
    Layer 2 tasks use: persona_ids, student_openings, environment, and full
    ground_truth with required_capabilities.
    """

    task_id: str
    version: str = "1.0"
    difficulty: Difficulty
    category: TaskCategory
    task_type: TaskType = TaskType.MULTI_TURN
    description: str
    # Layer 1 fields (single-turn Q&A)
    context: Optional[str] = None
    reference_answer: Optional[str] = None
    synthetic_response: Optional[str] = None
    source_dataset: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    # Layer 2 fields (multi-turn tutoring) — optional for Layer 1
    persona_ids: list[str] = Field(default_factory=list)
    student_openings: dict[str, str] = Field(default_factory=dict)
    environment: Optional[EnvironmentConfig] = None
    ground_truth: Optional[GroundTruth] = None
    requires_code: bool = False
    requires_tool: bool = False
    sample_code: Optional[str] = None
    max_turns: int = 30
    agent_max_steps: int = 10
    timeout_minutes: int = 15


class StudentPersona(BaseModel):
    persona_id: str
    knowledge_level: str
    description: str
    known_concepts: list[str] = Field(default_factory=list)
    unknown_concepts: list[str] = Field(default_factory=list)
    emotional_profile: str = ""
    behavioral_rules: list[str] = Field(default_factory=list)


class ConversationTurn(BaseModel):
    """A single turn in the conversation."""

    role: str  # "user" or "assistant"
    content: str


class TaskResult(BaseModel):
    """Result of running a single task evaluation."""

    task_id: str
    persona_id: str
    run_index: int = 0
    difficulty: str = ""  # §6.4: needed for difficulty curve computation
    category: str = ""  # §6.4: needed for per-category aggregation
    requires_code: bool = False  # Whether the task expects code output
    turns: list[ConversationTurn] = Field(default_factory=list)
    workspace_files: list[str] = Field(default_factory=list)
    quant_result_score: float = 0.0
    quant_process_score: float = 0.0
    tutor_scores: dict[str, float] = Field(default_factory=dict)
    tutor_scores_by_model: dict[str, dict[str, float]] = Field(
        default_factory=dict
    )  # {model_name: {dim: score}} per-judge-model breakdown
    overall_score: float = 0.0
    # Extended metrics (design doc §6.1, §6.4)
    process_metrics: dict = Field(
        default_factory=dict
    )  # DeepEval process metric scores
    eval_script_detail: dict = Field(
        default_factory=dict
    )  # Full eval script result (checklist items + diagnostics)
    code_eval: dict = Field(
        default_factory=dict
    )  # Code Execution QR (static + execution + output)
    result_judge: dict = Field(
        default_factory=dict
    )  # LLM Result Judge (numerical accuracy + completeness + correctness)
    code_process: dict = Field(
        default_factory=dict
    )  # Code Process Quality (iterative refinement + debugging + explanation)
    tool_usage: dict = Field(
        default_factory=dict
    )  # Tool Usage scoring (expected/convenient/distractor)
    sandbox_info: dict = Field(default_factory=dict)
    token_usage: dict = Field(
        default_factory=dict
    )  # Token/cost breakdown {agent: {...}, eval: {...}, total: {...}}
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    error: Optional[str] = None
    eval_aborted: bool = (
        False  # True when evaluation was aborted due to LLM call failures
    )


class BenchmarkReport(BaseModel):
    """Aggregate benchmark results."""

    agent_name: str
    total_tasks: int = 0
    overall_agent_score: float = 0.0
    quant_agent_index: float = 0.0
    tutoring_effectiveness_index: float = 0.0
    adaptiveness_score: float = 0.0
    process_mastery_score: float = 0.0
    results_by_task: dict[str, TaskResult] = Field(default_factory=dict)
    results_by_difficulty: dict[str, float] = Field(default_factory=dict)
    results_by_category: dict[str, float] = Field(default_factory=dict)
    # Layer 1 + cross-layer aggregation
    layers_evaluated: list[str] = Field(default_factory=list)
    layer1_results: Optional[list[dict]] = None
    layer1_summary: Optional[dict] = None
    layer1_mean_score: float = 0.0
    combined_result_subscore: Optional[float] = None
