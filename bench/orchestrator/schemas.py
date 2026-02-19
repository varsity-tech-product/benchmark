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


class RequiredCapability(BaseModel):
    description: str
    tool: Optional[str] = None
    tool_any_of: Optional[list[str]] = None
    output_contains: Optional[str] = None
    evidence: Optional[str] = None


class QuantValidation(BaseModel):
    eval_script: str
    expected_metrics: dict


class GroundTruth(BaseModel):
    expected_outcome: str
    required_capabilities: list[RequiredCapability] = Field(default_factory=list)
    expected_mcp_tools: list[str] = Field(default_factory=list)
    quant_validation: Optional[QuantValidation] = None
    bug_description: Optional[str] = None
    expected_fix: Optional[str] = None


class EnvironmentConfig(BaseModel):
    data_files: list[str] = Field(default_factory=list)
    core_mcp_tools: list[str] = Field(default_factory=list)
    distractor_mcp_tools_pool: list[str] = Field(default_factory=list)
    num_distractors: int = 5
    docs_available: list[str] = Field(default_factory=list)
    sandbox_image: str = "quant-tutor-env:v1.0"


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
    sample_code: Optional[str] = None
    max_turns: int = 30
    timeout_minutes: int = 15


class StudentPersona(BaseModel):
    persona_id: str
    knowledge_level: str
    description: str
    known_concepts: list[str] = Field(default_factory=list)
    unknown_concepts: list[str] = Field(default_factory=list)
    emotional_profile: str = ""
    behavioral_rules: list[str] = Field(default_factory=list)


class RubricDimension(BaseModel):
    weight: float = 1.0
    criteria: str
    scoring_guidance: dict[str, str] = Field(default_factory=dict)


class TutoringRubric(BaseModel):
    persona_level: str
    dimensions: dict[str, RubricDimension]


class MCPToolCallRecord(BaseModel):
    """Record of a single MCP tool call from the proxy layer."""

    name: str
    args: dict = Field(default_factory=dict)
    result: Optional[str] = None
    timestamp: Optional[str] = None
    duration_ms: Optional[float] = None
    success: bool = True
    turn_index: Optional[int] = None


class ConversationTurn(BaseModel):
    """A single turn in the conversation."""

    role: str  # "user" or "assistant"
    content: str
    tool_calls: list[MCPToolCallRecord] = Field(default_factory=list)


class TaskResult(BaseModel):
    """Result of running a single task evaluation."""

    task_id: str
    persona_id: str
    run_index: int = 0
    difficulty: str = ""  # §6.4: needed for difficulty curve computation
    category: str = ""  # §6.4: needed for per-category aggregation
    turns: list[ConversationTurn] = Field(default_factory=list)
    tool_call_log: list[MCPToolCallRecord] = Field(default_factory=list)
    quant_result_score: float = 0.0
    quant_process_score: float = 0.0
    tutor_scores: dict[str, float] = Field(default_factory=dict)
    overall_score: float = 0.0
    # Extended metrics (design doc §6.1, §6.4)
    tool_metrics: dict = Field(default_factory=dict)  # precision, recall, f1
    process_metrics: dict = Field(
        default_factory=dict
    )  # DeepEval process metric scores
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    error: Optional[str] = None


class BenchmarkReport(BaseModel):
    """Aggregate benchmark results."""

    agent_name: str
    total_tasks: int = 0
    overall_agent_score: float = 0.0
    quant_agent_index: float = 0.0
    tutoring_effectiveness_index: float = 0.0
    adaptiveness_score: float = 0.0
    tool_mastery_score: float = 0.0
    results_by_task: dict[str, TaskResult] = Field(default_factory=dict)
    results_by_difficulty: dict[str, float] = Field(default_factory=dict)
    results_by_category: dict[str, float] = Field(default_factory=dict)
    # Layer 1 + cross-layer aggregation
    layers_evaluated: list[str] = Field(default_factory=list)
    layer1_results: Optional[list[dict]] = None
    layer1_summary: Optional[dict] = None
    layer1_mean_score: float = 0.0
    combined_result_subscore: Optional[float] = None
