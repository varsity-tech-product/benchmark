"""Impl B plugin bundle helpers."""

from __future__ import annotations

from pathlib import Path

from platform_api.plugins import PluginBundle, PluginLoader

IMPL_B_BUNDLE_CONFIG = Path(__file__).with_name("bundle.json")


def load_impl_b_bundle(
    *,
    bench_root: str | Path | None = None,
    eval_model: str | None = None,
    config_path: str | Path | None = None,
) -> PluginBundle:
    """Load the programmatic-only Impl B plugin bundle."""
    bundles = PluginLoader().load_config(config_path or IMPL_B_BUNDLE_CONFIG)
    if len(bundles) != 1:
        raise RuntimeError("Impl B config must define exactly one bundle")
    bundle = bundles[0]
    for component in (bundle.task_suite, bundle.npc_provider, bundle.evaluator):
        configure = getattr(component, "configure", None)
        if callable(configure):
            configure(bench_root=bench_root, eval_model=eval_model)
    return bundle


__all__ = ["IMPL_B_BUNDLE_CONFIG", "load_impl_b_bundle"]
