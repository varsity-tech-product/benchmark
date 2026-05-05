"""QuantTutorBench HTTP Server entry point.

Usage::

    python -m server --port 8000 --docker
    python -m server --port 8000 --no-docker
    python -m server --port 8000 --docker --eval-model MODEL

"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure bench/ is importable when launching as ``python -m server``.
_BENCH_ROOT = Path(__file__).parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from server.config.bootstrap import load_server_env
from server.config.llm_config import EVAL_DEFAULT_MODEL
from server.reference import REFERENCE_BUNDLE_CONFIG

load_server_env(_BENCH_ROOT)


def _load_plugin_bundle(config_path: str | Path, *, eval_model: str):
    from platform_api.plugins import PluginLoader

    bundles = PluginLoader().load_config(config_path)
    if len(bundles) != 1:
        raise RuntimeError(
            f"Plugin config must define exactly one bundle: {config_path}"
        )
    bundle = bundles[0]
    for component in (
        bundle.task_suite,
        bundle.npc_provider,
        bundle.evaluator,
    ):
        configure = getattr(component, "configure", None)
        if callable(configure):
            configure(bench_root=_BENCH_ROOT, eval_model=eval_model)
    return bundle


def main():
    parser = argparse.ArgumentParser(
        description="QuantTutorBench HTTP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python -m server --port 8000 --docker\n"
            "  python -m server --port 8000 --no-docker --log-level DEBUG\n"
        ),
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)"
    )
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument(
        "--docker", action="store_true", help="Use Docker sandboxes (recommended)"
    )
    parser.add_argument(
        "--no-docker", action="store_true", help="Run without Docker (local execution)"
    )
    parser.add_argument(
        "--eval-model",
        default=EVAL_DEFAULT_MODEL,
        help=f"Model for LLM-based evaluation (default: {EVAL_DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--plugin-config",
        default=os.environ.get("QTB_PLUGIN_CONFIG", str(REFERENCE_BUNDLE_CONFIG)),
        help="Plugin bundle config path",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()
    use_docker = not args.no_docker if args.no_docker else args.docker

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from server.api.http_app import create_app

    plugin_bundle = _load_plugin_bundle(args.plugin_config, eval_model=args.eval_model)
    app = create_app(
        use_docker=use_docker,
        bench_root=str(_BENCH_ROOT),
        eval_model=args.eval_model,
        plugin_bundle=plugin_bundle,
    )

    import uvicorn

    logger = logging.getLogger(__name__)
    logger.info(
        "Starting QuantTutorBench Server on %s:%d (docker=%s, plugin=%s)",
        args.host,
        args.port,
        use_docker,
        plugin_bundle.name,
    )

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
