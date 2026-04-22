"""Session lifecycle manager for QuantTutorBench HTTP Server.

Each MCP HTTP session maps to one ``SessionState`` instance that manages:
- Task + persona loading (random persona selection)
- Container + tool setup
- TutoringSession lifecycle (student simulation, termination checking)
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
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from mcp.types import TextContent, Tool

from server.config.llm_config import EVAL_DEFAULT_MODEL

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


def _resolve_persona_pin(task_id: str, persona_ids: list[str]) -> Optional[str]:
    """Return an internal-only pinned persona override, if configured."""
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
                    desired = str(desired)
                    if desired in persona_ids:
                        return desired
                    logger.warning(
                        "Ignoring persona pin '%s' for task %s; valid options: %s",
                        desired,
                        task_id,
                        persona_ids,
                    )

    desired = os.environ.get("QTB_TEST_PERSONA_PIN", "").strip()
    if not desired:
        return None
    if desired in persona_ids:
        return desired
    logger.warning(
        "Ignoring persona pin '%s' for task %s; valid options: %s",
        desired,
        task_id,
        persona_ids,
    )
    return None


class SessionState:
    """Per-session state for one benchmark session.

    Lifecycle:
        1. Created by BenchSessionManager when a new MCP connection arrives.
        2. ``register(task_id)`` — loads task, picks persona, creates container.
        3. ``start()`` — returns student opening, enters IN_SESSION.
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
    ):
        self.session_id = session_id
        self.phase = SessionPhase.UNREGISTERED
        self.use_docker = use_docker
        self.bench_root = bench_root or Path(__file__).parent.parent.parent
        self.eval_model = eval_model

        # Task state (set during register)
        self.task = None
        self.persona = None
        self.task_id: str = ""
        self.persona_id: str = ""
        self._task_core_tool_names: tuple[str, ...] = ()
        self._task_convenient_tool_names: tuple[str, ...] = ()

        # Runtime components (set during register)
        self.proxy = None
        self.session = None  # TutoringSession
        self.container_manager = None
        self.container = None
        self.student_sim = None
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
        self._tutor_dims: Optional[list[str]] = None
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
    ) -> "SessionState":
        """Restore a COMPLETED session from server storage (run_state.json).

        Server persists session data to disk.  When the session is no longer
        in memory (cleaned by sweeper or server restart), this method
        reconstructs enough state to service any API call: evaluate, get
        results, get scores.

        No container, proxy, or student simulator is needed — only the
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

        # Check if evaluation was already run under the score_n store.
        try:
            from server.storage.score_store import get_scores_payload

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
            "Restored session %s from storage: %s/%s (eval=%s)",
            session_id[:8],
            task_id,
            persona_id,
            state._eval_status,
        )
        return state

    def _build_session_context(
        self,
        *,
        docs_available: list[str],
        student_code_dir: Optional[Path | str],
    ) -> dict:
        """Build the truthful runtime context exposed to tools."""
        environment = self.task.environment if self.task else None
        max_backtest_trials = environment.max_backtest_trials if environment else 0
        return {
            "category": self.task.category.value if self.task else "",
            "requires_code": bool(self.task.requires_code) if self.task else False,
            "docs_available": list(docs_available or []),
            "max_backtest_trials": max_backtest_trials,
            "sandbox_image": environment.sandbox_image if environment else "",
            "student_code_available": bool(student_code_dir),
        }

    def _build_tool_env(
        self,
        *,
        data_dir: str | Path,
        docs_dir: str | Path,
        workspace_dir: str | Path,
        student_code_dir: Optional[str | Path],
        docs_available: list[str],
    ) -> dict[str, str]:
        """Build per-session tool environment variables."""
        context = self._build_session_context(
            docs_available=docs_available,
            student_code_dir=student_code_dir,
        )
        env = {
            "QTB_DATA_DIR": str(data_dir),
            "QTB_DOCS_DIR": str(docs_dir),
            "QTB_WORKSPACE_DIR": str(workspace_dir),
            "QTB_MAX_BACKTEST_TRIALS": str(context["max_backtest_trials"]),
            "LEAN_RUN_TIMEOUT": "300",
            "QTB_SESSION_CONTEXT_JSON": json.dumps(context),
        }
        if student_code_dir:
            env["QTB_STUDENT_CODE_DIR"] = str(student_code_dir)
        return env

    # ------------------------------------------------------------------
    # register_session
    # ------------------------------------------------------------------

    def register(self, task_id: str, persona_id: Optional[str] = None) -> dict:
        """Handle ``register_session(task_id, persona_id?)``.

        Heavy operation: loads task, selects persona, creates container,
        registers tools, creates TutoringSession. If ``persona_id`` is
        provided, it overrides the default random persona selection.

        """
        from server.config.benchmark_config import DATASET_REVISION
        from server.config.bootstrap import load_server_env
        from server.config.llm_config import SIMULATOR_DEFAULT_MODEL
        from server.config.prompt_config import build_scenario, build_user_description
        from server.core.container import ContainerManager
        from server.core.proxy import MCPProxy
        from server.core.registry import populate_proxy_for_task
        from server.core.session import TutoringSession
        from server.core.staging import create_staged_dirs, create_staged_sample_code
        from server.core.student_sim import StudentSimulator
        from server.core.tc_checker import TCChecker, parse_tc_items
        from server.data_manager import ensure_data
        from server.eval.judges.runtime.model_resolver import (
            require_student_model,
        )

        self._closed = False

        try:
            task = self._load_task(task_id)
            if task is None:
                return {"error": f"Task not found: {task_id}"}

            self.task = task
            self.task_id = task_id
            self._task_core_tool_names = tuple(
                task.environment.core_mcp_tools if task.environment else ()
            )
            self._task_convenient_tool_names = tuple(
                task.ground_truth.convenient_tools if task.ground_truth else ()
            )

            if not task.persona_ids:
                return {
                    "accepted": False,
                    "error": f"Task {task_id} has no persona_ids",
                }

            session_seed = _session_random_seed(task_id, self.session_id, task.seed)
            selected_persona_id = persona_id.strip() if persona_id else None
            if selected_persona_id:
                if selected_persona_id not in task.persona_ids:
                    return {
                        "accepted": False,
                        "error": (
                            f"Unsupported persona_id '{selected_persona_id}' for task {task_id}. "
                            f"Valid persona_ids: {task.persona_ids}"
                        ),
                    }
                logger.info(
                    "Session %s using explicitly requested persona=%s for task=%s",
                    self.session_id,
                    selected_persona_id,
                    task_id,
                )
            else:
                selected_persona_id = _resolve_persona_pin(task_id, task.persona_ids)
            if selected_persona_id is None:
                selected_persona_id = random.Random(session_seed).choice(
                    task.persona_ids
                )
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
            try:
                resolved_sim_model = require_student_model(
                    SIMULATOR_DEFAULT_MODEL,
                )
            except RuntimeError as exc:
                return {"error": str(exc)}

            sandbox_img = task.environment.sandbox_image if task.environment else ""
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

            student_code_dir = None
            if task.sample_code:
                student_code_dir, sample_temp_dirs = create_staged_sample_code(
                    task.sample_code,
                    data_search_dirs=paths.data_search_dirs,
                    student_code_dir=paths.student_code,
                )
                self.staged_temp_dirs.extend(sample_temp_dirs)

            self.container = self.container_manager.create_container(
                task_id=f"{task_id}_{self.session_id[:8]}",
                data_dir=staged_data_dir,
                docs_dir=staged_docs_dir,
                student_code_dir=student_code_dir,
                sandbox_image=(
                    task.environment.sandbox_image if task.environment else None
                ),
                network_enabled=(
                    task.environment.network_enabled if task.environment else False
                ),
                lean_data_dir=paths.lean_data,
                custom_data_dir=paths.custom_data,
            )

            local_tool_env = self._build_tool_env(
                data_dir=staged_data_dir,
                docs_dir=staged_docs_dir,
                workspace_dir=self.container.workspace_path,
                student_code_dir=student_code_dir,
                docs_available=docs_available,
            )
            container_tool_env = self._build_tool_env(
                data_dir="/data",
                docs_dir="/docs",
                workspace_dir="/workspace",
                student_code_dir="/student_code" if student_code_dir else None,
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
                core_tool_names=(
                    task.environment.core_mcp_tools if task.environment else []
                ),
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

            tc_text = (
                task.ground_truth.termination_criteria if task.ground_truth else None
            )
            tc_items = parse_tc_items(
                tc_text,
                task.category.value,
                persona_id=self.persona_id,
            )
            has_tc = tc_items is not None

            self.student_sim = StudentSimulator(
                scenario=build_scenario(
                    task, self.persona_id, has_incremental_tc=has_tc
                ),
                user_description=build_user_description(
                    self.persona,
                    has_incremental_tc=has_tc,
                ),
                model=resolved_sim_model,
            )

            tc_checker = TCChecker(tc_items) if tc_items else None

            effective_timeout = task.timeout_minutes
            deadline = None
            if effective_timeout and effective_timeout > 0:
                deadline = time.time() + effective_timeout * 60
            self.proxy.set_deadline(deadline)

            self.session = TutoringSession(
                task=task,
                persona=persona,
                student_sim=self.student_sim,
                tc_checker=tc_checker,
                max_turns=task.max_turns,
                deadline=deadline,
                proxy=self.proxy,
                workspace_path=self.container.workspace_path,
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
        """Handle ``start_session()`` — return student opening + available tools.

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
        delivered to the student.

        Returns:
            Raw JSON string from TutoringSession (via proxy).
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
        return json.dumps(data)

    # ------------------------------------------------------------------
    # Internal server-side evaluation trigger
    # ------------------------------------------------------------------

    def request_evaluation(self) -> dict:
        """Start an internal evaluation run for a completed result bundle.

        Each non-concurrent call appends a new score_n evaluation run.
        Concurrent calls return the active running score instead of starting
        a second run.
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

            from server.eval.contracts.request import parse_eval_request
            from server.storage.score_store import allocate_score_run

            request = parse_eval_request(
                {
                    "session_id": self.session_id,
                    "eval_mode": self._eval_mode,
                    "tutor_dims": self._tutor_dims,
                    "eval_model": self.eval_model,
                    "idempotency_key": self._eval_idempotency_key,
                }
            )
            self._eval_mode = request.eval_mode
            self._tutor_dims = request.tutor_dims
            self.eval_model = request.eval_model or self.eval_model
            run, created = allocate_score_run(
                self._result_dir,
                eval_mode=request.eval_mode,
                eval_model=request.eval_model,
                tutor_dims=request.tutor_dims,
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
        payload.setdefault("next_allowed", ["get_scores", "get_results"])
        return payload

    # ------------------------------------------------------------------
    # Domain tool calls
    # ------------------------------------------------------------------

    def call_domain_tool(self, name: str, **kwargs) -> str:
        """Route a domain tool call through the proxy."""
        if self._closed:
            return json.dumps({"success": False, "output": "Error: Session is closed"})
        return self.proxy.call_tool(name, **kwargs)

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

        core_names = list(task.environment.core_mcp_tools if task.environment else [])
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
            result = await asyncio.to_thread(self.register, task_id, persona_id)
            if "session_id" in result:
                await self._notify_tools_changed()
            return [TextContent(type="text", text=json.dumps(result))]

        if name == "start_session":
            result = await asyncio.to_thread(self.start)
            await self._notify_tools_changed()
            return [TextContent(type="text", text=json.dumps(result))]

        if name == "get_background":
            from server.core.session import build_background

            bg = build_background(self.task) if self.task else ""
            return [TextContent(type="text", text=bg)]

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
            result = await asyncio.to_thread(
                self.handle_send_message,
                text,
                attachments=attachments,
                reasoning=reasoning,
            )
            # Log student reply
            try:
                data = json.loads(result)
                logger.info(
                    "[%s] student reply (status=%s): %s...",
                    self.session_id[:8],
                    data.get("status", "?"),
                    data.get("student_message", "")[:100],
                )
            except Exception:
                pass
            if self.phase == SessionPhase.COMPLETED:
                await self._notify_tools_changed()
            return [TextContent(type="text", text=str(result))]

        # Domain tool — route through proxy
        if name in HEAVY_TOOLS:
            async with backtest_sem():
                result = await asyncio.to_thread(
                    self.call_domain_tool, name, **arguments
                )
        else:
            result = await asyncio.to_thread(self.call_domain_tool, name, **arguments)
        result_preview = str(result)[:150]
        logger.debug("[%s] %s -> %s...", self.session_id[:8], name, result_preview)
        return [TextContent(type="text", text=str(result))]

    # ------------------------------------------------------------------
    # Result saving
    # ------------------------------------------------------------------

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

        # TC checker cost (if incremental TC was used)
        tc_cost = 0.0
        if self.session and self.session._tc_checker:
            tc_cost = getattr(self.session._tc_checker, "total_cost", 0.0)

        save_run_state(
            result_dir=result_dir,
            conversation=conversation,
            tool_logs=tool_logs,
            workspace_path=(self.container.workspace_path if self.container else None),
            simulator_cost=(self.student_sim.total_cost if self.student_sim else 0.0),
            tc_checker_cost=tc_cost,
            duration_seconds=duration,
            distractor_names=distractor_names,
            task_id=self.task_id,
            session_id=self._storage_session_id(),
            persona_id=self.persona_id,
            session_status=self.session.session_status if self.session else "",
            termination_reason=(
                self.session.completion_reason if self.session else None
            ),
            tc_coverage=(self.session.tc_coverage_summary if self.session else None),
            tc_debug_history=(self.session.tc_debug_history if self.session else None),
            artifact_debug_history=(
                self.session.artifact_debug_history if self.session else None
            ),
            run_id=self.run_id or "",
            public_task_label=(self.task_id.split("_")[0] if self.task_id else ""),
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
            from server.storage.eval_writer import run_evaluation

            if not self._result_dir:
                raise RuntimeError("No result_dir — session results not saved")

            conversation = self.session.conversation
            tool_logs = self.proxy.get_logs()
            distractor_names = self.proxy.get_distractor_names()

            eval_results = run_evaluation(
                task=self.task,
                persona=self.persona,
                result_dir=self._result_dir,
                conversation=conversation,
                tool_logs=tool_logs,
                distractor_names=distractor_names,
                bench_root=str(self.bench_root),
                eval_model=self.eval_model,
                eval_mode=self._eval_mode,
                tutor_dims=self._tutor_dims,
                score_id=score_id,
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
                    from server.storage.score_store import update_score_run

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

        Args:
            history: If True, return all evaluation runs from evaluations/.
        """
        if self._result_dir:
            from server.storage.score_store import get_scores_payload

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
        from server.storage.score_store import get_scores_payload

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
        self.persona = None
        self.task_id = ""
        self.persona_id = ""
        self._task_core_tool_names = ()
        self._task_convenient_tool_names = ()
        self.proxy = None
        self.session = None
        self.container_manager = None
        self.container = None
        self.student_sim = None
        self._start_time = None
        self._result_dir = None
        self.phase = SessionPhase.UNREGISTERED

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_task(self, task_id: str):
        """Load task JSON by ID."""
        from server.schemas import QuantTutorTask

        tasks_dir = self.bench_root / "tasks"
        for json_path in tasks_dir.rglob(f"{task_id}.json"):
            return QuantTutorTask(**json.loads(json_path.read_text()))
        return None

    def _load_persona(self, persona_id: str):
        """Load persona JSON by ID."""
        from server.schemas import StudentPersona

        personas_dir = self.bench_root / "personas"
        for json_path in personas_dir.rglob(f"{persona_id}.json"):
            return StudentPersona(**json.loads(json_path.read_text()))
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
