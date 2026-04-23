"""Model resolution for ewan_eval scoring pipeline.

Returns ``EwanLLMClient`` instances for LLM-based evaluation.

Public API:
    resolve_ewan_model(model) → client object with a_generate(prompt) → (text, cost)
    resolve_eval_model_list(model) → (list[str], bool)  — canonical model list + multi_model flag
    model_display_name(model) → stable string key for strings or client objects
"""

import logging
import random

from server.config.llm_config import (
    EVAL_DEFAULT_MODELS,
    EVAL_JUDGE_TEMPERATURE,
    STUDENT_MODEL_POOL_ALL,
    get_openrouter_base_url,
    has_openrouter_api_key,
)
from server.config.pricing import _resolve_pricing
from server.eval.judges.runtime.llm_client import EwanLLMClient

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Model list resolution (shared by Tutor 6D, QP, QR evaluators)
# ──────────────────────────────────────────────────────────────


def resolve_eval_model_list(model) -> tuple[list, bool]:
    """Resolve a model spec into (model_list, multi_model_flag).

    Args:
        model: Single model string, list of strings, or None.
            None → use EVAL_DEFAULT_MODELS.

    Returns:
        (eval_models, multi_model) — list of model identifiers and
        whether multiple models are in use.
    """
    if isinstance(model, list) and len(model) > 0:
        return model, len(model) > 1
    elif model is None:
        models = list(EVAL_DEFAULT_MODELS)
        return models, len(models) > 1
    return [model], False


def short_model_name(name: str) -> str:
    """Shorten 'provider/model-name' to 'model-name'."""
    return name.split("/")[-1] if "/" in name else name


def model_display_name(model) -> str:
    """Return a stable string key for model names or injected client objects."""
    if model is None:
        return "default"
    if isinstance(model, str):
        return model
    get_name = getattr(model, "get_model_name", None)
    if callable(get_name):
        name = get_name()
        if name:
            return str(name)
    name = getattr(model, "model", None)
    return str(name) if name else str(model)


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────


def resolve_ewan_model(model=None, *, temperature=EVAL_JUDGE_TEMPERATURE):
    """Resolve model name to LLM client object.

    Resolution order:
        1. If *model* is already a callable client, return as-is.
        2. OpenRouter (requires configured OPENROUTER_API_KEY).
        3. Fallback: return plain model name string.
    """
    if model is not None and not isinstance(model, (str, list)):
        return model

    if isinstance(model, list):
        model = random.choice(model)
    elif model is None:
        model = random.choice(EVAL_DEFAULT_MODELS)

    # Step 1: OpenRouter
    if has_openrouter_api_key():
        or_model = model if "/" in model else f"openai/{model}"
        pricing = _resolve_pricing(or_model) or (0.0, 0.0)
        return EwanLLMClient(
            model=or_model,
            base_url=get_openrouter_base_url(),
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
