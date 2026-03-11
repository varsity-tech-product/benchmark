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
    max_turns: int
    use_docker: bool
    save_result: bool
    result_base_dir: Path  # e.g. bench/results/run-single/openai/gpt-5.2/
    eval_model: Optional[str] = None
    simulator_model: Optional[str] = None
    model_override: Optional[str] = None
    trial_index: int = 0
    skip_eval: bool = False


@dataclass
class JobResult:
    """Result of a single benchmark job."""

    job: JobSpec
    task_result: Optional[TaskResult] = None
    trace_captured: dict = field(default_factory=dict)
    error: Optional[str] = None
    duration_seconds: float = 0.0


def run_single_job(job: JobSpec, create_agent_fn=None) -> JobResult:
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
            bench_root=str(Path(__file__).parent.parent),
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
            max_turns=job.max_turns or 5,
            tools_enabled=condition.tools_enabled,
            pre_teardown_hook=_capture if job.save_result else None,
            skip_eval=job.skip_eval,
        )

        # 5. Save reports
        if job.save_result and result_dir is not None:
            _save_job_reports(result_dir, task_result, trace_captured, agent, job)

        return JobResult(
            job=job,
            task_result=task_result,
            trace_captured=trace_captured,
            error=None,
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
    from config.prompt_config import BASELINE_SYSTEM_PROMPT, TUTOR_SYSTEM_PROMPT

    condition = CONDITIONS[job.condition_name]

    system_prompt = (
        TUTOR_SYSTEM_PROMPT
        if condition.prompt_mode == "tutor"
        else BASELINE_SYSTEM_PROMPT
    )

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
    """Save structured execution state for later --evalonly evaluation."""
    agent_records = (
        agent.get_token_records() if hasattr(agent, "get_token_records") else []
    )
    state = {
        "task_id": result.task_id,
        "persona_id": result.persona_id,
        "conversation": [{"role": t.role, "content": t.content} for t in result.turns],
        "tool_logs": [asdict(log) for log in trace_captured.get("proxy_logs", [])],
        "distractor_names": trace_captured.get("distractor_names", []),
        "workspace_files": result.workspace_files or [],
        "agent_cost": {
            "input_tokens": sum(r.input_tokens for r in agent_records),
            "output_tokens": sum(r.output_tokens for r in agent_records),
            "cost_usd": sum(r.cost_usd for r in agent_records),
            "api_calls": len(agent_records),
            "model": getattr(agent, "model", "unknown"),
        },
        "simulator_cost": (
            result.token_usage.get("simulator", {}).get("cost_usd", 0.0)
            if result.token_usage
            else 0.0
        ),
        "duration_seconds": result.duration_seconds,
    }
    (result_dir / "run_state.json").write_text(
        json.dumps(state, indent=2, default=str), encoding="utf-8"
    )


def _save_job_reports(
    result_dir: Path,
    result: TaskResult,
    trace_captured: dict,
    agent,
    job: JobSpec,
):
    """Save scores.md, trace.md, cost.md to result_dir."""
    from config.conditions import CONDITIONS

    condition = CONDITIONS[job.condition_name]

    # scores.md: skip when evaluation was aborted or skipped (--runonly)
    if job.skip_eval:
        _save_run_state(result_dir, result, trace_captured, agent, job)
    elif not result.eval_aborted:
        from evaluation.score_report import generate_score_report

        (result_dir / "scores.md").write_text(
            generate_score_report(result), encoding="utf-8"
        )
    else:
        (result_dir / "ABORTED.md").write_text(
            f"# Evaluation Aborted\n\n{result.error}\n",
            encoding="utf-8",
        )

    # trace.md
    if "proxy_logs" in trace_captured:
        from evaluation.trace_report import generate_trace_md

        (result_dir / "trace.md").write_text(
            generate_trace_md(
                result,
                trace_captured["proxy_logs"],
                agent_name=job.agent_type,
                model=agent.model,
                condition=condition.name,
            ),
            encoding="utf-8",
        )

    # cost.md
    from evaluation.cost_report import generate_cost_report

    (result_dir / "cost.md").write_text(generate_cost_report(result), encoding="utf-8")


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


def eval_single_job(job: JobSpec) -> JobResult:
    """Evaluate a previously saved --runonly result.

    Loads run_state.json from the result directory, reconstructs
    tool logs and conversation, runs the full evaluation pipeline,
    and saves scores.md + updated cost.md.
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

        # Reconstruct ConversationalTestCase
        try:
            from deepeval.test_case import ConversationalTestCase
            from deepeval.test_case import LLMTestCaseTurn as Turn

            test_case = ConversationalTestCase(
                turns=[Turn(role=t["role"], content=t["content"]) for t in conversation]
            )
        except ImportError:
            test_case = None

        # Create orchestrator (for _evaluate_task only — no docker needed)
        orchestrator = BenchmarkOrchestrator(
            bench_root=str(Path(__file__).parent.parent),
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
            conversational_test_case=test_case,
        )

        # Populate result with evaluation scores
        result.quant_result_score = eval_results.get("quant_result", 0.0)
        result.quant_process_score = eval_results.get("quant_process", 0.0)
        result.tutor_scores = eval_results.get("tutor_scores", {})
        result.tutor_scores_by_model = eval_results.get("tutor_scores_by_model", {})
        result.tutor_fallback_count = eval_results.get("tutor_fallback_count", 0)
        result.process_metrics = eval_results.get("process_metrics", {})
        result.eval_script_detail = eval_results.get("eval_script_detail", {})
        result.code_eval = eval_results.get("code_eval", {})
        result.result_judge = eval_results.get("result_judge", {})
        result.code_process = eval_results.get("process_metrics", {}).get(
            "code_process", {}
        )
        result.tool_usage = eval_results.get("tool_usage", {})

        from evaluation.scoring import compute_task_score

        score_breakdown = compute_task_score(
            quant_result_score=result.quant_result_score,
            quant_process_score=result.quant_process_score,
            tutor_dimension_scores=result.tutor_scores,
            category=job.task.category.value,
            requires_code=job.task.requires_code,
        )
        result.overall_score = score_breakdown["overall_score"]

        # Merge costs: run-phase (from state) + eval-phase (from evaluation)
        agent_cost_data = state.get("agent_cost", {})
        sim_cost = state.get("simulator_cost", 0.0)
        eval_cost = 0.0
        eval_cost += result.process_metrics.get("_eval_cost", 0.0)
        eval_cost += result.result_judge.get("_eval_cost", 0.0)
        eval_cost += result.tutor_scores.get("_eval_cost", 0.0)

        eval_cost_by_model: dict[str, float] = {}
        eval_cost_by_stage_model: dict[str, dict[str, float]] = {}
        for stage_name, src in [
            ("Tutor 7D", result.tutor_scores),
            ("Process Metrics", result.process_metrics),
            ("Result Judge", result.result_judge),
        ]:
            by_model = src.get("_eval_cost_by_model", {})
            if by_model:
                eval_cost_by_stage_model[stage_name] = {
                    m: round(c, 6) for m, c in by_model.items()
                }
            for m, c in by_model.items():
                eval_cost_by_model[m] = round(eval_cost_by_model.get(m, 0.0) + c, 6)

        agent_cost_usd = agent_cost_data.get("cost_usd", 0.0)
        result.token_usage = {
            "agent": {
                "input_tokens": agent_cost_data.get("input_tokens", 0),
                "output_tokens": agent_cost_data.get("output_tokens", 0),
                "cost_usd": round(agent_cost_usd, 6),
                "api_calls": agent_cost_data.get("api_calls", 0),
                "model": agent_cost_data.get("model", "unknown"),
            },
            "simulator": {
                "cost_usd": round(sim_cost, 6),
                "model": "unknown",
            },
            "eval": {
                "cost_usd": round(eval_cost, 6),
                "by_model": eval_cost_by_model,
                "by_stage_model": eval_cost_by_stage_model,
            },
            "total": {
                "cost_usd": round(agent_cost_usd + sim_cost + eval_cost, 6),
            },
        }
        result.cost_usd = round(agent_cost_usd + sim_cost + eval_cost, 4)

        # Save scores.md + updated cost.md
        from evaluation.cost_report import generate_cost_report
        from evaluation.score_report import generate_score_report

        (result_dir / "scores.md").write_text(
            generate_score_report(result), encoding="utf-8"
        )
        (result_dir / "cost.md").write_text(
            generate_cost_report(result), encoding="utf-8"
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
