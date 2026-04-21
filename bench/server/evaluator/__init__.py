"""Offline evaluator for QuantTutorBench bundles.

Reads bundles produced by ``server.storage.result_writer.save_run_state``
and writes scores to a parallel ``evaluations/server/...`` tree, decoupled
from any live session. The legacy in-session
``server.storage.eval_writer.run_evaluation`` now delegates here, so both
paths share the same scoring driver and output layout.

See ``server/storage/BUNDLE_SCHEMA.md`` for the bundle contract and issue
#46 for the producer/consumer split this module implements.
"""

from server.evaluator.single import score_bundle

__all__ = ["score_bundle"]
