"""Deterministic config hash for batch idempotency.

Two campaigns with the same ``(judge, eval_mode, tutor_dims, rubric_version,
formula_version)`` produce the same hash, so ``batch.filter_pending`` can
skip bundles that already carry a matching ``eval_meta.json``. Any change
to the judge or the scoring definition yields a different hash and the
bundle is re-scored.

``CONFIG_HASH_VERSION`` prefixes the serialized payload so bumping the
hash algorithm or widening the inputs invalidates prior hashes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional

CONFIG_HASH_VERSION = "1"


def compute_config_hash(
    *,
    judge: str,
    eval_mode: str,
    tutor_dims: Optional[Iterable[str]] = None,
    rubric_version: str = "",
    formula_version: str = "",
) -> str:
    payload = {
        "v": CONFIG_HASH_VERSION,
        "judge": judge,
        "eval_mode": eval_mode,
        "tutor_dims": sorted(tutor_dims) if tutor_dims else [],
        "rubric_version": rubric_version,
        "formula_version": formula_version,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
