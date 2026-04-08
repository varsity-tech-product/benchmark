"""Public data types for QuantTutorBench Gym."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Observation:
    """What the agent sees after each interaction with the environment.

    Attributes:
        student_message: The student's latest message (empty string on tool results).
        available_tools: Tool schemas the agent can call.
        done: Whether the conversation has ended.
        turn: Current turn number (incremented on each send_message).
        max_turns: Hard turn cap for this task.
        info: Extra metadata (e.g. termination reason, TC coverage).
    """

    student_message: str
    available_tools: list[dict]
    done: bool = False
    turn: int = 0
    max_turns: int = 30
    info: dict = field(default_factory=dict)


@dataclass
class Scores:
    """Evaluation scores after a completed conversation.

    Attributes:
        overall: Overall Agent Score (OAS) — the headline number.
        quant_result: QR sub-score (result correctness).
        quant_process: QP sub-score (process quality).
        quant_agent: Quant Agent Score (0.50*QR + 0.50*QP).
        tutor: Tutor Score (weighted 7D average).
        tutor_dimensions: Per-dimension tutor scores {name: float}.
        process_metrics: Detailed process metric breakdown.
        cost_usd: Total eval cost.
        duration_seconds: Wall-clock time for evaluation.
        error: Error message if evaluation failed.
    """

    overall: float = 0.0
    quant_result: float = 0.0
    quant_process: float = 0.0
    quant_agent: float = 0.0
    tutor: float = 0.0
    tutor_dimensions: dict[str, float] = field(default_factory=dict)
    process_metrics: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    error: Optional[str] = None
