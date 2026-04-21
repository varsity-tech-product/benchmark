"""Offline evaluator for QuantTutorBench bundles.

Reads bundles produced by ``server.storage.result_writer.save_run_state``
and writes scores to a parallel ``evaluations/server/...`` tree. After
#46 slice 4 this is the single scoring entry point — the operator REST
surface (``/ops/session/{sid}/evaluate``) and the CLI
(``python -m server.evaluator``) both call ``score_bundle`` directly.
The live session no longer holds any eval state.

See ``server/storage/BUNDLE_SCHEMA.md`` for the bundle contract and issue
#46 for the producer/consumer split this module implements.
"""

from server.evaluator.single import score_bundle

__all__ = ["score_bundle"]
