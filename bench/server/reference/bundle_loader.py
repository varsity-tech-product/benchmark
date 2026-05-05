"""Reference bundle loading helpers."""

from __future__ import annotations

from pathlib import Path

from platform_api.plugins import PluginBundle, PluginLoader


REFERENCE_BUNDLE_CONFIG = Path(__file__).with_name("bundle.json")


def load_reference_bundle(
    *,
    bench_root: str | Path | None = None,
    eval_model: str | None = None,
    config_path: str | Path | None = None,
) -> PluginBundle:
    """Load the reference PluginBundle and configure runtime-local paths."""

    bundles = PluginLoader().load_config(config_path or REFERENCE_BUNDLE_CONFIG)
    if len(bundles) != 1:
        raise RuntimeError("Reference bundle config must define exactly one plugin")
    bundle = bundles[0]
    for component in (
        bundle.task_suite,
        bundle.npc_provider,
        bundle.evaluator,
    ):
        configure = getattr(component, "configure", None)
        if callable(configure):
            configure(bench_root=bench_root, eval_model=eval_model)
    return bundle
