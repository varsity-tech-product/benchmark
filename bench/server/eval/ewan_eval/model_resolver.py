"""Model resolution — DeepEval-free, server-scoped.

Drop-in replacement for ``server.config.model_resolver.resolve_deepeval_model``.
Returns ``EwanLLMClient`` — no DeepEval classes.

Public API:
    resolve_ewan_model(model) → client object with a_generate(prompt) → (text, cost)
"""

import logging
import os
import random

from server.config.llm_config import (
    EVAL_DEFAULT_MODELS,
    EVAL_JUDGE_TEMPERATURE,
    OPENROUTER_BASE_URL,
    STUDENT_MODEL_POOL_ALL,
)
from server.config.pricing import _resolve_pricing
from server.eval.ewan_eval.llm_client import EwanLLMClient

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────


def resolve_ewan_model(model=None, *, temperature=EVAL_JUDGE_TEMPERATURE):
    """Resolve model name to LLM client object.

    Resolution order:
        1. If *model* is already a callable client, return as-is.
        2. OpenRouter (requires OPENROUTER_API_KEY env var).
        3. Fallback: return plain model name string.
    """
    if model is not None and not isinstance(model, (str, list)):
        return model

    if isinstance(model, list):
        model = random.choice(model)
    elif model is None:
        model = random.choice(EVAL_DEFAULT_MODELS)

    # Step 1: OpenRouter
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        or_model = model if "/" in model else f"openai/{model}"
        pricing = _resolve_pricing(or_model) or (0.0, 0.0)
        return EwanLLMClient(
            model=or_model,
            api_key=openrouter_key,
            base_url=OPENROUTER_BASE_URL,
            temperature=temperature,
            cost_per_input_token=pricing[0],
            cost_per_output_token=pricing[1],
        )

    # Step 2: Fallback — return plain string (caller must handle)
    if model.startswith("openai/"):
        return model[len("openai/") :]
    return model


def require_ewan_model(
    model=None,
    *,
    purpose: str = "LLM component",
    temperature=EVAL_JUDGE_TEMPERATURE,
):
    """Resolve a model and require a concrete callable client object."""
    resolved = resolve_ewan_model(
        model,
        temperature=temperature,
    )
    if hasattr(resolved, "generate"):
        return resolved

    model_name = model or resolved or "default"
    raise RuntimeError(
        f"Unable to initialize the {purpose} model ({model_name}). "
        "No usable model client was configured. Set OPENROUTER_API_KEY in "
        "the server environment or choose a simulator model backed by a "
        "configured provider."
    )


def require_student_model(
    model=None,
    *,
    temperature=EVAL_JUDGE_TEMPERATURE,
):
    """Resolve a student simulator model — must be vision-capable.

    Only models in ``STUDENT_MODEL_POOL_ALL`` are accepted.  This ensures
    the student LLM can handle image attachments.
    """
    from server.config.llm_config import SIMULATOR_DEFAULT_MODEL

    model = model or SIMULATOR_DEFAULT_MODEL

    if isinstance(model, str) and model not in STUDENT_MODEL_POOL_ALL:
        raise RuntimeError(
            f"Student simulator model {model!r} is not in the "
            f"vision-capable model pool. Choose from: "
            + ", ".join(sorted(STUDENT_MODEL_POOL_ALL))
        )

    return require_ewan_model(
        model,
        purpose="student simulator",
        temperature=temperature,
    )


# Backward-compat alias
resolve_deepeval_model = resolve_ewan_model
