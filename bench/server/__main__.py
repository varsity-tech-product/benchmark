"""QuantTutorBench HTTP Server entry point.

Usage::

    python -m server --port 8000 --docker
    python -m server --port 8000 --no-docker
    python -m server --port 8000 --docker --eval-model anthropic/claude-haiku-4-5

"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure bench/ is importable when launching as ``python -m server``.
_BENCH_ROOT = Path(__file__).parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from server.config.bootstrap import load_server_env

load_server_env(_BENCH_ROOT)


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
        default="anthropic/claude-haiku-4-5",
        help="Model for LLM-based evaluation (default: anthropic/claude-haiku-4-5)",
    )
    parser.add_argument(
        "--auto-eval",
        action="store_true",
        default=False,
        help="Automatically run evaluation when session completes (default: off)",
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

    app = create_app(
        use_docker=use_docker,
        bench_root=str(_BENCH_ROOT),
        eval_model=args.eval_model,
        auto_eval=args.auto_eval,
    )

    import uvicorn

    logger = logging.getLogger(__name__)
    logger.info(
        "Starting QuantTutorBench Server on %s:%d (docker=%s)",
        args.host,
        args.port,
        use_docker,
    )

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
