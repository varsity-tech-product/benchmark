"""Shared LEAN config builders for the reference generator and the session runner.

Both the host-side reference generator (`bench/reference_generator/generate_lean_reference.py`)
and the in-container session runner (`bench/docker/run_backtest.sh` via an inline
`python3 -c "..."`) must agree on a handful of keys that steer the LEAN Launcher —
`algorithm-type-name`, `algorithm-location`, `parameters`, `results-destination-folder`.
Any divergence between the two reproduces the empty-`algorithm-location` crash that
blocked session backtests on prod (see issue #33).

Design:

- `DEFAULT_FEES` / `DEFAULT_CUSTOM_DATA_ROOT` — single source of truth for the
  default fee rates and custom-data path. Reference generator and session runner
  both consume these.
- `resolve_algorithm_type_name()` — one rule for how a class name becomes a fully
  qualified `algorithm-type-name`, with the same `full_type_name` override semantics.
- `apply_session_overrides()` — mutates an existing config dict (the seed the
  session runner loads from the Dockerfile-shipped template) with every key a
  session-mode backtest needs. Safe to call on a freshly built reference config
  too, so the reference generator can route through it once we converge further.

This module ships inside the container at `/lean/helpers/lean_config.py` (copied by
`Dockerfile.lean`) so the in-container python3 heredoc in `run_backtest.sh` can
import it without relying on the full `bench/` tree being present at runtime.
"""

from __future__ import annotations

DEFAULT_FEES: dict[str, str] = {
    "maker-fee-rate": "0.0002",
    "taker-fee-rate": "0.0005",
}

DEFAULT_CUSTOM_DATA_ROOT: str = "/data/custom/binance"

DEFAULT_ALGORITHM_NAMESPACE: str = "QuantConnect.Algorithm.CSharp"


def resolve_algorithm_type_name(
    class_name: str = "",
    full_type_name: str = "",
) -> str:
    """Return the fully qualified type name LEAN's Launcher looks up.

    An explicit ``full_type_name`` wins; otherwise ``class_name`` is prefixed
    with the default namespace to match the shipped project layout. Falls back
    to ``Algorithm`` if neither is supplied, preserving legacy behaviour.
    """
    if full_type_name:
        return full_type_name
    name = class_name or "Algorithm"
    return f"{DEFAULT_ALGORITHM_NAMESPACE}.{name}"


def apply_session_overrides(
    cfg: dict,
    class_name: str = "",
    full_type_name: str = "",
    dll_path: str = "",
    results_dir: str = "",
    parameters: dict | None = None,
) -> dict:
    """Mutate ``cfg`` with every key a session-mode backtest needs.

    The seed config loaded from the Dockerfile-shipped template carries the
    structural scaffolding (``environments``, handler names, data paths) but
    leaves the per-run values empty — this helper fills them in. Returns the
    same dict so callers can chain or reassign.
    """
    cfg["algorithm-type-name"] = resolve_algorithm_type_name(class_name, full_type_name)
    if dll_path:
        cfg["algorithm-location"] = dll_path
    if results_dir:
        cfg["results-destination-folder"] = results_dir

    merged: dict = dict(DEFAULT_FEES)
    merged["custom-data-root"] = DEFAULT_CUSTOM_DATA_ROOT
    if parameters:
        merged.update(parameters)
    cfg["parameters"] = merged
    return cfg
