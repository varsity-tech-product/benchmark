"""QuantTutorBench core data models."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional, Union
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, model_validator


SUPPORTED_DATA_URI_SCHEMES = frozenset({"hf", "s3", "file"})
HF_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _hf_commit_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    ref = f"{parsed.netloc}{parsed.path}".strip("/")
    if "@" not in ref:
        return ""
    revision = ref.rsplit("@", 1)[1]
    return revision.split("/", 1)[0]


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TaskCategory(str, Enum):
    # v3.0 L2 categories (multi-turn dialog scenarios)
    DIAGNOSTIC = "diagnostic"
    END_TO_END = "end_to_end"
    ADVERSARIAL = "adversarial"
    # v3.0 L1 categories (single-shot agent execution)
    ALPHA_RESEARCH = "alpha_research"
    BACKTEST_ENGINE = "backtest_engine"
    DATA_ENGINEERING = "data_engineering"
    DEBUG = "debug"
    IMPLEMENTATION = "implementation"
    # Legacy (v2.2) categories — kept so legacy_v22 task files still parse.
    DATA_ANALYSIS = "data_analysis"
    STRATEGY = "strategy"
    BACKTEST = "backtest"
    CONCEPTUAL_QA = "conceptual_qa"
    STRATEGY_EXPLANATION = "strategy_explanation"
    CODE_GENERATION = "code_generation"
    CODE_DEBUGGING = "code_debugging"
    DATA_INTERPRETATION = "data_interpretation"
    MULTI_STEP_REASONING = "multi_step_reasoning"


class TaskType(str, Enum):
    # v3.0
    AGENT_EXECUTION = "agent_execution"  # L1 single-shot agentic task
    MULTI_TURN_DIALOG = "multi_turn_dialog"  # L2 NPC dialog
    # Legacy (v2.2)
    SINGLE_TURN = "single_turn"
    MULTI_TURN = "multi_turn"


class QuantValidation(BaseModel):
    eval_script: str


class DataMount(BaseModel):
    uri: str
    target_path: str
    read_only: bool = True

    @model_validator(mode="after")
    def _validate_mount(self):
        self.uri = self.uri.strip()
        self.target_path = self.target_path.strip()
        if not self.uri:
            raise ValueError("data_mount.uri is required")
        if not self.target_path.startswith("/"):
            raise ValueError("data_mount.target_path must be an absolute sandbox path")

        parsed = urlparse(self.uri)
        scheme = parsed.scheme.lower()
        if scheme not in SUPPORTED_DATA_URI_SCHEMES:
            supported = ", ".join(sorted(SUPPORTED_DATA_URI_SCHEMES))
            raise ValueError(f"unsupported data URI scheme {scheme!r}; expected {supported}")
        if scheme == "hf":
            commit = _hf_commit_from_uri(self.uri)
            if not commit or not HF_COMMIT_RE.fullmatch(commit):
                raise ValueError(
                    "hf:// data mounts require an explicit @commit SHA "
                    "(40 hex characters)"
                )
        if scheme == "s3" and not parse_qs(parsed.query).get("versionId"):
            raise ValueError("s3:// data mounts require a versionId query parameter")
        return self


class SandboxSpec(BaseModel):
    image_uri: str
    resource_limits: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_spec(self):
        self.image_uri = self.image_uri.strip()
        if not self.image_uri:
            raise ValueError("sandbox_spec.image_uri is required")
        return self


class GroundTruth(BaseModel):
    termination_criteria: Union[str, dict[str, str]] = ""
    required_capabilities: list[str] = Field(default_factory=list)
    expected_mcp_tools: list[str] = Field(default_factory=list)
    convenient_tools: list[str] = Field(default_factory=list)
    quant_validation: Optional[QuantValidation] = None
    # v3.0 L1 fields — flat verification spec consumed by l1_verifier.
    verification_script: Optional[str] = None
    expected_outputs: list = Field(default_factory=list)
    expected_outcome: Optional[str] = None  # L2 prose description


class EnvironmentConfig(BaseModel):
    data_files: list[str] = Field(default_factory=list)
    data_mounts: list[DataMount] = Field(default_factory=list)
    core_mcp_tools: list[str] = Field(default_factory=list)
    docs_available: list[str] = Field(default_factory=list)
    sandbox_image: str = "quant-tutor-env:v2.2"
    sandbox_spec: Optional[SandboxSpec] = None
    # Maximum backtest trials for I-series tasks (0 = no trial system).
    max_backtest_trials: int = 0
    # Whether this task requires outbound internet access inside the sandbox.
    # Default is False for reproducibility/safety.
    network_enabled: bool = False

    @property
    def sandbox_image_uri(self) -> str:
        if self.sandbox_spec is not None:
            return self.sandbox_spec.image_uri
        return self.sandbox_image

    @property
    def sandbox_resource_limits(self) -> dict[str, Any]:
        limits = (
            dict(self.sandbox_spec.resource_limits)
            if self.sandbox_spec is not None
            else {}
        )
        if "network_enabled" not in limits and self.network_enabled:
            limits["network_enabled"] = True
        return limits


class QuantTutorTask(BaseModel):
    """Unified task format for both Layer 1 (single-turn) and Layer 2 (multi-turn).

    Layer 1 tasks use: description (as question), context, reference_answer,
    synthetic_response, and minimal ground_truth.
    Layer 2 tasks use: persona_id, user_opening, environment, and full
    ground_truth with required_capabilities.
    """

    task_id: str
    version: str = "1.0"
    layer: Optional[str] = None  # "L0" / "L1" / "L2" in v3.0; absent for legacy v2.2
    difficulty: Optional[Difficulty] = None  # L0 knowledge primitives have no difficulty notion
    category: TaskCategory
    subcategory: Optional[str] = None
    rubric_profile: Optional[str] = None
    task_type: TaskType = TaskType.MULTI_TURN
    description: str = ""  # L0 uses ``question`` instead
    # v3.0 L0 — single-turn knowledge Q&A
    question: Optional[str] = None
    # v3.0 L1 — single-shot agentic prompt
    agent_prompt: Optional[str] = None
    # Legacy v2.2 layer-1 Q&A fields
    context: Optional[str] = None
    reference_answer: Optional[str] = None
    synthetic_response: Optional[str] = None
    source_dataset: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    # Persona / opening — single value per #122 (collapsed from persona_ids[] + user_openings{})
    persona_id: str = ""
    user_opening: str = ""
    environment: Optional[EnvironmentConfig] = None
    ground_truth: Optional[GroundTruth] = None
    requires_code: bool = False
    requires_tool: bool = False
    sample_code: Optional[str] = None
    max_turns: int = 30
    timeout_minutes: int = 15
    seed: Optional[int] = (
        None  # Reproducibility seed; overrides hash(task_id_run_index)
    )


class UserPersona(BaseModel):
    persona_id: str
    knowledge_level: str
    description: str
    familiar_concepts: dict[str, list[str]] | list[str] = Field(default_factory=dict)
    unfamiliar_concepts: dict[str, list[str]] | list[str] = Field(default_factory=dict)
    emotional_profile: str = ""
    behavioral_rules: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_concept_keys(cls, data):
        """Hard-fail on pre-2026-04-24 field names so renamed-schema silently
        dropping legacy keys (Pydantic default ``extra='ignore'``) can never
        produce a user with empty knowledge boundaries. See the 2026-04-24
        persona migration: ``known_concepts``/``unknown_concepts`` were
        renamed to ``familiar_concepts``/``unfamiliar_concepts``.
        """
        if isinstance(data, dict):
            for legacy, current in (
                ("known_concepts", "familiar_concepts"),
                ("unknown_concepts", "unfamiliar_concepts"),
            ):
                if legacy in data:
                    raise ValueError(
                        f"UserPersona received legacy field {legacy!r}; "
                        f"rename to {current!r} (2026-04-24 schema migration)."
                    )
        return data


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
    quant_result_score: Optional[float] = None
    quant_process_score: Optional[float] = None
    overall_score: Optional[float] = None
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
    trial_metadata: dict = Field(default_factory=dict)  # Trial system results
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
    eval_mode: str = "full"  # "full" | "qr_only" | "qp_only"


class BenchmarkReport(BaseModel):
    """Aggregate benchmark results."""

    agent_name: str
    total_tasks: int = 0
    overall_agent_score: Optional[float] = None
    quant_agent_index: Optional[float] = None
    process_mastery_score: Optional[float] = None
    results_by_task: dict[str, TaskResult] = Field(default_factory=dict)
    results_by_difficulty: dict[str, float] = Field(default_factory=dict)
    results_by_category: dict[str, float] = Field(default_factory=dict)
    # Layer 1 + cross-layer aggregation
    layers_evaluated: list[str] = Field(default_factory=list)
    layer1_results: Optional[list[dict]] = None
    layer1_summary: Optional[dict] = None
    layer1_mean_score: Optional[float] = None
    combined_result_subscore: Optional[float] = None
