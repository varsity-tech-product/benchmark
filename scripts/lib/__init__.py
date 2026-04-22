"""Shared utilities for the Quant Tutor Benchmark pipeline."""

from .llm_utils import MODELS, call_llm
from .schemas import (
    FinalBenchmarkRecord,
    LearnerProfile,
    StructuredQA,
    TutoringStrategy,
)

__all__ = [
    "StructuredQA",
    "LearnerProfile",
    "TutoringStrategy",
    "FinalBenchmarkRecord",
    "call_llm",
    "MODELS",
]

# Version
__version__ = "0.1.0"
