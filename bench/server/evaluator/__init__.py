"""Offline evaluator for QuantTutorBench bundles.

Reads bundles produced by ``server.storage.result_writer.save_run_state``
and writes scores to a parallel ``evaluations/server/...`` tree.

Entry points:

- ``score_bundle`` — score one bundle synchronously (REST + CLI single mode).
- ``run_campaign`` — batch driver that fans ``score_bundle`` over many
  bundles with concurrency and idempotency (issue #47).

See ``server/storage/BUNDLE_SCHEMA.md`` for the bundle contract.
"""

from server.evaluator.batch import (
    BundleOutcome,
    CampaignSummary,
    resolve_bundles,
    run_campaign,
)
from server.evaluator.single import score_bundle

__all__ = [
    "score_bundle",
    "run_campaign",
    "resolve_bundles",
    "BundleOutcome",
    "CampaignSummary",
]
