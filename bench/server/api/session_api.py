"""Session lifecycle manager for QuantTutorBench HTTP Server.

Each MCP HTTP session maps to one ``SessionState`` instance that manages:
- Task + persona loading (random persona selection)
- Container + tool setup
- Session lifecycle (user simulation, termination checking)
- Result saving (run_state.json + agent_files/)
- Internal evaluation triggering (background thread; server/operator only)

Thread safety:
- MCP processes requests sequentially per session, so ``handle_tool_call``
  is never called concurrently within a single session.
- ``_run_evaluation`` runs in a separate daemon thread.  Access to
  ``_eval_status`` / ``_eval_results`` is guarded by ``_eval_lock``.
- ``_last_activity`` is updated on every tool call for idle-timeout
  detection by the ``BenchSessionManager`` sweeper.

"""

import asyncio
import hashlib
import json
import logging
import os
import random
import shutil
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import anyio
from mcp.types import TextContent, Tool
from platform_api.contracts import EvalItem, EvalSample, ToolLog, TranscriptMessage
from platform_api.plugins import PluginBundle

from server.config.llm_config import EVAL_DEFAULT_MODEL
from server.reference import load_reference_bundle

if TYPE_CHECKING:
    from mcp.server import Server

from .limits import HEAVY_TOOLS, backtest_sem
from .protocol import (
    GET_BACKGROUND_TOOL,
    REGISTER_SESSION_TOOL,
    SEND_MESSAGE_TOOL,
    SESSION_API_TOOLS,
    START_SESSION_TOOL,
    SessionPhase,
    check_permission,
    make_error_response,
    next_allowed_for_phase,
)

logger = logging.getLogger(__name__)


async def _run_state_sync(use_docker: bool, func, *args, **kwargs):
    if not use_docker:
        return await _run_sync_worker(func, *args, **kwargs)
    if kwargs:
        return await anyio.to_thread.run_sync(lambda: func(*args, **kwargs))
    return await anyio.to_thread.run_sync(func, *args)


async def _run_sync_worker(func, *args, **kwargs):
    done = threading.Event()
    outcome: dict[str, object] = {}

    def runner() -> None:
        try:
            outcome["value"] = func(*args, **kwargs)
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=runner, name="qtb-session-worker", daemon=True)
    thread.start()
    while not done.is_set():
        await anyio.sleep(0.01)
    thread.join()
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome.get("value")


def _stable_int_seed(*parts: object) -> int:
    """Build a stable non-negative integer seed from arbitrary values."""
    material = "::".join(str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _session_random_seed(
    task_id: str, session_id: str, task_seed: Optional[int]
) -> int:
    """Return the internal RNG seed for this session.

    Precedence:
    1. ``QTB_TEST_RANDOM_SEED`` for reproducible internal regression tests
    2. task-level seed from the task definition
    3. stable hash of task_id + session_id
    """
    test_seed = os.environ.get("QTB_TEST_RANDOM_SEED", "").strip()
    if test_seed:
        return _stable_int_seed("qtb-test-seed", test_seed, task_id)
    if task_seed is not None:
        return int(task_seed)
    return _stable_int_seed(task_id, session_id)


def _resolve_persona_pin(
    task_id: str, persona_ids: Optional[list[str]] = None
) -> Optional[str]:
    """Return an internal-only pinned persona override, if configured."""
    allowed = set(persona_ids or [])

    def _allowed(value: str) -> Optional[str]:
        if allowed and value not in allowed:
            return None
        return value

    raw_json = os.environ.get("QTB_TEST_PERSONA_PIN_JSON", "").strip()
    if raw_json:
        try:
            mapping = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid QTB_TEST_PERSONA_PIN_JSON")
        else:
            if isinstance(mapping, dict):
                desired = mapping.get(task_id) or mapping.get("*")
                if desired:
                    return _allowed(str(desired))

    desired = os.environ.get("QTB_TEST_PERSONA_PIN", "").strip()
    return _allowed(desired) if desired else None


def _task_is_lean(task) -> bool:
    environment = getattr(task, "environment", None)
    if environment is None:
        return False
    sandbox_image = _environment_sandbox_image(environment).lower()
    core_tools = list(getattr(environment, "core_mcp_tools", []) or [])
    return "lean" in sandbox_image or "run_lean_backtest" in core_tools


def _environment_sandbox_image(environment) -> str:
    if environment is None:
        return ""
    spec = getattr(environment, "sandbox_spec", None)
    image_uri = str(getattr(spec, "image_uri", "") or "").strip()
    if image_uri:
        return image_uri
    return str(getattr(environment, "sandbox_image", "") or "")


def _environment_resource_limits(environment) -> dict:
    spec = getattr(environment, "sandbox_spec", None)
    limits = getattr(spec, "resource_limits", None)
    resolved = dict(limits or {})
    if "network_enabled" not in resolved and bool(
        getattr(environment, "network_enabled", False)
    ):
        resolved["network_enabled"] = True
    return resolved


def _environment_network_enabled(environment) -> bool:
    limits = _environment_resource_limits(environment)
    value = limits.get("network_enabled")
    if value is None:
        return bool(getattr(environment, "network_enabled", False))
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _environment_sandbox_digest(environment) -> dict:
    if environment is None:
        return {}
    from platform_api.contracts import DataMount
    from platform_api.runtime import build_sandbox_digest

    data_mounts = []
    for item in getattr(environment, "data_mounts", []) or []:
        if hasattr(item, "model_dump"):
            payload = item.model_dump()
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            payload = {
                "uri": getattr(item, "uri"),
                "target_path": getattr(item, "target_path"),
                "read_only": getattr(item, "read_only", True),
            }
        data_mounts.append(
            DataMount(
                uri=str(payload["uri"]),
                target_path=str(payload["target_path"]),
                read_only=bool(payload.get("read_only", True)),
            )
        )
    return build_sandbox_digest(
        _environment_sandbox_image(environment),
        resource_limits=_environment_resource_limits(environment),
        data_mounts=tuple(data_mounts),
    )


def _effective_core_tool_names(task) -> list[str]:
    environment = getattr(task, "environment", None)
    names = list(getattr(environment, "core_mcp_tools", []) or [])
    if _task_is_lean(task) and "get_lean_template" not in names:
        names.append("get_lean_template")
    return names


def _lean_template_type(task) -> str:
    task_id = str(getattr(task, "task_id", "") or "").upper()
    category = str(getattr(getattr(task, "category", None), "value", "") or "").lower()
    subcategory = str(getattr(task, "subcategory", "") or "").lower()
    if category == "debug" or getattr(task, "sample_code", None):
        return "debug"
    if "composite" in subcategory or "sweep" in subcategory:
        return "framework"
    if task_id.startswith(("L1_IMP_01", "L1_IMP_02", "L1_IMP_03")):
        return "multi_symbol"
    if any(token in subcategory for token in ("universe", "multi", "pairs")):
        return "multi_symbol"
    return "generic"


def _lean_template_context(task, *, user_code_dir: Optional[str | Path]) -> dict:
    environment = getattr(task, "environment", None)
    template_type = _lean_template_type(task)
    return {
        "category": task.category.value,
        "requires_code": bool(task.requires_code),
        "template_type": template_type,
        "expects_universe": template_type == "multi_symbol",
        "sandbox_image": _environment_sandbox_image(environment),
        "user_code_available": bool(user_code_dir),
    }


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _safe_tool_log_dict(log) -> dict:
    if isinstance(log, dict):
        return dict(log)
    if is_dataclass(log):
        return asdict(log)
    if hasattr(log, "__dict__"):
        return dict(log.__dict__)
    return {"raw": str(log)}


class SessionState:
    """Per-session state for one benchmark session.

    Lifecycle:
        1. Created by BenchSessionManager when a new MCP connection arrives.
        2. ``register(task_id)`` — loads task, picks persona, creates container.
        3. ``start()`` — returns user opening, enters IN_SESSION.
        4. ``handle_send_message(text)`` — routes through proxy, may complete.
        5. Server/operator evaluation may run after results are saved.
        6. ``cleanup()`` — destroys container and temp dirs.
    """

    def __init__(
        self,
        session_id: str,
        use_docker: bool = True,
        bench_root: Optional[Path] = None,
        eval_model: str = EVAL_DEFAULT_MODEL,
        plugin_bundle: PluginBundle | None = None,
    ):
        self.session_id = session_id
        self.phase = SessionPhase.UNREGISTERED
        self.use_docker = use_docker
        self.bench_root = (
            Path(bench_root) if bench_root else Path(__file__).parent.parent.parent
        )
        self.eval_model = eval_model
        self.plugin_bundle = plugin_bundle or load_reference_bundle(
            bench_root=self.bench_root,
            eval_model=eval_model,
        )

        # Task state (set during register)
        self.task = None
        self.eval_item: EvalItem | None = None
        self.persona = None
        self.task_id: str = ""
        self.persona_id: str = ""
        self._task_core_tool_names: tuple[str, ...] = ()
        self._task_convenient_tool_names: tuple[str, ...] = ()

        # Runtime components (set during register)
        self.proxy = None
        self.session = None  # Session
        self.container_manager = None
        self.container = None
        self.user_sim = None
        self.staged_temp_dirs: list = []

        # Timing
        self._start_time: Optional[float] = None
        self._last_activity: float = time.time()

        # Concurrency: serializes all write operations (MCP + REST).
        self._request_lock = asyncio.Lock()

        # Per-session MCP Server reference (set by http_app._create_mcp_server).
        # Used to send tools/list_changed notifications on phase transitions.
        self._mcp_server: Optional["Server"] = None

        # Run layer binding (set by http_app when connected via run token).
        self.run_id: Optional[str] = None
        self._run_task_id: Optional[str] = None  # task_id from RunAssignment
        self.owner_user_id: str = ""
        self.owner_github_login: str = ""
        self.owner_email: str = ""
        self.visibility: str = "private"
        self._latest_layer_tag: str = ""
        self._snapshot_tags: list[str] = []
        self._snapshot_interval: int = _int_env(
            "QTB_RESUME_SNAPSHOT_INTERVAL", 5, minimum=1
        )
        self._snapshot_keep: int = _int_env(
            "QTB_RESUME_SNAPSHOT_KEEP", 3, minimum=1
        )
        self._resume_layer_tag: str = ""
        self._suppress_active_persist: bool = False
        # Callback invoked after successful register(). Used by http_app to
        # bind the session to a RunAssignment without injecting RunService
        # into SessionState.
        self._on_registered: Optional[callable] = None
        # Callback invoked after session completion + result save.
        self._on_completed: Optional[callable] = None

        # Evaluation state (guarded by _eval_lock)
        self._eval_lock = threading.Lock()
        self._eval_status: str = "pending"  # pending | running | completed | failed
        self._eval_results: Optional[dict] = None
        self._eval_error: Optional[str] = None
        self._active_score_id: Optional[str] = None
        self._result_dir: Optional[Path] = None
        self._closed: bool = False

        # Evaluation parameters (set before internal evaluation if non-default)
        self._eval_mode: str = "full"
        self._eval_idempotency_key: Optional[str] = None

    # ------------------------------------------------------------------
    # Restore from server storage
    # ------------------------------------------------------------------

    @classmethod
    def restore_from_storage(
        cls,
        session_id: str,
        result_dir: Path,
        bench_root: Path,
        eval_model: str = EVAL_DEFAULT_MODEL,
        plugin_bundle: PluginBundle | None = None,
    ) -> "SessionState":
        """Restore a COMPLETED session from server storage (run_state.json).

        Server persists session data to disk.  When the session is no longer
        in memory (cleaned by sweeper or server restart), this method
        reconstructs enough state to service any API call: evaluate, get
        results, get scores.

        No container, proxy, or user simulator is needed — only the
        persisted conversation, tool logs, and task/persona metadata.
        """
        from types import SimpleNamespace

        run_state = json.loads(
            (result_dir / "run_state.json").read_text(encoding="utf-8")
        )
        task_id = run_state["task_id"]
        persona_id = run_state["persona_id"]

        state = cls(
            session_id=session_id,
            use_docker=False,
            bench_root=bench_root,
            eval_model=eval_model,
            plugin_bundle=plugin_bundle,
        )

        # Load task and persona from server data store
        state.task = state._load_task(task_id)
        state.persona = state._load_persona(persona_id)
        state.task_id = task_id
        state.persona_id = persona_id

        if not state.task or not state.persona:
            raise ValueError(
                f"Cannot restore session: task={task_id} found={state.task is not None}, "
                f"persona={persona_id} found={state.persona is not None}"
            )

        # Reconstruct in-memory objects from persisted data
        conversation = run_state.get("conversation", [])
        state.session = SimpleNamespace(conversation=conversation)

        tool_logs = run_state.get("tool_logs", [])
        tool_log_objs = [
            SimpleNamespace(**log) if isinstance(log, dict) else log
            for log in tool_logs
        ]
        distractor_names = run_state.get("distractor_names", [])
        state.proxy = SimpleNamespace(
            get_logs=lambda: tool_log_objs,
            get_distractor_names=lambda: distractor_names,
        )

        state._result_dir = result_dir
        state.phase = SessionPhase.COMPLETED
        state.run_id = str(run_state.get("run_id") or "")
        state.owner_user_id = str(run_state.get("owner_user_id") or "")
        state.owner_github_login = str(run_state.get("owner_github_login") or "")
        state.owner_email = str(run_state.get("owner_email") or "")
        state.visibility = str(run_state.get("visibility") or "private")

        # Check if evaluation was already run under the score_n store.
        try:
            from eval.storage.score_store import get_scores_payload

            payload = get_scores_payload(result_dir)
            if payload.get("status") == "completed":
                state._eval_status = "completed"
                state._active_score_id = payload.get("score_id")
                state._eval_results = payload.get("scores")
            elif payload.get("status") == "running":
                state._eval_status = "running"
                state._active_score_id = payload.get("score_id")
        except Exception:
            pass
        logger.info(
            "Restored session %s from storage: %s/%s",
            session_id[:8],
            task_id,
            persona_id,
        )
        return state

    @classmethod
    def restore_active_from_storage(
        cls,
        *,
        state_path: Path,
        bench_root: Path,
        use_docker: bool,
        eval_model: str = EVAL_DEFAULT_MODEL,
        plugin_bundle: PluginBundle | None = None,
    ) -> "SessionState":
        """Restore an ACTIVE session from results/runs/{run_id}/run_state.json."""
        run_state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        session_id = str(run_state["session_id"])
        task_id = str(run_state["task_id"])
        persona_id = str(run_state["persona_id"])

        state = cls(
            session_id=session_id,
            use_docker=use_docker,
            bench_root=bench_root,
            eval_model=eval_model,
            plugin_bundle=plugin_bundle,
        )
        state.run_id = str(run_state.get("run_id") or "")
        state._run_task_id = task_id
        state.owner_user_id = str(run_state.get("owner_user_id") or "")
        state.owner_github_login = str(run_state.get("owner_github_login") or "")
        state.owner_email = str(run_state.get("owner_email") or "")
        state.visibility = str(run_state.get("visibility") or "private")
        state._latest_layer_tag = str(run_state.get("latest_layer_tag") or "")
        state._resume_layer_tag = state._latest_layer_tag
        state._snapshot_tags = [
            str(tag) for tag in run_state.get("snapshot_tags", []) if tag
        ]
        state._snapshot_interval = max(
            1,
            int(run_state.get("snapshot_interval") or state._snapshot_interval),
        )
        state._snapshot_keep = max(
            1,
            int(run_state.get("snapshot_keep") or state._snapshot_keep),
        )

        state._suppress_active_persist = True
        try:
            registered = state.register(task_id, persona_id)
            if "error" in registered:
                raise RuntimeError(str(registered.get("error")))
        finally:
            state._suppress_active_persist = False

        snapshot_dir = Path(
            run_state.get("workspace_snapshot_path")
            or state_path.parent / "workspace_snapshot"
        )
        state._restore_active_workspace_snapshot(snapshot_dir)
        if state.user_sim is not None:
            state.user_sim.total_cost = float(run_state.get("simulator_cost") or 0.0)

        if state.proxy is not None and hasattr(state.proxy, "restore_logs"):
            state.proxy.restore_logs(run_state.get("tool_logs", []))

        turn_count = int(run_state.get("turn_count") or 0)
        if state.session is not None and hasattr(
            state.session, "restore_runtime_state"
        ):
            state.session.restore_runtime_state(
                conversation=run_state.get("conversation", []),
                turn_count=turn_count,
                session_status=str(run_state.get("session_status") or "active"),
                completion_reason=run_state.get("termination_reason"),
                file_ledger=run_state.get("file_ledger") or {},
                artifact_debug_history=run_state.get("artifact_debug_history") or [],
            )
        if state.proxy is not None and hasattr(state.proxy, "set_turn"):
            state.proxy.set_turn(turn_count)

        phase = str(run_state.get("phase") or SessionPhase.IN_SESSION.value)
        try:
            state.phase = SessionPhase(phase)
        except ValueError:
            state.phase = SessionPhase.IN_SESSION
        state._start_time = time.time() - float(run_state.get("duration_seconds") or 0)
        state._closed = False
        state._persist_active_state()
        logger.info(
            "Restored active session %s from %s using layer=%s",
            session_id[:8],
            state_path,
            state._latest_layer_tag or "none",
        )
        return state

    def _build_session_context(
        self,
        *,
        docs_available: list[str],
        user_code_dir: Optional[Path | str],
    ) -> dict:
        """Build the truthful runtime context exposed to tools."""
        environment = self.task.environment if self.task else None
        max_backtest_trials = environment.max_backtest_trials if environment else 0
        return {
            "category": self.task.category.value if self.task else "",
            "requires_code": bool(self.task.requires_code) if self.task else False,
            "docs_available": list(docs_available or []),
            "max_backtest_trials": max_backtest_trials,
            "sandbox_image": _environment_sandbox_image(environment),
            "user_code_available": bool(user_code_dir),
        }

    def _build_tool_env(
        self,
        *,
        data_dir: str | Path,
        docs_dir: str | Path,
        workspace_dir: str | Path,
        user_code_dir: Optional[str | Path],
        docs_available: list[str],
    ) -> dict[str, str]:
        """Build per-session tool environment variables."""
        context = self._build_session_context(
            docs_available=docs_available,
            user_code_dir=user_code_dir,
        )
        env = {
            "QTB_DATA_DIR": str(data_dir),
            "QTB_DOCS_DIR": str(docs_dir),
            "QTB_WORKSPACE_DIR": str(workspace_dir),
            "QTB_MAX_BACKTEST_TRIALS": str(context["max_backtest_trials"]),
            "LEAN_RUN_TIMEOUT": "300",
            "QTB_SESSION_CONTEXT_JSON": json.dumps(context),
        }
        if user_code_dir:
            env["QTB_USER_CODE_DIR"] = str(user_code_dir)
        if self.task and _task_is_lean(self.task):
            env["QTB_LEAN_TEMPLATE_CONTEXT_JSON"] = json.dumps(
                _lean_template_context(self.task, user_code_dir=user_code_dir)
            )
        return env

    # ------------------------------------------------------------------
    # register_session
    # ------------------------------------------------------------------

    def register(self, task_id: str, persona_id: Optional[str] = None) -> dict:
        """Handle ``register_session(task_id, persona_id?)``.

        Heavy operation: loads task, selects persona, creates container,
        registers tools, creates Session. If ``persona_id`` is
        provided, it overrides the default random persona selection.

        """
        from server.config.benchmark_config import DATASET_REVISION
        from server.config.bootstrap import load_server_env
        from server.config.llm_config import SIMULATOR_DEFAULT_MODEL
        from server.core.container import ContainerManager
        from server.core.proxy import MCPProxy
        from server.core.registry import populate_proxy_for_task
        from server.core.session import Session
        from server.core.staging import create_staged_dirs, create_staged_sample_code
        from server.core.user_sim import require_user_model
        from server.data_manager import ensure_data

        self._closed = False

        try:
            try:
                eval_item = self.plugin_bundle.task_suite.get_task(task_id)
                task = self._task_from_eval_item(eval_item)
            except Exception:
                logger.exception("Plugin task lookup failed for %s", task_id)
                eval_item = None
                task = None
            if task is None or eval_item is None:
                return {"error": f"Task not found: {task_id}"}

            self.task = task
            self.eval_item = eval_item
            self.task_id = task_id
            self._task_core_tool_names = tuple(_effective_core_tool_names(task))
            self._task_convenient_tool_names = tuple(
                task.ground_truth.convenient_tools if task.ground_truth else ()
            )

            if not task.persona_id:
                return {
                    "accepted": False,
                    "error": f"Task {task_id} has no persona_id",
                }

            session_seed = _session_random_seed(task_id, self.session_id, task.seed)
            selected_persona_id = persona_id.strip() if persona_id else None
            if selected_persona_id:
                logger.info(
                    "Session %s using explicitly requested persona=%s for task=%s",
                    self.session_id,
                    selected_persona_id,
                    task_id,
                )
            else:
                selected_persona_id = _resolve_persona_pin(task_id)
            if selected_persona_id is None:
                selected_persona_id = task.persona_id
            elif not persona_id:
                logger.info(
                    "Session %s using internally pinned persona=%s for task=%s",
                    self.session_id,
                    selected_persona_id,
                    task_id,
                )
            persona = self._load_persona(selected_persona_id)
            if persona is None:
                return {
                    "accepted": False,
                    "error": f"Persona not found: {selected_persona_id}",
                }
            self.persona = persona
            self.persona_id = selected_persona_id

            load_server_env(self.bench_root)
            build_user_simulator = getattr(
                self.plugin_bundle.npc_provider,
                "build_user_simulator",
                None,
            )
            resolved_sim_model = None
            if callable(build_user_simulator):
                try:
                    resolved_sim_model = require_user_model(
                        SIMULATOR_DEFAULT_MODEL,
                    )
                except RuntimeError as exc:
                    return {"error": str(exc)}

            sandbox_img = _environment_sandbox_image(task.environment)
            series = "lean" if sandbox_img and "lean" in sandbox_img else "normal"
            paths = ensure_data(
                series=series,
                revision=DATASET_REVISION,
                need_reference=False,
            )

            self.container_manager = ContainerManager(use_docker=self.use_docker)
            data_files = task.environment.data_files if task.environment else []
            docs_available = task.environment.docs_available if task.environment else []

            staged_data_dir, staged_docs_dir, self.staged_temp_dirs = (
                create_staged_dirs(
                    data_files,
                    docs_available,
                    data_search_dirs=paths.data_search_dirs,
                    docs_dir=paths.docs,
                    force_temp_data_dir=bool(paths.custom_data),
                )
            )

            user_code_dir = None
            if task.sample_code:
                user_code_dir, sample_temp_dirs = create_staged_sample_code(
                    task.sample_code,
                    data_search_dirs=paths.data_search_dirs,
                    user_code_dir=paths.user_code,
                )
                self.staged_temp_dirs.extend(sample_temp_dirs)

            base_sandbox_img = (
                _environment_sandbox_image(task.environment)
                if task.environment
                else None
            )
            self.container = self.container_manager.create_container(
                task_id=f"{task_id}_{self.session_id[:8]}",
                data_dir=staged_data_dir,
                docs_dir=staged_docs_dir,
                user_code_dir=user_code_dir,
                sandbox_image=(self._resume_layer_tag or base_sandbox_img),
                network_enabled=(
                    _environment_network_enabled(task.environment)
                    if task.environment
                    else False
                ),
                lean_data_dir=paths.lean_data,
                custom_data_dir=paths.custom_data,
                data_mounts=task.environment.data_mounts if task.environment else [],
                restore_workspace_snapshot=bool(self._resume_layer_tag),
                resource_image=base_sandbox_img,
                resource_limits=(
                    _environment_resource_limits(task.environment)
                    if task.environment
                    else None
                ),
            )

            local_tool_env = self._build_tool_env(
                data_dir=staged_data_dir,
                docs_dir=staged_docs_dir,
                workspace_dir=self.container.workspace_path,
                user_code_dir=user_code_dir,
                docs_available=docs_available,
            )
            container_tool_env = self._build_tool_env(
                data_dir="/data",
                docs_dir="/docs",
                workspace_dir="/workspace",
                user_code_dir="/user_code" if user_code_dir else None,
                docs_available=docs_available,
            )

            if self.container_manager.use_docker:
                self.container_manager.start_executor(
                    self.container.container_id,
                    env_vars=container_tool_env,
                )

            self.proxy = MCPProxy(workspace_path=self.container.workspace_path)
            populate_proxy_for_task(
                proxy=self.proxy,
                core_tool_names=list(self._task_core_tool_names),
                convenient_tool_names=(
                    task.ground_truth.convenient_tools if task.ground_truth else []
                ),
                seed=session_seed,
                container_manager=self.container_manager,
                container_id=self.container.container_id,
                workspace_path=self.container.workspace_path,
                use_docker=self.container_manager.use_docker,
                env_overrides=local_tool_env,
            )

            self.user_sim = None
            if callable(build_user_simulator):
                self.user_sim = build_user_simulator(
                    task,
                    self.persona,
                    model=resolved_sim_model,
                )

            effective_timeout = task.timeout_minutes
            deadline = None
            if effective_timeout and effective_timeout > 0:
                deadline = time.time() + effective_timeout * 60
            self.proxy.set_deadline(deadline)

            self.session = Session(
                task=task,
                persona=persona,
                user_sim=self.user_sim,
                max_turns=task.max_turns,
                deadline=deadline,
                proxy=self.proxy,
                workspace_path=self.container.workspace_path,
                npc_provider=self.plugin_bundle.npc_provider,
                eval_item=eval_item,
            )

            # Keep protocol traffic in raw logs; downstream reports decide what to hide.
            from server.api.protocol import SEND_MESSAGE_TOOL

            self.proxy.register_tool(
                name="send_message",
                func=self.session.handle_send_message,
                description=SEND_MESSAGE_TOOL.description,
                params=SEND_MESSAGE_TOOL.inputSchema,
            )

            self._start_time = time.time()
            self.phase = SessionPhase.REGISTERED

            logger.info(
                "Session %s registered: task=%s persona=%s docker=%s run=%s",
                self.session_id,
                task_id,
                self.persona_id,
                self.use_docker,
                self.run_id or "none",
            )

            # Notify Run layer (if connected via run token).
            if self._on_registered:
                try:
                    self._on_registered(self.session_id)
                except Exception as exc:
                    logger.warning(
                        "Session %s _on_registered callback failed: %s",
                        self.session_id,
                        exc,
                    )

            self._persist_active_state()
            return {
                "session_id": self.session_id,
                "current_phase": self.phase.value,
                "next_allowed": next_allowed_for_phase(self.phase),
            }
        except Exception as exc:
            logger.exception("Session %s register failed", self.session_id)
            self._reset_registration_state()
            return {"error": f"Registration failed: {exc}"}

    # ------------------------------------------------------------------
    # start_session
    # ------------------------------------------------------------------

    def start(self) -> dict:
        """Handle ``start_session()`` — return user opening + available tools.

        After phase transitions to IN_SESSION, the full tool list is
        included so the agent can start working without an extra
        list_tools round-trip.
        """
        if self._closed:
            return {"error": "Session is closed"}
        result = self.session.handle_start_session()
        self.phase = SessionPhase.IN_SESSION
        logger.info("Session %s started.", self.session_id)
        data = json.loads(result)
        data["tools"] = [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema,
            }
            for t in self.get_visible_tools()
        ]
        data["current_phase"] = self.phase.value
        data["next_allowed"] = next_allowed_for_phase(self.phase)
        self._persist_active_state()
        return data

    # ------------------------------------------------------------------
    # send_message (routed through proxy for logging)
    # ------------------------------------------------------------------

    def handle_send_message(
        self,
        text: str,
        attachments: list[str] | None = None,
        reasoning: str | None = None,
    ) -> str:
        """Handle ``send_message(text, attachments?, reasoning?)``.

        Routes through ``proxy.call_tool`` so the call is logged.
        Detects session completion and triggers result saving.

        ``reasoning`` is forwarded to the proxy (which records it in the
        tool log ``args``) and to the underlying session. It is never
        delivered to the user.

        Returns:
            Raw JSON string from Session (via proxy).
        """
        if self._closed:
            return json.dumps({"error": "Session is closed", "status": "closed"})

        proxy_kwargs: dict = {"text": text, "attachments": attachments or []}
        if reasoning:
            proxy_kwargs["reasoning"] = reasoning
        result = self.proxy.call_tool("send_message", **proxy_kwargs)

        # Parse once: drives completion handling and next_allowed injection.
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result

        session_status = data.get("status")
        if session_status in ("completed", "failed"):
            self.phase = SessionPhase.COMPLETED
            self._save_results()
            self._cleanup_resume_layers()
            self._remove_active_state()
            self._destroy_container()
            if session_status == "failed":
                logger.error(
                    "Session %s failed: reason=%s",
                    self.session_id,
                    data.get("reason", "unknown"),
                )
            else:
                logger.info(
                    "Session %s completed: reason=%s",
                    self.session_id,
                    data.get("reason", "unknown"),
                )
            self._trigger_auto_eval()
            # Notify Run layer
            if self._on_completed:
                try:
                    self._on_completed(
                        str(self._result_dir) if self._result_dir else None
                    )
                except Exception as exc:
                    logger.warning(
                        "Session %s _on_completed callback failed: %s",
                        self.session_id,
                        exc,
                    )
        data.setdefault("current_phase", self.phase.value)
        data.setdefault("next_allowed", next_allowed_for_phase(self.phase))
        if self.phase != SessionPhase.COMPLETED:
            self._persist_active_state()
        return json.dumps(data)

    # ------------------------------------------------------------------
    # Internal server-side evaluation trigger
    # ------------------------------------------------------------------

    def _trigger_auto_eval(self) -> None:
        """Enqueue a server-internal eval after a terminal transition.

        Idempotent on the in-memory ``_eval_status`` guard and on the
        ``auto:{session_id}`` key in the score store, so duplicate triggers
        (e.g. an idle-sweep racing with handle_send_message) collapse onto
        the same score record. Called by the public REST completion paths;
        operators can still run additional evals via ``ops_evaluate`` with
        a different idempotency key.
        """
        if self.phase != SessionPhase.COMPLETED or not self._result_dir:
            return
        with self._eval_lock:
            if self._eval_status != "pending":
                return
        try:
            # Pass auto params explicitly so a concurrent ops_evaluate
            # mutation of _eval_mode / _eval_idempotency_key / eval_model
            # cannot leak into score_1.
            result = self.request_evaluation(
                eval_mode="full",
                eval_model=self.eval_model,
                idempotency_key=f"auto:{self.session_id}",
            )
        except Exception as exc:
            logger.warning(
                "[auto-eval:%s] enqueue failed: %s", self.session_id[:8], exc
            )
            return
        logger.info(
            "[auto-eval:%s] status=%s score_id=%s",
            self.session_id[:8],
            result.get("status"),
            result.get("score_id"),
        )

    def request_evaluation(
        self,
        *,
        eval_mode: str | None = None,
        eval_model: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Start an internal evaluation run for a completed result bundle.

        Each non-concurrent call appends a new score_n evaluation run.
        Concurrent calls return the active running score instead of starting
        a second run. When ``eval_mode`` / ``eval_model`` / ``idempotency_key``
        are passed explicitly the call ignores instance-state defaults — used
        by ``_trigger_auto_eval`` so its parameters cannot be perturbed by a
        racing operator request that mutated the instance fields.
        """
        if self._closed:
            return {
                "status": "failed",
                "error": "Session is closed",
                "current_phase": self.phase.value,
            }
        if not self._result_dir:
            return {
                "status": "failed",
                "error": "Results are not available for evaluation",
                "current_phase": self.phase.value,
            }
        with self._eval_lock:
            if self._eval_status == "running":
                return self._decorate_eval_payload(
                    {
                        "status": "running",
                        "score_id": self._active_score_id,
                        "message": "Evaluation in progress.",
                    }
                )

            from eval.contracts.request import parse_eval_request
            from eval.storage.score_store import allocate_score_run

            request = parse_eval_request(
                {
                    "session_id": self.session_id,
                    "eval_mode": (
                        eval_mode if eval_mode is not None else self._eval_mode
                    ),
                    "eval_model": (
                        eval_model if eval_model is not None else self.eval_model
                    ),
                    "idempotency_key": (
                        idempotency_key
                        if idempotency_key is not None
                        else self._eval_idempotency_key
                    ),
                }
            )
            self._eval_mode = request.eval_mode
            self.eval_model = request.eval_model or self.eval_model
            run, created = allocate_score_run(
                self._result_dir,
                eval_mode=request.eval_mode,
                eval_model=request.eval_model,
                idempotency_key=request.idempotency_key,
            )
            self._active_score_id = run.score_id
            self._eval_status = "running"
            self._eval_error = None
            self._eval_results = None

            if not created:
                return self._decorate_eval_payload(
                    {
                        "status": "running",
                        "score_id": run.score_id,
                        "message": "Evaluation in progress.",
                    }
                )

            threading.Thread(
                target=self._run_evaluation,
                args=(run.score_id,),
                daemon=True,
                name=f"eval-{self.session_id[:8]}-{run.score_id}",
            ).start()
            return self._decorate_eval_payload(
                {
                    "status": "running",
                    "score_id": run.score_id,
                    "message": "Evaluation started.",
                }
            )

        return self._decorate_eval_payload({"status": "unknown"})

    def _decorate_eval_payload(self, payload: dict) -> dict:
        """Attach read-only follow-up hints to an internal eval response."""
        payload.setdefault("current_phase", self.phase.value)
        payload.setdefault(
            "next_allowed",
            [
                f"GET /ops/session/{self.session_id}/scores",
                f"GET /ops/session/{self.session_id}/results",
            ],
        )
        return payload

    # ------------------------------------------------------------------
    # Domain tool calls
    # ------------------------------------------------------------------

    def call_domain_tool(self, name: str, **kwargs) -> str:
        """Route a domain tool call through the proxy."""
        if self._closed:
            return json.dumps({"success": False, "output": "Error: Session is closed"})
        result = self.proxy.call_tool(name, **kwargs)
        self._persist_active_state()
        return result

    # ------------------------------------------------------------------
    # Tool visibility per phase (for MCP list_tools)
    # ------------------------------------------------------------------

    def get_visible_tools(self) -> list[Tool]:
        """Return MCP Tool list — static union for frozen-registry clients.

        All lifecycle tools are visible from UNREGISTERED onward so a
        frozen-registry MCP client (which caches ``list_tools`` at connect
        time and ignores ``tools/list_changed``) can still drive the full
        state machine. Phase permissions are enforced at call time via
        ``check_permission``; wrong-phase calls return an imperative error
        naming the next hop.

        Domain tools are appended whenever a task is bound to this session
        — either via ``register_session`` (proxy-resolved, includes live
        distractors) or via a run-token binding before register (preloaded
        from the task definition using the same deterministic seed as
        ``populate_proxy_for_task``, so the initial ``list_tools`` already
        contains the full catalogue).
        """
        lifecycle = [
            REGISTER_SESSION_TOOL,
            START_SESSION_TOOL,
            SEND_MESSAGE_TOOL,
            GET_BACKGROUND_TOOL,
        ]
        return lifecycle + self._resolve_domain_tools()

    def _resolve_domain_tools(self) -> list[Tool]:
        """Return task-specific domain tools, preloading when necessary.

        If the proxy exposes live tool schemas (post-register), they are
        authoritative. Otherwise — UNREGISTERED on a run-token connection,
        or a completed session restored from storage where the proxy is a
        logs-only stub — synthesize schemas from task metadata so
        ``list_tools`` still returns the full catalogue.
        """
        if self.proxy is not None and hasattr(self.proxy, "get_available_tools"):
            return self._get_domain_tools()

        task_id = self.task_id or self._run_task_id or ""
        if not task_id:
            return []
        return self._preload_domain_tools_for_task(task_id)

    def _preload_domain_tools_for_task(self, task_id: str) -> list[Tool]:
        """Synthesize domain Tool objects from task metadata without a container.

        Called in UNREGISTERED phase when a run-token has bound the session
        to a task but register_session hasn't run yet. Distractor selection
        mirrors ``populate_proxy_for_task`` (same seed, same pool math) so
        the preloaded catalogue matches the post-register list.
        """
        import random

        from server.core.distractors.distractor_tools import DISTRACTOR_TOOLS
        from server.core.registry import _TOTAL_TOOL_SLOTS
        from server.tooling import (
            get_task_tool_specs,
            render_agent_tool_description,
        )

        task = self.task or self._load_task(task_id)
        if task is None:
            return []

        core_names = _effective_core_tool_names(task)
        convenient_names = list(
            task.ground_truth.convenient_tools if task.ground_truth else []
        )

        seed = _session_random_seed(task_id, self.session_id, task.seed)
        rng = random.Random(seed)
        excluded = set(core_names) | set(convenient_names)
        pool = [d for d in DISTRACTOR_TOOLS if d not in excluded]
        n_slots = max(
            0,
            min(
                _TOTAL_TOOL_SLOTS - len(core_names) - len(convenient_names),
                len(pool),
            ),
        )
        distractor_names = rng.sample(pool, n_slots) if n_slots > 0 else []

        specs = get_task_tool_specs(
            core_tool_names=core_names,
            convenient_tool_names=convenient_names,
            distractor_tool_names=distractor_names,
        )
        return [
            Tool(
                name=name,
                description=render_agent_tool_description(spec),
                inputSchema=spec.input_schema,
            )
            for name, spec in specs.items()
        ]

    # ------------------------------------------------------------------
    # Unified MCP call_tool handler
    # ------------------------------------------------------------------

    async def _notify_tools_changed(self) -> None:
        """Send ``tools/list_changed`` notification to the MCP client.

        Called after phase transitions so the client re-fetches
        ``list_tools`` and sees the updated tool set.
        """
        if self._mcp_server is None:
            return
        try:
            ctx = self._mcp_server.request_context
            await ctx.session.send_tool_list_changed()
        except Exception as exc:
            # Non-fatal — client can still call list_tools manually.
            logger.debug("Failed to send tool_list_changed: %s", exc)

    async def handle_tool_call(
        self,
        name: str,
        arguments: dict,
    ) -> list[TextContent]:
        """Route an MCP tool call with permission checking.

        Called by the per-session MCP Server's ``call_tool`` handler.
        """
        self._last_activity = time.time()

        if self._closed:
            return [
                TextContent(
                    type="text",
                    text=make_error_response("Session is closed.", []),
                )
            ]

        # Permission check
        allowed, error, allowed_ops = check_permission(self.phase, name)
        if not allowed:
            logger.debug(
                "[%s] DENIED %s in phase %s",
                self.session_id[:8],
                name,
                self.phase.value,
            )
            return [
                TextContent(
                    type="text",
                    text=make_error_response(
                        error, allowed_ops, current_phase=self.phase
                    ),
                )
            ]

        logger.debug(
            "[%s] tool_call: %s (phase=%s)", self.session_id[:8], name, self.phase.value
        )

        # Session API routing
        if name == "register_session":
            task_id = arguments.get("task_id", "")
            persona_id = arguments.get("persona_id")
            # Run-bound: task_id from RunAssignment overrides client argument
            if self.run_id and self._run_task_id:
                task_id = self._run_task_id
            if not task_id:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "error": "task_id required when not connected via run token"
                            }
                        ),
                    )
                ]
            result = await _run_state_sync(
                self.use_docker, self.register, task_id, persona_id
            )
            if "session_id" in result:
                await self._notify_tools_changed()
            return [TextContent(type="text", text=json.dumps(result))]

        if name == "start_session":
            result = await _run_state_sync(self.use_docker, self.start)
            await self._notify_tools_changed()
            return [TextContent(type="text", text=json.dumps(result))]

        if name == "get_background":
            from server.core.session import build_background

            bg = build_background(self.task) if self.task else ""
            return [TextContent(type="text", text=json.dumps(bg))]

        if name == "send_message":
            text = arguments.get("text", "")
            attachments = arguments.get("attachments") or []
            reasoning = arguments.get("reasoning")
            if not isinstance(attachments, list):
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"error": "attachments must be an array of file paths"}
                        ),
                    )
                ]
            if len(attachments) > 3:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"error": "Maximum 3 attachments allowed"}),
                    )
                ]
            logger.info(
                "[%s] send_message (turn %d, %d attachments, reasoning=%s): %s...",
                self.session_id[:8],
                self.session.turn if self.session else 0,
                len(attachments),
                "yes" if reasoning else "no",
                text[:100],
            )
            result = await _run_state_sync(
                self.use_docker,
                lambda: self.handle_send_message(
                    text,
                    attachments=attachments,
                    reasoning=reasoning,
                )
            )
            # Log user reply
            try:
                data = json.loads(result)
                logger.info(
                    "[%s] user reply (status=%s): %s...",
                    self.session_id[:8],
                    data.get("status", "?"),
                    data.get("user_message", "")[:100],
                )
            except Exception:
                pass
            if self.phase == SessionPhase.COMPLETED:
                await self._notify_tools_changed()
            return [TextContent(type="text", text=str(result))]

        # Domain tool — route through proxy
        if name in HEAVY_TOOLS:
            async with backtest_sem():
                result = await _run_state_sync(
                    self.use_docker,
                    lambda: self.call_domain_tool(name, **arguments)
                )
        else:
            result = await _run_state_sync(
                self.use_docker,
                lambda: self.call_domain_tool(name, **arguments)
            )
        result_preview = str(result)[:150]
        logger.debug("[%s] %s -> %s...", self.session_id[:8], name, result_preview)
        return [TextContent(type="text", text=str(result))]

    # ------------------------------------------------------------------
    # Result saving
    # ------------------------------------------------------------------

    def _active_run_state_path(self) -> Path | None:
        if not self.run_id:
            return None
        return self.bench_root / "results" / "runs" / self.run_id / "run_state.json"

    def _active_workspace_snapshot_path(self) -> Path | None:
        state_path = self._active_run_state_path()
        if state_path is None:
            return None
        return state_path.parent / "workspace_snapshot"

    def _turn_count(self) -> int:
        return int(getattr(self.session, "turn", 0) or 0) if self.session else 0

    def _copy_active_workspace_snapshot(self) -> Path | None:
        if self.container is None:
            return None
        source = Path(self.container.workspace_path)
        if not source.is_dir():
            return None
        dest = self._active_workspace_snapshot_path()
        if dest is None:
            return None
        tmp = dest.with_name(f".{dest.name}.tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(source, tmp, symlinks=True)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
        return dest

    def _restore_active_workspace_snapshot(self, snapshot_dir: Path) -> None:
        if self.container is None or not snapshot_dir.is_dir():
            return
        dest = Path(self.container.workspace_path)
        dest.mkdir(parents=True, exist_ok=True)
        for child in dest.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in snapshot_dir.iterdir():
            target = dest / child.name
            if child.is_dir() and not child.is_symlink():
                shutil.copytree(child, target, symlinks=True)
            else:
                shutil.copy2(child, target, follow_symlinks=False)

    def _maybe_commit_resume_snapshot(self, *, force: bool = False) -> None:
        if not self.run_id or not self.container_manager or not self.container:
            return
        turn_count = self._turn_count()
        if not force and (turn_count <= 0 or turn_count % self._snapshot_interval != 0):
            return
        if not force and self._latest_layer_tag.endswith(f"-{turn_count}"):
            return
        if not hasattr(self.container_manager, "commit_resume_snapshot"):
            return
        try:
            tag = self.container_manager.commit_resume_snapshot(
                self.container.container_id,
                run_id=self.run_id,
                turn_count=turn_count,
            )
        except Exception as exc:
            logger.warning(
                "Session %s resume snapshot failed at turn %s: %s",
                self.session_id,
                turn_count,
                exc,
            )
            return
        if not tag:
            return
        self._latest_layer_tag = tag
        if tag in self._snapshot_tags:
            self._snapshot_tags.remove(tag)
        self._snapshot_tags.append(tag)
        while len(self._snapshot_tags) > self._snapshot_keep:
            old = self._snapshot_tags.pop(0)
            try:
                self.container_manager.remove_image(old)
            except Exception as exc:
                logger.debug("Could not remove old resume layer %s: %s", old, exc)

    def _cleanup_resume_layers(self) -> None:
        tags = list(
            dict.fromkeys([*self._snapshot_tags, self._latest_layer_tag])
        )
        tags = [tag for tag in tags if tag]
        if not tags:
            self._latest_layer_tag = ""
            return
        manager = self.container_manager
        if manager is None:
            try:
                from server.core.container import ContainerManager

                manager = ContainerManager(use_docker=self.use_docker)
            except Exception:
                manager = None
        if manager is None or not hasattr(manager, "remove_image"):
            return
        for tag in tags:
            try:
                manager.remove_image(tag)
            except Exception as exc:
                logger.debug("Could not remove resume layer %s: %s", tag, exc)
        self._snapshot_tags = []
        self._latest_layer_tag = ""

    def _remove_active_state(self) -> None:
        state_path = self._active_run_state_path()
        if state_path is None:
            return
        try:
            state_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove active run_state %s: %s", state_path, exc)
        snapshot_path = self._active_workspace_snapshot_path()
        if snapshot_path is not None:
            shutil.rmtree(snapshot_path, ignore_errors=True)

    def _active_run_payload(self) -> dict:
        conversation = self.session.conversation if self.session else []
        tool_logs = (
            [_safe_tool_log_dict(log) for log in self.proxy.get_logs()]
            if self.proxy
            else []
        )
        distractor_names = (
            self.proxy.get_distractor_names() if self.proxy is not None else []
        )
        try:
            from eval.tool_filters import NON_SUBSTANTIVE_TOOLS

            step_count = sum(
                1 for log in tool_logs if log.get("name") not in NON_SUBSTANTIVE_TOOLS
            )
        except Exception:
            step_count = len(tool_logs)
        duration = time.time() - self._start_time if self._start_time else 0.0
        session_status = (
            self.session.session_status if self.session else self.phase.value
        )
        termination_reason = self.session.completion_reason if self.session else None
        return {
            "active_state_version": 1,
            "run_id": self.run_id or "",
            "public_task_label": self.task_id or "",
            "owner_user_id": self.owner_user_id,
            "owner_github_login": self.owner_github_login,
            "owner_email": self.owner_email,
            "visibility": self.visibility,
            "task_id": self.task_id,
            "session_id": self._storage_session_id(),
            "persona_id": self.persona_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "phase": self.phase.value,
            "session_status": session_status,
            "termination_reason": termination_reason,
            "turn_count": self._turn_count(),
            "conversation": conversation,
            "tool_logs": tool_logs,
            "distractor_names": distractor_names,
            "file_ledger": self.session.file_ledger if self.session else {},
            "artifact_debug_history": (
                self.session.artifact_debug_history if self.session else []
            ),
            "simulator_cost": self.user_sim.total_cost if self.user_sim else 0.0,
            "duration_seconds": duration,
            "step_count": step_count,
            "sandbox_digest": _environment_sandbox_digest(
                self.task.environment if self.task else None
            ),
            "latest_layer_tag": self._latest_layer_tag,
            "snapshot_tags": list(self._snapshot_tags),
            "snapshot_interval": self._snapshot_interval,
            "snapshot_keep": self._snapshot_keep,
            "workspace_snapshot_path": (
                str(path)
                if (path := self._active_workspace_snapshot_path()) and path.exists()
                else ""
            ),
            "plugin_bundle": getattr(self.plugin_bundle, "name", ""),
        }

    def _persist_active_state(self, *, force_snapshot: bool = False) -> Path | None:
        if self._suppress_active_persist:
            return None
        state_path = self._active_run_state_path()
        if state_path is None:
            return None
        if force_snapshot:
            self._maybe_commit_resume_snapshot(force=True)
            self._copy_active_workspace_snapshot()
        else:
            self._maybe_commit_resume_snapshot(force=False)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_name(f".{state_path.name}.tmp")
        tmp_path.write_text(
            json.dumps(self._active_run_payload(), indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp_path, state_path)
        return state_path

    def suspend_for_resume(self) -> None:
        """Persist an active checkpoint, snapshot the container, and release it."""
        self._persist_active_state(force_snapshot=True)
        self._destroy_container()
        self._closed = True

    def _storage_session_id(self) -> str:
        """Session ID for result storage.

        New server storage uses the raw 32-char session id for all runs.
        """
        return self.session_id

    def _save_results(self):
        """Save run_state.json after session completion.

        Storage path: results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:12]}/
        """
        from datetime import datetime

        from server.storage.result_writer import save_run_state

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_name = f"{ts}_{self.session_id[:12]}"
        result_dir = (
            self.bench_root
            / "results"
            / "server"
            / self.task_id
            / self.persona_id
            / dir_name
        )
        self._result_dir = result_dir

        conversation = self.session.conversation
        tool_logs = self.proxy.get_logs()
        distractor_names = self.proxy.get_distractor_names()
        duration = time.time() - self._start_time if self._start_time else 0.0

        save_run_state(
            result_dir=result_dir,
            conversation=conversation,
            tool_logs=tool_logs,
            workspace_path=(self.container.workspace_path if self.container else None),
            simulator_cost=(self.user_sim.total_cost if self.user_sim else 0.0),
            duration_seconds=duration,
            distractor_names=distractor_names,
            task_id=self.task_id,
            session_id=self._storage_session_id(),
            persona_id=self.persona_id,
            session_status=self.session.session_status if self.session else "",
            termination_reason=(
                self.session.completion_reason if self.session else None
            ),
            artifact_debug_history=(
                self.session.artifact_debug_history if self.session else None
            ),
            run_id=self.run_id or "",
            public_task_label=self.task_id or "",
            owner_user_id=self.owner_user_id,
            owner_github_login=self.owner_github_login,
            owner_email=self.owner_email,
            visibility=self.visibility,
            sandbox_digest=_environment_sandbox_digest(
                self.task.environment if self.task else None
            ),
        )
        logger.info("Results saved: %s", result_dir)

    # ------------------------------------------------------------------
    # Evaluation (background thread)
    # ------------------------------------------------------------------

    def _run_evaluation(self, score_id: str):
        """Run evaluation pipeline in a background thread.

        Writes to ``_eval_status`` / ``_eval_results`` under ``_eval_lock``
        to avoid races with ``request_evaluation`` reads.
        """
        try:
            if not self._result_dir:
                raise RuntimeError("No result_dir — session results not saved")

            eval_item = self.eval_item
            if eval_item is None:
                eval_item = self.plugin_bundle.task_suite.get_task(self.task_id)
                self.eval_item = eval_item
            sample = self._build_eval_sample(score_id)
            score = self.plugin_bundle.evaluator.evaluate(eval_item, sample)
            summary = (
                score.metrics.get("summary")
                if isinstance(score.metrics, dict)
                else None
            )
            eval_results = (
                dict(summary)
                if isinstance(summary, dict)
                else {
                    "score_status": score.status,
                    "overall_score": score.value,
                    "reason": score.reason,
                }
            )

            with self._eval_lock:
                self._eval_results = eval_results
                score_status = str(eval_results.get("score_status") or "")
                if score_status == "failed":
                    self._eval_status = "failed"
                    self._eval_error = eval_results.get("error")
                elif score_status == "interrupted":
                    self._eval_status = "failed"
                    self._eval_error = (
                        eval_results.get("error") or "Evaluation interrupted"
                    )
                else:
                    self._eval_status = "completed"
                self._active_score_id = score_id
            logger.info("Evaluation completed for session %s", self.session_id)

        except Exception as e:
            logger.error(
                "Evaluation failed for session %s: %s",
                self.session_id,
                e,
                exc_info=True,
            )
            with self._eval_lock:
                self._eval_error = str(e)
                self._eval_status = "failed"
            if self._result_dir:
                try:
                    from eval.storage.score_store import update_score_run

                    update_score_run(
                        self._result_dir,
                        score_id,
                        status="failed",
                        error=str(e),
                    )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Query methods (shared by server internals and REST read endpoints)
    # ------------------------------------------------------------------

    def get_run_results(self) -> dict:
        """Return run_state.json content as dict."""
        if not self._result_dir:
            return {"error": "Results not available"}
        state_path = self._result_dir / "run_state.json"
        if not state_path.exists():
            return {"error": "Results not available"}
        return json.loads(state_path.read_text(encoding="utf-8"))

    def get_eval_scores(
        self,
        history: bool = False,
        score_id: str | None = None,
        score_ids: list[str] | None = None,
        status_filter: list[str] | None = None,
    ) -> dict:
        """Return evaluation scores.

        Reads the in-bundle score store written under
        ``evaluations/score_n``.
        """
        if self._result_dir:
            from eval.storage.score_store import get_scores_payload

            return get_scores_payload(
                self._result_dir,
                history=history,
                score_id=score_id,
                score_ids=score_ids,
                status_filter=status_filter,
            )
        return {"status": "pending"}

    def _read_eval_history(self) -> dict:
        """Read all score_n entries from evaluations/index.json."""
        if not self._result_dir:
            return {"session_id": self.session_id, "scores": [], "evaluations": []}
        from eval.storage.score_store import get_scores_payload

        payload = get_scores_payload(self._result_dir, history=True)
        payload["session_id"] = self.session_id
        return payload

    # ------------------------------------------------------------------
    # Container cleanup
    # ------------------------------------------------------------------

    def _destroy_container(self):
        """Destroy container and clean temp dirs.

        Called after results are saved (agent_files copied to result_dir).
        """
        if self.container_manager and self.container:
            try:
                self.container_manager.destroy_container(
                    self.container.container_id,
                )
                logger.info(
                    "Container destroyed: %s",
                    self.container.container_id,
                )
            except Exception as exc:
                logger.warning("Container cleanup failed: %s", exc)
            self.container = None

        from server.core.staging import cleanup_staged_dirs

        cleanup_staged_dirs(self.staged_temp_dirs)
        self.staged_temp_dirs = []

    def cleanup(self, *, persist_partial: bool = False):
        """Full cleanup — called on cancellation, idle cleanup, or shutdown.

        Partial-result persistence is opt-in so user-triggered cancellation
        does not silently write incomplete sessions to the results store.

        When the session is still active (not completed) and has conversation
        content, the agent abandoned the session prematurely — we record
        this as ``agent_abandoned`` so it can be penalized during evaluation.
        """
        self._closed = True
        if (
            persist_partial
            and self.session
            and self.session.conversation
            and self._result_dir is None
            and self.phase in (SessionPhase.IN_SESSION, SessionPhase.REGISTERED)
        ):
            try:
                # Mark session as agent-abandoned (session never reached
                # "completed" but the agent disconnected).
                self.session.force_complete("agent_abandoned", append_closing=False)
                logger.info(
                    "Session %s: agent abandoned — saving partial results",
                    self.session_id,
                )
                self._save_results()
                self._cleanup_resume_layers()
                self._remove_active_state()
            except Exception as exc:
                logger.warning(
                    "Session %s: save before cleanup failed: %s",
                    self.session_id,
                    exc,
                )
        self._destroy_container()

    def _reset_registration_state(self):
        """Rollback partial register() state after a failed initialization."""
        try:
            self._destroy_container()
        except Exception:
            logger.warning(
                "Session %s rollback cleanup failed",
                self.session_id,
                exc_info=True,
            )
        self.task = None
        self.eval_item = None
        self.persona = None
        self.task_id = ""
        self.persona_id = ""
        self._task_core_tool_names = ()
        self._task_convenient_tool_names = ()
        self.proxy = None
        self.session = None
        self.container_manager = None
        self.container = None
        self.user_sim = None
        self._start_time = None
        self._result_dir = None
        self.phase = SessionPhase.UNREGISTERED

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_task(self, task_id: str):
        """Load task JSON by ID."""
        try:
            return self._task_from_eval_item(
                self.plugin_bundle.task_suite.get_task(task_id)
            )
        except Exception:
            return None

    def _task_from_eval_item(self, eval_item: EvalItem):
        """Extract the reference business task from an EvalItem envelope."""
        get_business_task = getattr(
            self.plugin_bundle.task_suite,
            "get_business_task",
            None,
        )
        if callable(get_business_task):
            return get_business_task(eval_item.task_id)

        from eval.contracts.schemas import QuantTutorTask

        raw = eval_item.payload.get("quant_tutor_task")
        if hasattr(raw, "model_dump"):
            return raw
        if isinstance(raw, dict):
            return QuantTutorTask(**raw)
        return None

    def _build_eval_sample(self, score_id: str) -> EvalSample:
        """Build a platform EvalSample from the completed session state."""
        conversation = self.session.conversation if self.session else []
        transcript = tuple(
            TranscriptMessage(
                role=str(entry.get("role") or ""),
                content=str(entry.get("content") or ""),
                ts=(
                    entry.get("ts")
                    if isinstance(entry.get("ts"), (int, float))
                    else None
                ),
                metadata={
                    key: value
                    for key, value in entry.items()
                    if key not in {"role", "content", "ts"}
                },
            )
            for entry in conversation
        )
        raw_tool_logs = self.proxy.get_logs() if self.proxy else []
        tool_logs = tuple(
            log
            for raw in raw_tool_logs
            if (log := self._contract_tool_log(raw)) is not None
        )
        workspace_path = (
            self.container.workspace_path
            if self.container
            else str(self._result_dir / "agent_files") if self._result_dir else ""
        )
        payload = {
            "task": self.task,
            "persona": self.persona,
            "result_dir": str(self._result_dir) if self._result_dir else "",
            "workspace_path": workspace_path,
            "eval_model": self.eval_model,
            "eval_mode": self._eval_mode,
            "score_id": score_id,
            "session_id": self.session_id,
            "distractor_names": (
                self.proxy.get_distractor_names() if self.proxy is not None else []
            ),
        }
        return EvalSample(
            sample_id=score_id,
            task_id=self.task_id,
            transcript=transcript,
            tool_logs=tool_logs,
            files={},
            payload=payload,
        )

    @staticmethod
    def _contract_tool_log(raw) -> ToolLog | None:
        data = _safe_tool_log_dict(raw)
        name = str(data.get("name") or data.get("tool_name") or "")
        if not name:
            return None
        args = data.get("args") or {}
        if not isinstance(args, dict):
            args = {"value": args}
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return ToolLog(
            name=name,
            args=args,
            result=str(data.get("result") or ""),
            success=bool(data.get("success", True)),
            duration_ms=float(data.get("duration_ms") or 0.0),
            turn_index=int(data.get("turn_index") or 0),
            metadata=metadata,
        )

    def _load_persona(self, persona_id: str):
        """Load persona JSON by ID."""
        from eval.contracts.schemas import UserPersona

        personas_dir = self.bench_root / "personas"
        for json_path in personas_dir.rglob(f"{persona_id}.json"):
            return UserPersona(**json.loads(json_path.read_text()))
        return None

    def _get_domain_tools(self) -> list[Tool]:
        """Convert proxy tool schemas to MCP Tool objects.

        Excludes session API tools (send_message is on the proxy for logging
        but listed separately by get_visible_tools).
        """
        from server.tooling import (
            get_task_tool_specs,
            normalize_input_schema,
            render_agent_tool_description,
        )

        if not self.proxy:
            return []

        tool_specs = get_task_tool_specs(
            core_tool_names=self._task_core_tool_names,
            convenient_tool_names=self._task_convenient_tool_names,
            distractor_tool_names=self.proxy.get_distractor_names(),
        )
        tools = []
        for schema in self.proxy.get_available_tools():
            name = schema["name"]
            if name in SESSION_API_TOOLS:
                continue
            spec = tool_specs.get(name)
            description = (
                render_agent_tool_description(spec)
                if spec is not None
                else schema.get("description", "")
            )
            input_schema = (
                spec.input_schema
                if spec is not None
                else normalize_input_schema(schema.get("parameters", {}))
            )
            tools.append(
                Tool(
                    name=name,
                    description=description,
                    inputSchema=input_schema,
                )
            )
        return tools
