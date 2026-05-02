"""Single-job execution unit for QuantTutorBench.

Each job = one (task, persona, trial) tuple. Jobs are designed to be
fully isolated (fresh agent, fresh orchestrator) so they can run safely
in a ThreadPoolExecutor.
"""

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from orchestrator.schemas import QuantTutorTask, StudentPersona, TaskResult


@dataclass
class JobSpec:
    """One benchmark job = (task, persona, trial)."""

    task: QuantTutorTask
    persona: StudentPersona
    agent_type: str  # "generic", "openai", "anthropic", "google"
    condition_name: str  # "agent", "baseline", ...
    use_docker: bool
    save_result: bool
    result_base_dir: Path  # e.g. bench/results/run-single/openai/gpt-5.2/
    max_turns: Optional[int] = None
    eval_model: Optional[str] = None
    simulator_model: Optional[str] = None
    model_override: Optional[str] = None
    trial_index: int = 0
    skip_eval: bool = False
    timeout_minutes: Optional[int] = None
    eval_mode: str = "full"  # "full" | "qr" | "qp"


@dataclass
class JobResult:
    """Result of a single benchmark job."""

    job: JobSpec
    task_result: Optional[TaskResult] = None
    trace_captured: dict = field(default_factory=dict)
    error: Optional[str] = None
    duration_seconds: float = 0.0


def run_single_job(job: JobSpec, create_agent_fn=None, cancel_event=None) -> JobResult:
    """Execute one benchmark job in isolation.

    Creates a fresh agent, orchestrator, and runs the full lifecycle.
    Thread-safe: no shared mutable state.

    Args:
        job: The job specification.
        create_agent_fn: Callable(JobSpec) -> BaseAgentAdapter. If None,
            uses the default _create_agent_from_spec.
    """
    start = time.time()
    trace_captured: dict = {}

    try:
        from config.conditions import CONDITIONS

        from orchestrator.orchestrator import BenchmarkOrchestrator

        # 1. Create fresh agent (per-job isolation)
        if create_agent_fn is not None:
            agent = create_agent_fn(job)
        else:
            agent = _create_agent_from_spec(job)

        # 2. Create orchestrator (shares nothing across jobs)
        orchestrator = BenchmarkOrchestrator(
            bench_root=str(Path(__file__).parent.parent.parent),
            use_docker=job.use_docker,
            eval_model=job.eval_model,
            simulator_model=job.simulator_model,
        )

        condition = CONDITIONS[job.condition_name]

        # 3. Prepare trace capture hook if --save-result requested
        result_dir = None
        if job.save_result:
            result_dir = (
                job.result_base_dir
                / job.task.category.value
                / job.task.task_id
                / job.persona.persona_id
            )
            result_dir.mkdir(parents=True, exist_ok=True)
            agent_files_dir = result_dir / "agent_files"

            def _capture(*, result, proxy, workspace_path):
                trace_captured["proxy_logs"] = list(proxy.get_logs())
                trace_captured["distractor_names"] = proxy.get_distractor_names()
                # Capture thinking/COT trace from adapters that support it
                if hasattr(agent, "get_thinking_trace"):
                    trace_captured["thinking_trace"] = agent.get_thinking_trace()
                # Capture per-turn content blocks (thinking/tool_use/tool_result/text)
                if hasattr(agent, "get_content_blocks"):
                    trace_captured["content_blocks"] = agent.get_content_blocks()
                if workspace_path and os.path.isdir(workspace_path):
                    if agent_files_dir.exists():
                        shutil.rmtree(agent_files_dir)
                    shutil.copytree(workspace_path, str(agent_files_dir))

        # 4. Run the job
        task_result = orchestrator.run_single_task(
            task=job.task,
            persona=job.persona,
            agent=agent,
            run_index=job.trial_index,
            max_turns=job.max_turns,
            tools_enabled=condition.tools_enabled,
            pre_teardown_hook=_capture if job.save_result else None,
            skip_eval=job.skip_eval,
            prompt_mode=condition.prompt_mode,
            timeout_minutes=job.timeout_minutes,
            cancel_event=cancel_event,
            eval_mode=job.eval_mode,
        )

        # 5. Check if cancelled
        cancelled = cancel_event and cancel_event.is_set()

        # 6. Save reports (skip if cancelled)
        if job.save_result and result_dir is not None:
            if cancelled:
                # Clean up the pre-created result directory and empty parents
                if result_dir.exists():
                    shutil.rmtree(result_dir)
                # Remove empty parent dirs up to result_base_dir
                for parent in result_dir.parents:
                    if (
                        parent == job.result_base_dir
                        or parent == job.result_base_dir.parent
                    ):
                        break
                    try:
                        parent.rmdir()  # only removes if empty
                    except OSError:
                        break
            else:
                _save_job_reports(result_dir, task_result, trace_captured, agent, job)

        return JobResult(
            job=job,
            task_result=task_result,
            trace_captured=trace_captured,
            error="cancelled" if cancelled else None,
            duration_seconds=time.time() - start,
        )
    except Exception as e:
        return JobResult(
            job=job,
            task_result=None,
            trace_captured=trace_captured,
            error=str(e),
            duration_seconds=time.time() - start,
        )


def _create_agent_from_spec(job: JobSpec):
    """Create the appropriate agent adapter from a JobSpec.

    Mirrors the logic of _create_agent() in run_benchmark.py but uses
    JobSpec fields instead of argparse.
    """
    from config.conditions import CONDITIONS
    from config.llm_config import OPENROUTER_BASE_URL
    from config.model_resolver import get_model_for_agent
    from config.prompt_config import (
        BASELINE_SYSTEM_PROMPT,
        ORACLE_SYSTEM_PROMPT,
        TUTOR_SYSTEM_PROMPT,
    )

    condition = CONDITIONS[job.condition_name]

    _PROMPT_MAP = {
        "tutor": TUTOR_SYSTEM_PROMPT,
        "baseline": BASELINE_SYSTEM_PROMPT,
        "oracle": ORACLE_SYSTEM_PROMPT,
    }
    system_prompt = _PROMPT_MAP.get(condition.prompt_mode, TUTOR_SYSTEM_PROMPT)

    if job.model_override:
        model_short = job.model_override.split("/")[-1]
        agent_name = f"{job.agent_type}_{condition.name}_{model_short}"
    else:
        agent_name = f"{job.agent_type}_{condition.name}"

    # Pure LLM conditions: no SDK framework needed
    if not condition.tools_enabled:
        from orchestrator.agent_adapters.generic_adapter import GenericLLMAdapter

        model = job.model_override or get_model_for_agent(
            job.agent_type, use_openrouter=True
        )
        return GenericLLMAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )

    # Tools-enabled conditions: use the native SDK adapter
    if job.agent_type == "anthropic":
        from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

        model = job.model_override or get_model_for_agent("anthropic")
        return ClaudeAgentAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )
    elif job.agent_type == "google":
        from orchestrator.agent_adapters.google_adapter import GoogleAdapter

        model = job.model_override or get_model_for_agent("google")
        return GoogleAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )
    elif job.agent_type == "openai":
        from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter

        model = job.model_override or get_model_for_agent("openai", use_openrouter=True)
        return OpenAIAgentAdapter(
            model=model,
            base_url=OPENROUTER_BASE_URL,
            system_prompt=system_prompt,
            agent_name=agent_name,
        )
    else:  # generic
        from orchestrator.agent_adapters.generic_adapter import GenericLLMAdapter

        model = job.model_override or get_model_for_agent("generic")
        return GenericLLMAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )


def _save_run_state(
    result_dir: Path,
    result: TaskResult,
    trace_captured: dict,
    agent,
    job: JobSpec,
):
    """Save structured execution state for later --evalonly evaluation.

    The saved state includes 3 reference-compatible fields
    (key_results, trace_summary, step_count) so any run can be
    promoted to a reference via ``generate_reference promote``.
    """
    from server.storage.trace_utils import build_trace_summary, extract_key_results

    tool_logs_dicts = [asdict(log) for log in trace_captured.get("proxy_logs", [])]

    # Non-substantive tools excluded from step count
    _NON_SUBSTANTIVE = {"send_message", "get_environment_info"}
    substantive_count = sum(
        1 for log in tool_logs_dicts if log["name"] not in _NON_SUBSTANTIVE
    )

    # Workspace path for key_results extraction
    workspace_path = str(result_dir / "agent_files")

    # Use result.token_usage (populated before agent._token_records is cleared)
    # instead of re-reading agent.get_token_records() which returns [].
    agent_usage = result.token_usage.get("agent", {}) if result.token_usage else {}

    # Merge per-turn content_blocks into conversation entries.
    # content_blocks is {turn_index: [blocks]} where turn_index maps to
    # assistant turn positions in the conversation.
    content_blocks_map = trace_captured.get("content_blocks", {})
    conversation_entries = []
    assistant_idx = 0
    for t in result.turns:
        entry: dict = {"role": t.role, "content": t.content}
        if t.role in ("assistant", "tutor"):
            blocks = content_blocks_map.get(assistant_idx)
            if blocks:
                entry["content_blocks"] = blocks
            assistant_idx += 1
        conversation_entries.append(entry)

    state = {
        "task_id": result.task_id,
        "persona_id": result.persona_id,
        "conversation": conversation_entries,
        "tool_logs": tool_logs_dicts,
        "distractor_names": trace_captured.get("distractor_names", []),
        "workspace_files": result.workspace_files or [],
        "agent_cost": {
            "input_tokens": agent_usage.get("input_tokens", 0),
            "output_tokens": agent_usage.get("output_tokens", 0),
            "cost_usd": agent_usage.get("cost_usd", 0),
            "api_calls": agent_usage.get("api_calls", 0),
            "model": agent_usage.get("model", getattr(agent, "model", "unknown")),
        },
        "simulator_cost": (
            result.token_usage.get("simulator", {}).get("cost_usd", 0.0)
            if result.token_usage
            else 0.0
        ),
        "duration_seconds": result.duration_seconds,
        # Reference-compatible fields (enable promote to reference)
        "key_results": extract_key_results(workspace_path, tool_logs_dicts),
        "trace_summary": build_trace_summary(tool_logs_dicts),
        "step_count": substantive_count,
        # Thinking/COT trace (Anthropic extended thinking)
        "thinking_trace": trace_captured.get("thinking_trace", []),
    }
    (result_dir / "run_state.json").write_text(
        json.dumps(state, indent=2, default=str), encoding="utf-8"
    )


def _eval_results_from_task_result(result: TaskResult) -> dict:
    return {
        "quant_result": result.quant_result_score,
        "quant_process": result.quant_process_score,
        "process_metrics": result.process_metrics,
        "eval_script_detail": result.eval_script_detail,
        "code_eval": result.code_eval,
        "result_judge": result.result_judge,
        "code_process": result.code_process,
        "tool_usage": result.tool_usage,
    }


def _save_job_reports(
    result_dir: Path,
    result: TaskResult,
    trace_captured: dict,
    agent,
    job: JobSpec,
):
    """Save run_state.json and append score_n JSON when evaluation ran."""

    _save_run_state(result_dir, result, trace_captured, agent, job)

    if not job.skip_eval and not result.eval_aborted:
        from server.storage.eval_writer import save_eval_results

        save_eval_results(
            task=job.task,
            result_dir=result_dir,
            eval_results=_eval_results_from_task_result(result),
            eval_mode=job.eval_mode,
            eval_model=job.eval_model,
            duration=result.duration_seconds,
        )


# ──────────────────────────────────────────────────────────────
# --evalonly: evaluate previously saved --runonly results
# ──────────────────────────────────────────────────────────────


class _ProxyStub:
    """Lightweight proxy substitute for --evalonly.

    Provides the same get_logs() / get_distractor_names() interface
    that the evaluation pipeline reads from MCPProxy.
    """

    def __init__(self, logs: list, distractor_names: list):
        self._logs = logs
        self._distractor_names = distractor_names

    def get_logs(self):
        return self._logs

    def get_distractor_names(self):
        return self._distractor_names


def eval_single_job(job: JobSpec, cancel_event=None) -> JobResult:
    """Evaluate a previously saved --runonly result.

    Loads run_state.json from the result directory, reconstructs
    tool logs and conversation, runs the full evaluation pipeline,
    and appends ``evaluations/score_n/score.json`` + ``cost.json``.
    """
    start = time.time()

    result_dir = (
        job.result_base_dir
        / job.task.category.value
        / job.task.task_id
        / job.persona.persona_id
    )
    state_path = result_dir / "run_state.json"

    if not state_path.exists():
        return JobResult(
            job=job,
            error=f"run_state.json not found at {result_dir}",
            duration_seconds=time.time() - start,
        )

    try:
        from mcp_servers.proxy.mcp_proxy import ToolCallLog

        from orchestrator.orchestrator import BenchmarkOrchestrator

        state = json.loads(state_path.read_text(encoding="utf-8"))

        # Reconstruct ToolCallLog objects
        tool_logs = [ToolCallLog(**log) for log in state["tool_logs"]]

        # Reconstruct proxy stub
        proxy = _ProxyStub(tool_logs, state.get("distractor_names", []))

        # Workspace = agent_files/ directory
        workspace_path = str(result_dir / "agent_files")

        # Conversation as list of dicts
        conversation = state["conversation"]

        # Create orchestrator (for _evaluate_task only — no docker needed)
        orchestrator = BenchmarkOrchestrator(
            bench_root=str(Path(__file__).parent.parent.parent),
            use_docker=False,
            eval_model=job.eval_model,
        )

        # Build TaskResult shell with run-phase data
        result = TaskResult(
            task_id=job.task.task_id,
            persona_id=job.persona.persona_id,
            run_index=job.trial_index,
            difficulty=job.task.difficulty.value,
            category=job.task.category.value,
            requires_code=job.task.requires_code,
            workspace_files=state.get("workspace_files", []),
        )
        from orchestrator.schemas import ConversationTurn

        for t in conversation:
            result.turns.append(ConversationTurn(role=t["role"], content=t["content"]))

        result.duration_seconds = state.get("duration_seconds", 0.0)

        # Run evaluation
        print(
            f"  [EVALONLY] Evaluating {job.task.task_id} x {job.persona.persona_id}..."
        )
        eval_results = orchestrator._evaluate_task(
            job.task,
            job.persona,
            workspace_path,
            proxy,
            conversation,
            cancel_event=cancel_event,
            eval_mode=job.eval_mode,
        )

        # Populate result with evaluation scores
        from orchestrator.eval_helpers import populate_eval_results

        populate_eval_results(
            result,
            eval_results,
            category=job.task.category.value,
            requires_code=job.task.requires_code,
            eval_mode=job.eval_mode,
        )

        # Merge costs: run-phase (from state) + eval-phase (from evaluation)
        from orchestrator.eval_helpers import aggregate_eval_cost, build_token_usage

        agent_cost_data = state.get("agent_cost", {})
        eval_cost, eval_cost_by_model, eval_cost_by_stage_model = aggregate_eval_cost(
            result
        )

        result.token_usage, result.cost_usd = build_token_usage(
            agent_input=agent_cost_data.get("input_tokens", 0),
            agent_output=agent_cost_data.get("output_tokens", 0),
            agent_cost=agent_cost_data.get("cost_usd", 0.0),
            api_calls=agent_cost_data.get("api_calls", 0),
            agent_model=agent_cost_data.get("model", "unknown"),
            sim_cost=state.get("simulator_cost", 0.0),
            sim_model="unknown",
            eval_cost=eval_cost,
            eval_cost_by_model=eval_cost_by_model,
            eval_cost_by_stage_model=eval_cost_by_stage_model,
        )

        from server.storage.eval_writer import save_eval_results

        save_eval_results(
            task=job.task,
            result_dir=result_dir,
            eval_results=eval_results,
            eval_mode=job.eval_mode,
            eval_model=job.eval_model,
            duration=time.time() - start,
        )

        return JobResult(
            job=job,
            task_result=result,
            error=None,
            duration_seconds=time.time() - start,
        )
    except Exception as e:
        return JobResult(
            job=job,
            task_result=None,
            error=str(e),
            duration_seconds=time.time() - start,
        )
