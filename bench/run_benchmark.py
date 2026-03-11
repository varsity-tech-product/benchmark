#!/usr/bin/env python3
"""QuantTutorBench CLI - Benchmark runner for quantitative finance tutoring agents.

Three CLI dimensions control the evaluation:
  --agent      SDK / model family: generic, openai, anthropic, google
  --condition  Test condition (2x2 matrix): agent, baseline, pure_llm, pure_llm_baseline
  --layer      Which layer(s) to run: all (default), 1 (single-turn), 2 (multi-turn)

Usage:
    python run_benchmark.py run --agent openai --condition agent                    # Both layers
    python run_benchmark.py run --agent openai --condition agent --layer 2          # Layer 2 only
    python run_benchmark.py run --agent openai --condition pure_llm --layer 1       # Layer 1 only
    python run_benchmark.py run --agent openai --layer all --l1-max-items 100       # Cap Layer 1
    python run_benchmark.py run-single --task S01_ma_crossover --persona beginner_no_finance
    python run_benchmark.py run-single --task D01 --workers 3                       # All personas
    python run_benchmark.py run-group --group data_analysis --docker --workers 3
    python run_benchmark.py run-layer2 --agent openai --docker --workers 3
    python run_benchmark.py run-layer1 --max-items 10                               # Standalone L1
    python run_benchmark.py list-tasks
    python run_benchmark.py test-e2e
"""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add bench root to path
BENCH_ROOT = Path(__file__).parent
PROJECT_ROOT = BENCH_ROOT.parent
sys.path.insert(0, str(BENCH_ROOT))

# Load .env from project root
load_dotenv(PROJECT_ROOT / ".env")

# API routing strategy:
#   - Anthropic/Google SDK adapters use their own API keys directly.
#   - OpenAI Agent SDK routes through OpenRouter by default for higher
#     rate limits and parallelism.
#   - Everything else (DeepEval judge, simulator, GenericLLM) goes through OpenRouter.
#   - resolve_deepeval_model() always returns a GPTModel routed via OpenRouter,
#     so DeepEval never competes with native SDK adapters for rate limits.
#   - The env-var bridge below is a fallback for any DeepEval code that reads
#     OPENAI_API_KEY directly instead of using our resolved model object.
from config.conditions import CONDITION_NAMES, CONDITIONS
from config.llm_config import (
    AGENT_DEFAULT_MODEL,
    OPENROUTER_BASE_URL,
)
from config.model_resolver import get_model_for_agent

if os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
    os.environ["OPENAI_BASE_URL"] = OPENROUTER_BASE_URL


# ---------------------------------------------------------------------------
# Task & group loaders
# ---------------------------------------------------------------------------

_LAYER2_GROUPS = [
    "data_analysis",
    "strategy",
    "backtest",
    "implementation",
    "end_to_end",
    "debug",
    "adversarial",
]


def _find_and_load_task(task_id: str):
    """Find a task JSON by ID across all category directories."""
    from orchestrator.schemas import QuantTutorTask

    for category_dir in (BENCH_ROOT / "tasks" / "layer2").iterdir():
        if category_dir.is_dir():
            for f in category_dir.glob("*.json"):
                if task_id in f.stem:
                    with open(f) as fh:
                        return QuantTutorTask(**json.load(fh))
    print(f"Task not found: {task_id}")
    sys.exit(1)


def _load_tasks_by_group(group: str) -> list:
    """Load all tasks in a category group."""
    from orchestrator.schemas import QuantTutorTask

    group_dir = BENCH_ROOT / "tasks" / "layer2" / group
    if not group_dir.is_dir():
        print(f"Group not found: {group}")
        sys.exit(1)
    tasks = []
    for f in sorted(group_dir.glob("*.json")):
        with open(f) as fh:
            tasks.append(QuantTutorTask(**json.load(fh)))
    return tasks


def _load_all_layer2_tasks() -> list:
    """Load all Layer 2 tasks across all categories."""
    from orchestrator.schemas import QuantTutorTask

    tasks = []
    for category_dir in sorted((BENCH_ROOT / "tasks" / "layer2").iterdir()):
        if category_dir.is_dir():
            for f in sorted(category_dir.glob("*.json")):
                with open(f) as fh:
                    tasks.append(QuantTutorTask(**json.load(fh)))
    return tasks


def _load_persona(persona_id: str):
    """Load a persona by ID."""
    from orchestrator.schemas import StudentPersona

    persona_path = BENCH_ROOT / "personas" / f"{persona_id}.json"
    with open(persona_path) as f:
        return StudentPersona(**json.load(f))


# ---------------------------------------------------------------------------
# Job building helpers
# ---------------------------------------------------------------------------


def _build_result_base_dir(args, subcommand: str = "run-single") -> Path:
    """Build the base result directory from args."""
    agent_type = getattr(args, "agent", "generic")
    model_override = getattr(args, "model", None)
    if model_override:
        model_short = model_override.split("/")[-1]
    else:
        model = get_model_for_agent(agent_type)
        model_short = model.split("/")[-1] if "/" in model else model
    return BENCH_ROOT / "results" / subcommand / agent_type / model_short


def _build_job_spec(task, persona, args, result_base_dir: Path):
    """Build a JobSpec from a task, persona, and CLI args."""
    from orchestrator.job_runner import JobSpec

    runonly = getattr(args, "runonly", False)
    evalonly = getattr(args, "evalonly", False)
    if runonly and evalonly:
        print("ERROR: --runonly and --evalonly are mutually exclusive.")
        sys.exit(1)
    return JobSpec(
        task=task,
        persona=persona,
        agent_type=getattr(args, "agent", "generic"),
        condition_name=getattr(args, "condition", "agent"),
        max_turns=getattr(args, "max_turns", 5),
        use_docker=getattr(args, "docker", False),
        save_result=getattr(args, "save_result", False) or runonly or evalonly,
        result_base_dir=result_base_dir,
        eval_model=getattr(args, "eval_model", None),
        simulator_model=getattr(args, "simulator_model", None),
        model_override=getattr(args, "model", None),
        skip_eval=runonly,
    )


def _build_jobs_for_tasks(tasks, persona_ids, args, result_base_dir: Path) -> list:
    """Build JobSpec list for multiple tasks × personas."""

    jobs = []
    for task in tasks:
        pids = persona_ids or task.persona_ids
        for pid in pids:
            persona = _load_persona(pid)
            jobs.append(_build_job_spec(task, persona, args, result_base_dir))
    return jobs


# ---------------------------------------------------------------------------
# Progress + summary display
# ---------------------------------------------------------------------------


def _progress(completed: int, total: int, latest):
    """Print progress update."""
    if latest.task_result:
        status = "OK"
    elif latest.error:
        status = f"ERR: {latest.error[:60]}"
    else:
        status = "???"
    print(
        f"  [{completed}/{total}] {latest.job.task.task_id} x "
        f"{latest.job.persona.persona_id} — {status} "
        f"({latest.duration_seconds:.1f}s)"
    )


def _print_single_result(result):
    """Print detailed result for a single task (preserves existing display)."""
    print(f"\n--- Conversation ({len(result.turns)} turns) ---")
    for turn in result.turns:
        prefix = "Student" if turn.role == "user" else "Tutor"
        print(f"\n[{prefix}]: {turn.content[:200]}...")

    print("\n--- Scores ---")
    print(f"Quant Result:  {result.quant_result_score:.4f}")
    print(f"Quant Process: {result.quant_process_score:.4f}")
    print(f"Overall:       {result.overall_score:.4f}")

    if result.tutor_scores:
        print("\n--- Tutor Dimension Scores (7D, averaged) ---")
        for dim, score in sorted(result.tutor_scores.items()):
            if dim.startswith("_"):
                continue
            if isinstance(score, (int, float)):
                print(f"  {dim}: {score:.4f}")
            else:
                print(f"  {dim}: {score}")

    if result.tutor_scores_by_model:
        print("\n--- Per-Model Tutor Scores ---")
        for model_name, dim_scores in sorted(result.tutor_scores_by_model.items()):
            clean = {
                k: v
                for k, v in dim_scores.items()
                if not k.startswith("_") and isinstance(v, (int, float))
            }
            avg = sum(clean.values()) / len(clean) if clean else 0.0
            print(f"  [{model_name}] avg={avg:.4f}")
            for dim, score in sorted(clean.items()):
                print(f"    {dim}: {score:.4f}")

    if result.process_metrics:
        print("\n--- Process Metrics (QP 7 Dimensions) ---")
        _QP_METRICS = [
            "tool_usage",
            "step_efficiency",
            "process_reasonableness",
            "process_alignment",
            "code_process",
            "role_adherence",
            "topic_adherence",
        ]
        print(f"  {'Metric':<25} {'Score':>8}  {'Status':>6}")
        print(f"  {'-'*25} {'-'*8}  {'-'*6}")
        for mn in _QP_METRICS:
            v = result.process_metrics.get(mn)
            if v is None:
                continue
            if isinstance(v, dict):
                sc = v.get("score")
                skipped = v.get("skipped", False)
                if skipped:
                    print(f"  {mn:<25} {'N/A':>8}  {'SKIP':>6}")
                elif sc is not None:
                    st = "PASS" if v.get("passed") else "FAIL"
                    print(f"  {mn:<25} {sc:>8.4f}  {st:>6}")
                else:
                    print(f"  {mn:<25} {'N/A':>8}  {'?':>6}")
            elif isinstance(v, (int, float)):
                print(f"  {mn:<25} {v:>8.4f}")
        agg = result.process_metrics.get("aggregate_process_score")
        if agg is not None:
            print(f"  {'-'*25} {'-'*8}  {'-'*6}")
            print(f"  {'AGGREGATE':<25} {agg:>8.4f}")

        _pm = result.process_metrics.get("_per_model")
        if _pm:
            print("\n  Per-Model QP Breakdown:")
            for mname in sorted(_pm.keys()):
                mdata = _pm[mname]
                short = mname.split("/")[-1] if "/" in mname else mname
                agg_m = mdata.get("aggregate_process_score", "?")
                print(f"    [{short}] aggregate={agg_m}")
                for mn in _QP_METRICS:
                    ms = mdata.get(mn)
                    if ms is not None and isinstance(ms, (int, float)):
                        print(f"      {mn:<25} {ms:>8.4f}")
                    elif ms is not None:
                        print(f"      {mn:<25} {ms}")

    if result.code_eval:
        print("\n--- Code Execution QR ---")
        ce = result.code_eval
        print(f"  Applicable: {ce.get('applicable', False)}")
        if ce.get("applicable"):
            print(f"  Combined score: {ce.get('score', 0):.4f}")
            sa = ce.get("static_analysis", {})
            if sa:
                print(
                    f"  Layer A (static):    {sa.get('score', 0):.4f}"
                    f"  (syntax={'OK' if sa.get('syntax_valid') else 'FAIL'},"
                    f" files={sa.get('files_analyzed', 0)},"
                    f" funcs={sa.get('total_functions', 0)})"
                )
            ex = ce.get("execution", {})
            if ex:
                print(
                    f"  Layer B (execution): {ex.get('score', 0):.4f}"
                    f"  (calls={ex.get('exec_calls_found', 0)},"
                    f" success_rate={ex.get('success_rate', 0):.2f},"
                    f" untested={ex.get('untested_files', [])})"
                )
            ov = ce.get("output_verification")
            if ov:
                print(
                    f"  Layer C (output):    {ov.get('score', 0):.4f}"
                    f"  (accuracy={ov.get('numerical_accuracy', 0):.2f},"
                    f" completeness={ov.get('output_completeness', 0):.2f},"
                    f" metrics_compared={ov.get('metrics_compared', 0)})"
                )
            else:
                print("  Layer C (output):    SKIPPED (no reference)")

    if result.result_judge:
        print("\n--- LLM Result Judge ---")
        rj = result.result_judge
        print(f"  Score: {rj.get('score', 0):.4f}")
        sub = rj.get("sub_scores", {})
        if sub:
            print(
                f"  Completeness: {sub.get('completeness', '?')}"
                f"  Correctness: {sub.get('correctness', '?')}"
            )
        print(f"  Has reference: {rj.get('has_reference', False)}")
        reason = rj.get("reason", "")
        if reason:
            print(f"  Reason: {reason[:200]}")

    if result.code_process:
        print("\n--- Code Process Quality ---")
        cp = result.code_process
        applicable = cp.get("applicable", False)
        print(f"  Applicable: {applicable}")
        if applicable:
            print(f"  Combined score: {cp.get('score', 0):.4f}")
            prog = cp.get("programmatic", {})
            if prog and prog.get("applicable"):
                psub = prog.get("sub_scores", {})
                parts = []
                for k in (
                    "iterative_refinement",
                    "test_before_deliver",
                    "error_recovery",
                    "code_evolution",
                ):
                    v = psub.get(k)
                    parts.append(f"{k}={v}" if v is not None else f"{k}=N/A")
                print(
                    f"  Programmatic ({prog.get('score', 0):.4f}): {', '.join(parts)}"
                )
            llm = cp.get("llm_judged", {})
            if llm and llm.get("applicable"):
                lsub = llm.get("sub_scores", {})
                parts = [
                    f"{k}={lsub.get(k, '?')}"
                    for k in (
                        "debugging_competence",
                        "incremental_development",
                        "code_explanation_quality",
                    )
                ]
                print(f"  LLM-judged  ({llm.get('score', 0):.4f}): {', '.join(parts)}")
            reason = cp.get("llm_judged", {}).get("reason", "")
            if reason:
                print(f"  Reason: {reason[:200]}")

    if result.tool_usage:
        print("\n--- Tool Usage ---")
        tu = result.tool_usage
        print(f"  Score: {tu.get('score', 0):.4f}")
        print(
            f"  base={tu.get('base', '?')}  "
            f"bonus={tu.get('bonus', '?')}  "
            f"penalty_exp={tu.get('penalty_expected', '?')}  "
            f"penalty_dist={tu.get('penalty_distractor', '?')}"
        )
        if tu.get("missing_expected"):
            print(f"  Missing expected: {tu['missing_expected']}")
        if tu.get("called_distractors"):
            print(f"  Called distractors: {tu['called_distractors']}")
        if tu.get("called_convenient"):
            print(f"  Called convenient: {tu['called_convenient']}")

    if result.error:
        print(f"\nError: {result.error}")


def _print_group_summary(results: list):
    """Print summary table after group/layer execution."""
    is_runonly = any(r.job.skip_eval for r in results if r.job)

    print(f"\n{'='*110}")
    print("Summary" + (" (RUNONLY — no evaluation)" if is_runonly else ""))
    print(f"{'='*110}")

    if is_runonly:
        print(f"{'Task':<30} {'Persona':<25} {'Turns':>6} " f"{'Time':>7} {'Status'}")
        print("-" * 80)
    else:
        print(
            f"{'Task':<30} {'Persona':<25} {'OAS':>6} {'QR':>6} {'QP':>6} "
            f"{'Time':>7} {'Status'}"
        )
        print("-" * 110)

    total_time = 0.0
    total_cost = 0.0
    ok_count = 0
    err_count = 0
    for r in results:
        if r.task_result:
            tr = r.task_result
            if is_runonly:
                print(
                    f"{tr.task_id:<30} {tr.persona_id:<25} "
                    f"{len(tr.turns):>6} {r.duration_seconds:>6.1f}s OK"
                )
            else:
                print(
                    f"{tr.task_id:<30} {tr.persona_id:<25} "
                    f"{tr.overall_score:>6.4f} {tr.quant_result_score:>6.4f} "
                    f"{tr.quant_process_score:>6.4f} {r.duration_seconds:>6.1f}s OK"
                )
            total_cost += tr.cost_usd
            ok_count += 1
        else:
            if is_runonly:
                print(
                    f"{r.job.task.task_id:<30} {r.job.persona.persona_id:<25} "
                    f"{'---':>6} {r.duration_seconds:>6.1f}s ERR"
                )
            else:
                print(
                    f"{r.job.task.task_id:<30} {r.job.persona.persona_id:<25} "
                    f"{'---':>6} {'---':>6} {'---':>6} {r.duration_seconds:>6.1f}s ERR"
                )
            err_count += 1
        total_time += r.duration_seconds

    print("-" * (80 if is_runonly else 110))
    status_parts = [f"{ok_count} OK"]
    if err_count:
        status_parts.append(f"{err_count} ERR")
    print(
        f"Total: {len(results)} jobs ({', '.join(status_parts)}), "
        f"{total_time:.1f}s wall time, ${total_cost:.4f}"
    )


def _save_group_summary(results: list, label: str, result_base_dir: Path):
    """Save a summary.md for group/layer runs."""
    result_base_dir.mkdir(parents=True, exist_ok=True)
    is_runonly = any(r.job.skip_eval for r in results if r.job)

    lines = [f"# {label} Summary\n"]
    if is_runonly:
        lines.append("*RUNONLY mode — no evaluation scores*\n\n")
        lines.append(
            "| Task | Persona | Turns | Time | Status |\n"
            "|------|---------|-------|------|--------|\n"
        )
    else:
        lines.append(
            "| Task | Persona | OAS | QR | QP | Time | Status |\n"
            "|------|---------|-----|----|----|------|--------|\n"
        )
    for r in results:
        if r.task_result:
            tr = r.task_result
            if is_runonly:
                lines.append(
                    f"| {tr.task_id} | {tr.persona_id} | "
                    f"{len(tr.turns)} | {r.duration_seconds:.1f}s | OK |\n"
                )
            else:
                lines.append(
                    f"| {tr.task_id} | {tr.persona_id} | "
                    f"{tr.overall_score:.4f} | {tr.quant_result_score:.4f} | "
                    f"{tr.quant_process_score:.4f} | {r.duration_seconds:.1f}s | OK |\n"
                )
        else:
            if is_runonly:
                lines.append(
                    f"| {r.job.task.task_id} | {r.job.persona.persona_id} | "
                    f"--- | {r.duration_seconds:.1f}s | ERR |\n"
                )
            else:
                lines.append(
                    f"| {r.job.task.task_id} | {r.job.persona.persona_id} | "
                    f"--- | --- | --- | {r.duration_seconds:.1f}s | ERR |\n"
                )
    (result_base_dir / "summary.md").write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Agent creation (used by cmd_run and cmd_run_layer1)
# ---------------------------------------------------------------------------


def _create_agent(args):
    """Create the appropriate agent adapter based on --agent and --condition.

    The 2x2 matrix:
      --condition agent            → SDK adapter + native model + TUTOR prompt
      --condition baseline         → SDK adapter + native model + BASELINE prompt
      --condition pure_llm         → GenericLLM + OpenRouter model + TUTOR prompt
      --condition pure_llm_baseline → GenericLLM + OpenRouter model + BASELINE prompt

    OpenAI Agent SDK always routes through OpenRouter for higher rate limits.
    Anthropic and Google SDKs use their native APIs.
    """
    from config.prompt_config import BASELINE_SYSTEM_PROMPT, TUTOR_SYSTEM_PROMPT

    agent_type = getattr(args, "agent", "generic")
    condition = CONDITIONS[getattr(args, "condition", "agent")]
    model_override = getattr(args, "model", None)

    system_prompt = (
        TUTOR_SYSTEM_PROMPT
        if condition.prompt_mode == "tutor"
        else BASELINE_SYSTEM_PROMPT
    )

    # Include model short name in agent_name for distinguishable results
    if model_override:
        model_short = model_override.split("/")[-1]
        agent_name = f"{agent_type}_{condition.name}_{model_short}"
    else:
        agent_name = f"{agent_type}_{condition.name}"

    # Pure LLM conditions: no SDK framework needed, use GenericLLM via OpenRouter
    if not condition.tools_enabled:
        from orchestrator.agent_adapters.generic_adapter import GenericLLMAdapter

        model = model_override or get_model_for_agent(agent_type, use_openrouter=True)
        return GenericLLMAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )

    # Tools-enabled conditions: use the native SDK adapter
    if agent_type == "anthropic":
        from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

        model = model_override or get_model_for_agent("anthropic")
        return ClaudeAgentAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )
    elif agent_type == "google":
        from orchestrator.agent_adapters.google_adapter import GoogleAdapter

        model = model_override or get_model_for_agent("google")
        return GoogleAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )
    elif agent_type == "openai":
        from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter

        # Always route through OpenRouter for higher rate limits / parallelism
        model = model_override or get_model_for_agent("openai", use_openrouter=True)
        return OpenAIAgentAdapter(
            model=model,
            base_url=OPENROUTER_BASE_URL,
            system_prompt=system_prompt,
            agent_name=agent_name,
        )
    else:  # generic
        from orchestrator.agent_adapters.generic_adapter import GenericLLMAdapter

        model = model_override or get_model_for_agent("generic")
        return GenericLLMAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )


def _make_layer1_callback(agent):
    """Bridge a BaseAgentAdapter to Layer1Runner's callback interface.

    Supports two calling conventions:
    1. Pure Q&A: callback(context, question) -> str
    2. Tool-enabled: callback(context, question, tools=..., tool_callback=...) -> str

    The agent's system prompt (tutor vs baseline) is already set by _create_agent().
    """

    def callback(context: str, question: str, tools=None, tool_callback=None) -> str:
        if context:
            messages = [
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuestion: {question}",
                }
            ]
        else:
            messages = [{"role": "user", "content": question}]
        return agent.generate_response(
            messages=messages,
            available_tools=tools or [],
            tool_callback=tool_callback,
        )

    return callback


# ---------------------------------------------------------------------------
# Concurrency guard
# ---------------------------------------------------------------------------


def _validate_workers(args):
    """Enforce safety: local mode (no Docker) must use workers=1.

    Exception: --evalonly is safe with workers > 1 (no containers, no shared env).
    """
    workers = getattr(args, "workers", 1)
    docker = getattr(args, "docker", False)
    evalonly = getattr(args, "evalonly", False)
    if workers > 1 and not docker and not evalonly:
        print(
            "ERROR: --workers > 1 requires --docker (or --evalonly). "
            "Local mode uses os.environ which is not thread-safe."
        )
        sys.exit(1)
    return workers


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_run(args):
    """Run the full benchmark (Layer 1 + Layer 2, or individually via --layer)."""
    from evaluation.scoring import compute_combined_benchmark_kpis, compute_task_score
    from orchestrator.orchestrator import BenchmarkOrchestrator
    from orchestrator.schemas import BenchmarkReport

    condition = CONDITIONS[getattr(args, "condition", "agent")]
    agent = _create_agent(args)
    layer = getattr(args, "layer", "all")
    workers = _validate_workers(args)

    orchestrator = BenchmarkOrchestrator(
        bench_root=str(BENCH_ROOT),
        use_docker=args.docker,
        eval_model=getattr(args, "eval_model", None),
        simulator_model=getattr(args, "simulator_model", None),
    )

    task_filter = args.tasks.split(",") if args.tasks else None
    persona_filter = args.personas.split(",") if args.personas else None

    print("=== QuantTutorBench - Unified Benchmark ===")
    print(f"Agent: {agent.agent_name}")
    print(f"Condition: {condition.name} — {condition.description}")
    print(f"Model: {agent.model}")
    print(f"Layer(s): {layer}")
    print(f"Tools: {'enabled' if condition.tools_enabled else 'disabled'}")
    print(f"Prompt: {condition.prompt_mode}")
    print(f"Docker: {args.docker}")
    print(f"Max turns: {args.max_turns}")
    print(f"Trials: {args.trials}")
    print(f"Workers: {workers}")
    print()

    report = BenchmarkReport(agent_name=agent.agent_name)
    layers_evaluated = []

    # ── Phase 1: Layer 1 (single-turn knowledge) ──
    if layer in ("all", "1"):
        print("=== Phase 1: Layer 1 (single-turn knowledge) ===")
        from layer1.data_loader import get_layer1_stats, load_layer1_items
        from layer1.runner import Layer1Runner
        from orchestrator.container_manager import ContainerManager

        l1_max = getattr(args, "l1_max_items", None)
        items = load_layer1_items(max_items=l1_max)
        if items:
            stats = get_layer1_stats(items)
            print(f"  Loaded {stats['total_items']} items")
            print(f"  By category: {stats['by_category']}")

            callback = _make_layer1_callback(agent)
            l1_container_manager = ContainerManager(use_docker=args.docker)
            runner = Layer1Runner(
                agent_callback=callback,
                use_deepeval=not getattr(args, "no_deepeval", False),
                eval_model=getattr(args, "eval_model", None),
                container_manager=l1_container_manager,
                use_docker=args.docker,
            )
            l1_results = runner.run_batch(items, max_items=l1_max)
            l1_summary = Layer1Runner.compute_layer1_scores(l1_results)

            report.layer1_results = [r.to_dict() for r in l1_results]
            report.layer1_summary = l1_summary
            report.layer1_mean_score = l1_summary.get("mean_score", 0.0)
            layers_evaluated.append("layer1")

            print(
                f"  Layer 1 complete: {l1_summary['total_items']} items, "
                f"mean={l1_summary['mean_score']:.4f}"
            )
        else:
            print("  No Layer 1 items found, skipping.")
        print()

    # ── Phase 2: Layer 2 (multi-turn tutoring) ──
    if layer in ("all", "2"):
        print("=== Phase 2: Layer 2 (multi-turn tutoring) ===")

        if workers > 1:
            # Parallel execution via job runner
            from orchestrator.job_runner import JobSpec
            from orchestrator.parallel_runner import run_jobs_parallel

            tasks = _load_all_layer2_tasks()
            result_base_dir = _build_result_base_dir(args, "run")
            save_result = getattr(args, "save_result", False)

            jobs = []
            for task in tasks:
                if task_filter and task.task_id not in task_filter:
                    continue
                for persona_id in task.persona_ids:
                    if persona_filter and persona_id not in persona_filter:
                        continue
                    persona = _load_persona(persona_id)
                    for trial in range(args.trials):
                        jobs.append(
                            JobSpec(
                                task=task,
                                persona=persona,
                                agent_type=getattr(args, "agent", "generic"),
                                condition_name=getattr(args, "condition", "agent"),
                                max_turns=args.max_turns,
                                use_docker=args.docker,
                                save_result=save_result,
                                result_base_dir=result_base_dir,
                                eval_model=getattr(args, "eval_model", None),
                                simulator_model=getattr(args, "simulator_model", None),
                                model_override=getattr(args, "model", None),
                                trial_index=trial,
                            )
                        )

            print(f"  Running {len(jobs)} jobs with {workers} workers...")
            job_results = run_jobs_parallel(
                jobs, max_workers=workers, progress_callback=_progress
            )

            # Merge into report
            for jr in job_results:
                if jr.task_result:
                    result_key = (
                        f"{jr.task_result.task_id}_{jr.task_result.persona_id}"
                        f"_t{jr.job.trial_index}"
                    )
                    report.results_by_task[result_key] = jr.task_result
                    report.total_tasks += 1

            _print_group_summary(job_results)
        else:
            # Sequential execution (original path)
            l2_report = orchestrator.run_benchmark(
                agent=agent,
                task_filter=task_filter,
                persona_filter=persona_filter,
                num_trials=args.trials,
                max_turns_override=args.max_turns,
                tools_enabled=condition.tools_enabled,
            )
            report.results_by_task = l2_report.results_by_task
            report.total_tasks = l2_report.total_tasks
            report.adaptiveness_score = l2_report.adaptiveness_score
            report.process_mastery_score = l2_report.process_mastery_score
            report.results_by_difficulty = l2_report.results_by_difficulty
            report.results_by_category = l2_report.results_by_category

        layers_evaluated.append("layer2")
        print()

    report.layers_evaluated = layers_evaluated

    # ── Phase 3: Combined scoring ──
    all_result_objects = list(report.results_by_task.values())
    all_l2_scores = [
        compute_task_score(
            r.quant_result_score,
            r.quant_process_score,
            r.tutor_scores,
            category=r.category,
            requires_code=r.requires_code,
        )
        for r in all_result_objects
    ]

    combined_kpis = compute_combined_benchmark_kpis(
        layer2_task_results=all_l2_scores,
        layer2_result_objects=all_result_objects if all_result_objects else None,
        layer1_mean_score=report.layer1_mean_score,
        layer1_summary=report.layer1_summary,
        layers_evaluated=layers_evaluated,
    )

    report.overall_agent_score = combined_kpis.get("overall_agent_score", 0.0)
    report.quant_agent_index = combined_kpis.get("quant_agent_index", 0.0)
    report.tutoring_effectiveness_index = combined_kpis.get(
        "tutoring_effectiveness_index", 0.0
    )
    report.combined_result_subscore = combined_kpis.get("combined_result_subscore")

    # Compute KPIs that may not have been set in parallel path
    if workers > 1 and all_l2_scores:
        from evaluation.scoring import compute_benchmark_kpis

        kpis = compute_benchmark_kpis(
            all_l2_scores, task_result_objects=all_result_objects
        )
        report.adaptiveness_score = kpis.get("adaptiveness_score", 0.0)
        report.process_mastery_score = kpis.get("process_mastery_score", 0.0)

        import statistics as _stats
        from collections import defaultdict

        by_diff = defaultdict(list)
        by_cat = defaultdict(list)
        for r_obj, r_score in zip(all_result_objects, all_l2_scores):
            if r_obj.difficulty:
                by_diff[r_obj.difficulty].append(r_score["overall_score"])
            if r_obj.category:
                by_cat[r_obj.category].append(r_score["overall_score"])
        report.results_by_difficulty = {
            k: round(_stats.mean(v), 4) for k, v in sorted(by_diff.items())
        }
        report.results_by_category = {
            k: round(_stats.mean(v), 4) for k, v in sorted(by_cat.items())
        }

    # ── Output ──
    report_path = orchestrator.save_results(report)

    print("=== Benchmark-Level KPIs (§6.3 + §6.4) ===")
    print(f"Layers evaluated:                {', '.join(layers_evaluated)}")
    print(f"Overall Agent Score (OAS):       {report.overall_agent_score:.4f}")
    print(f"Quant Agent Index (QAI):         {report.quant_agent_index:.4f}")
    if report.combined_result_subscore is not None:
        print(f"  Result Sub-score (blended):    {report.combined_result_subscore:.4f}")
        if "layer1" in layers_evaluated:
            print(
                f"    Layer 1 contribution:        {report.layer1_mean_score:.4f} (weight=0.40)"
            )
    print(f"Tutoring Effectiveness (TEI):    {report.tutoring_effectiveness_index:.4f}")
    print(f"Adaptiveness Score (AS):         {report.adaptiveness_score:.4f}")
    print(f"Process Mastery Score (PMS):      {report.process_mastery_score:.4f}")
    if "layer1" in layers_evaluated and report.layer1_summary:
        print(
            f"Layer 1 items evaluated:         {report.layer1_summary['total_items']}"
        )
    if "layer2" in layers_evaluated:
        print(f"Layer 2 tasks evaluated:         {report.total_tasks}")
    print(f"\nReport saved: {report_path}")


def cmd_run_single(args):
    """Run a single task (one persona or all personas in parallel)."""
    from orchestrator.parallel_runner import run_jobs_parallel

    workers = _validate_workers(args)
    task = _find_and_load_task(args.task)
    condition = CONDITIONS[getattr(args, "condition", "agent")]

    runonly = getattr(args, "runonly", False)
    evalonly = getattr(args, "evalonly", False)

    # Determine personas
    if args.persona:
        persona_ids = [args.persona]
        if not evalonly:
            workers = 1
    else:
        persona_ids = task.persona_ids

    result_base_dir = _build_result_base_dir(args, "run-single")

    # Build jobs
    jobs = []
    for pid in persona_ids:
        persona = _load_persona(pid)
        jobs.append(_build_job_spec(task, persona, args, result_base_dir))

    # Print header
    agent_type = getattr(args, "agent", "generic")
    model_override = getattr(args, "model", None)
    model_display = model_override or get_model_for_agent(agent_type)
    print("=== QuantTutorBench - Single Task ===")
    print(f"Task: {task.task_id}")
    print(f"Persona(s): {', '.join(persona_ids)}")
    if evalonly:
        print("Mode: EVALONLY (evaluate saved results)")
        print(f"Source: {result_base_dir}")
    else:
        print(f"Agent: {agent_type}")
        print(f"Condition: {condition.name} — {condition.description}")
        print(f"Model: {model_display}")
        print(f"Tools: {'enabled' if condition.tools_enabled else 'disabled'}")
        print(f"Max turns: {args.max_turns}")
        if runonly:
            print("Mode: RUNONLY (no evaluation)")
    if len(jobs) > 1:
        print(f"Workers: {workers} (parallel personas)")
    print()

    # Select job executor
    job_fn = None
    if evalonly:
        from orchestrator.job_runner import eval_single_job

        job_fn = eval_single_job

    if len(jobs) == 1:
        # Single persona — run directly, show detailed output
        job_result = run_jobs_parallel(jobs, max_workers=1, job_fn=job_fn)[0]
        if job_result.task_result:
            if runonly:
                tr = job_result.task_result
                print(f"\n--- Conversation ({len(tr.turns)} turns) ---")
                for turn in tr.turns:
                    prefix = "Student" if turn.role == "user" else "Tutor"
                    print(f"\n[{prefix}]: {turn.content[:200]}...")
                print("\n[RUNONLY] Evaluation skipped.")
            else:
                _print_single_result(job_result.task_result)
        elif job_result.error:
            print(f"\nError: {job_result.error}")
        # Print save info
        _should_save = getattr(args, "save_result", False) or runonly or evalonly
        if _should_save and job_result.task_result:
            _result_dir = (
                result_base_dir / task.category.value / task.task_id / persona_ids[0]
            )
            print(f"\nResults saved: {_result_dir}")
    else:
        # Multi-persona — run in parallel, show summary table
        print(f"Running {len(jobs)} jobs...")
        job_results = run_jobs_parallel(
            jobs,
            max_workers=workers,
            progress_callback=_progress,
            job_fn=job_fn,
        )
        _print_group_summary(job_results)
        _should_save = getattr(args, "save_result", False) or runonly or evalonly
        if _should_save:
            print(
                f"\nResults saved: {result_base_dir / task.category.value / task.task_id}"
            )


def cmd_run_group(args):
    """Run all tasks in a category group."""
    from orchestrator.parallel_runner import run_jobs_parallel

    workers = _validate_workers(args)
    tasks = _load_tasks_by_group(args.group)
    persona_ids = [args.persona] if args.persona else None

    result_base_dir = _build_result_base_dir(args, "run-group") / args.group
    jobs = _build_jobs_for_tasks(tasks, persona_ids, args, result_base_dir)

    runonly = getattr(args, "runonly", False)
    evalonly = getattr(args, "evalonly", False)
    print(f"=== QuantTutorBench - Group: {args.group} ===")
    print(
        f"Tasks: {len(tasks)} | "
        f"Personas: {len(persona_ids) if persona_ids else 'all'} each | "
        f"Jobs: {len(jobs)} | Workers: {workers}"
    )
    if evalonly:
        print("Mode: EVALONLY (evaluate saved results)")
    elif runonly:
        print("Mode: RUNONLY (no evaluation)")
    print()

    job_fn = None
    if evalonly:
        from orchestrator.job_runner import eval_single_job

        job_fn = eval_single_job

    job_results = run_jobs_parallel(
        jobs,
        max_workers=workers,
        progress_callback=_progress,
        job_fn=job_fn,
    )
    _print_group_summary(job_results)

    _should_save = getattr(args, "save_result", False) or runonly or evalonly
    if _should_save:
        _save_group_summary(job_results, f"Group: {args.group}", result_base_dir)
        print(f"\nResults saved: {result_base_dir}")


def cmd_run_layer2(args):
    """Run all Layer 2 tasks in parallel."""
    from orchestrator.parallel_runner import run_jobs_parallel

    workers = _validate_workers(args)
    tasks = _load_all_layer2_tasks()
    persona_ids = [args.persona] if args.persona else None

    result_base_dir = _build_result_base_dir(args, "run-layer2")
    jobs = _build_jobs_for_tasks(tasks, persona_ids, args, result_base_dir)

    runonly = getattr(args, "runonly", False)
    evalonly = getattr(args, "evalonly", False)
    print("=== QuantTutorBench - Layer 2 (All Tasks) ===")
    print(
        f"Tasks: {len(tasks)} | "
        f"Personas: {len(persona_ids) if persona_ids else 'all'} each | "
        f"Jobs: {len(jobs)} | Workers: {workers}"
    )
    if evalonly:
        print("Mode: EVALONLY (evaluate saved results)")
    elif runonly:
        print("Mode: RUNONLY (no evaluation)")
    print()

    job_fn = None
    if evalonly:
        from orchestrator.job_runner import eval_single_job

        job_fn = eval_single_job

    job_results = run_jobs_parallel(
        jobs,
        max_workers=workers,
        progress_callback=_progress,
        job_fn=job_fn,
    )
    _print_group_summary(job_results)

    _should_save = getattr(args, "save_result", False) or runonly or evalonly
    if _should_save:
        _save_group_summary(job_results, "Layer 2 (All Tasks)", result_base_dir)
        print(f"\nResults saved: {result_base_dir}")


def cmd_list_tasks(args):
    """List all available tasks."""
    tasks_dir = BENCH_ROOT / "tasks"
    print(f"{'ID':<30} {'Layer':<8} {'Category':<20} {'Difficulty':<10} {'Personas'}")
    print("-" * 95)
    for layer_dir in sorted(tasks_dir.iterdir()):
        if layer_dir.is_dir():
            layer_name = layer_dir.name  # "layer1" or "layer2"
            for category_dir in sorted(layer_dir.iterdir()):
                if category_dir.is_dir():
                    for task_file in sorted(category_dir.glob("*.json")):
                        with open(task_file) as f:
                            task = json.load(f)
                        personas = ", ".join(task.get("persona_ids", []))
                        print(
                            f"{task['task_id']:<30} {layer_name:<8} {task['category']:<20} {task['difficulty']:<10} {personas}"
                        )


def cmd_run_layer1(args):
    """Run Layer 1 single-turn evaluations."""
    from layer1.data_loader import get_layer1_stats, load_layer1_items
    from layer1.runner import Layer1Runner
    from orchestrator.agent_adapters.generic_adapter import GenericLLMAdapter
    from orchestrator.container_manager import ContainerManager

    eval_model_name = args.eval_model or None

    print("=== QuantTutorBench - Layer 1 ===")
    print(f"Agent model: {AGENT_DEFAULT_MODEL} (from config/llm_config.py)")
    print(f"Eval model: {eval_model_name or '(DeepEval default)'}")
    print("Loading items from synthesized data...")

    items = load_layer1_items(max_items=args.max_items)
    stats = get_layer1_stats(items)

    print(f"Loaded {stats['total_items']} items")
    print(f"  By category: {stats['by_category']}")
    print(f"  By difficulty: {stats['by_difficulty']}")
    print(f"  By source: {stats['by_source']}")
    print()

    # Create a real agent to answer questions
    agent = GenericLLMAdapter(
        model=AGENT_DEFAULT_MODEL,
        agent_name=f"generic_{AGENT_DEFAULT_MODEL}",
    )

    callback = _make_layer1_callback(agent)

    # Configure eval model for Layer 1 GEval judge.
    # DeepEval accepts a model string and uses OPENAI_API_KEY + OPENAI_BASE_URL
    # from environment (already bridged from OPENROUTER_API_KEY at top of this file).
    l1_container_manager = ContainerManager(use_docker=False)
    runner = Layer1Runner(
        agent_callback=callback,
        use_deepeval=not args.no_deepeval,
        eval_model=eval_model_name,
        container_manager=l1_container_manager,
        use_docker=False,
    )

    results = runner.run_batch(items, max_items=args.max_items)
    scores = runner.compute_layer1_scores(results)

    print("\n=== Layer 1 Results ===")
    print(f"Items evaluated: {scores['total_items']}")
    print(f"Mean score: {scores['mean_score']:.4f}")
    print(f"By category: {scores['by_category']}")
    print(f"By difficulty: {scores['by_difficulty']}")

    # Save results
    output_path = BENCH_ROOT / "results" / "layer1_results.json"
    runner.save_results(results, output_path)
    print(f"Results saved: {output_path}")


def cmd_test_e2e(args):
    """Run end-to-end validation test."""
    print("=== QuantTutorBench - End-to-End Test ===\n")
    errors = []
    passed = []

    # Test 1: Schema validation
    print("[1/8] Validating task schemas...")
    from orchestrator.schemas import QuantTutorTask, StudentPersona

    tasks_dir = BENCH_ROOT / "tasks"
    task_count = 0
    for layer_dir in sorted(tasks_dir.iterdir()):
        if layer_dir.is_dir():
            for category_dir in sorted(layer_dir.iterdir()):
                if category_dir.is_dir():
                    for task_file in sorted(category_dir.glob("*.json")):
                        task_count += 1
                        try:
                            with open(task_file) as f:
                                QuantTutorTask(**json.load(f))
                        except Exception as e:
                            errors.append(f"Task schema: {task_file.name}: {e}")
    if task_count > 0:
        passed.append(f"Task schemas: {task_count} validated")
    else:
        errors.append("No tasks found")

    # Test 2: Persona validation
    print("[2/8] Validating personas...")
    personas_dir = BENCH_ROOT / "personas"
    persona_count = 0
    for pf in sorted(personas_dir.glob("*.json")):
        persona_count += 1
        try:
            with open(pf) as f:
                StudentPersona(**json.load(f))
        except Exception as e:
            errors.append(f"Persona schema: {pf.name}: {e}")
    if persona_count > 0:
        passed.append(f"Personas: {persona_count} validated")
    else:
        errors.append("No personas found")

    # Test 3: Layer 1 data loading
    print("[3/8] Testing Layer 1 data loading...")
    try:
        from layer1.data_loader import get_layer1_stats, load_layer1_items

        items = load_layer1_items(max_items=5)
        stats = get_layer1_stats(items)
        if stats["total_items"] > 0:
            passed.append(f"Layer 1 data: {stats['total_items']} items loaded")
        else:
            errors.append("Layer 1: No items loaded")
    except Exception as e:
        errors.append(f"Layer 1 data loading: {e}")

    # Test 4: MCP tools
    print("[4/8] Testing MCP tool registry...")
    try:
        from mcp_servers.core.tools import CORE_TOOLS
        from mcp_servers.distractors.distractor_tools import DISTRACTOR_TOOLS

        passed.append(
            f"MCP tools: {len(CORE_TOOLS)} core, {len(DISTRACTOR_TOOLS)} distractors"
        )
    except Exception as e:
        errors.append(f"MCP tools: {e}")

    # Test 5: Scoring pipeline
    print("[5/8] Testing scoring pipeline...")
    try:
        from evaluation.scoring import compute_task_score

        score = compute_task_score(
            quant_result_score=0.8,
            quant_process_score=0.6,
            tutor_dimension_scores={
                "D1": 0.7,
                "D2": 0.8,
                "D3": 0.6,
                "D4": 0.9,
                "D5": 0.7,
                "D6": 0.5,
                "D7": 0.8,
            },
        )
        if 0 < score["overall_score"] <= 1:
            passed.append(f"Scoring: overall={score['overall_score']:.4f}")
        else:
            errors.append(f"Scoring: unexpected score {score['overall_score']}")
    except Exception as e:
        errors.append(f"Scoring: {e}")

    # Test 5b: Combined scoring pipeline (Layer 1 + Layer 2)
    print("[5b/8] Testing combined scoring pipeline...")
    try:
        from evaluation.scoring import compute_combined_benchmark_kpis

        combined = compute_combined_benchmark_kpis(
            layer2_task_results=[score],  # reuse 'score' from test 5
            layer1_mean_score=0.75,
            layer1_summary={
                "mean_score": 0.75,
                "total_items": 100,
                "by_category": {},
                "by_difficulty": {},
            },
            layers_evaluated=["layer1", "layer2"],
        )
        # Result_Sub = 0.40*0.75 + 0.60*0.8 = 0.78
        result_sub = combined.get("combined_result_subscore", 0)
        if 0 < combined["overall_agent_score"] <= 1 and abs(result_sub - 0.78) < 0.01:
            passed.append(
                f"Combined scoring: OAS={combined['overall_agent_score']:.4f}, "
                f"Result_Sub={result_sub:.4f}"
            )
        else:
            errors.append(
                f"Combined scoring: unexpected OAS={combined['overall_agent_score']}, "
                f"Result_Sub={result_sub}"
            )
    except Exception as e:
        errors.append(f"Combined scoring: {e}")

    # Test 6: DeepEval availability
    print("[6/8] Checking DeepEval and LLM connectivity...")
    try:
        import deepeval

        passed.append(f"DeepEval: v{deepeval.__version__} available")
    except ImportError:
        errors.append("DeepEval: not installed")

    # Test 7: LLM connectivity via OpenRouter
    print("[7/8] Testing LLM connectivity via OpenRouter...")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        errors.append("OPENROUTER_API_KEY not found in environment")
    else:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url=OPENROUTER_BASE_URL,
            )
            response = client.chat.completions.create(
                model=AGENT_DEFAULT_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": "What is a moving average? Answer in one sentence.",
                    }
                ],
                max_tokens=100,
            )
            answer = response.choices[0].message.content
            if answer and len(answer) > 10:
                passed.append(f"LLM connectivity: OK (got {len(answer)} chars)")
            else:
                errors.append("LLM connectivity: empty or too short response")
        except Exception as e:
            errors.append(f"LLM connectivity: {e}")

    # Summary
    print("\n=== E2E Test Results ===")
    print(f"Passed: {len(passed)}")
    for p in passed:
        print(f"  [PASS] {p}")
    if errors:
        print(f"Errors: {len(errors)}")
        for e in errors:
            print(f"  [FAIL] {e}")
    else:
        print("All tests passed!")

    return len(errors) == 0


def cmd_validate_tasks(args):
    """Validate all task JSON files against the schema."""
    from orchestrator.schemas import QuantTutorTask

    tasks_dir = BENCH_ROOT / "tasks"
    errors = []
    count = 0

    for layer_dir in sorted(tasks_dir.iterdir()):
        if layer_dir.is_dir():
            for category_dir in sorted(layer_dir.iterdir()):
                if category_dir.is_dir():
                    for task_file in sorted(category_dir.glob("*.json")):
                        count += 1
                        try:
                            with open(task_file) as f:
                                data = json.load(f)
                            QuantTutorTask(**data)
                            print(
                                f"  OK: {layer_dir.name}/{category_dir.name}/{task_file.name}"
                            )
                        except Exception as e:
                            errors.append((task_file.name, str(e)))
                            print(f"  FAIL: {task_file.name}: {e}")

    print(f"\nValidated {count} tasks, {len(errors)} errors.")
    return len(errors) == 0


# ---------------------------------------------------------------------------
# Shared argparse argument definitions
# ---------------------------------------------------------------------------


def _add_common_args(parser):
    """Add arguments shared by run-single, run-group, run-layer2."""
    parser.add_argument(
        "--agent",
        default="generic",
        choices=["generic", "openai", "anthropic", "google"],
        help="Agent SDK / model family",
    )
    parser.add_argument(
        "--condition",
        default="agent",
        choices=CONDITION_NAMES,
        help="Test condition (2x2 matrix cell)",
    )
    parser.add_argument("--docker", action="store_true", help="Use Docker sandbox")
    parser.add_argument("--max-turns", type=int, default=5, help="Max turns")
    parser.add_argument(
        "--eval-model",
        default=None,
        help="LLM model for GEval judge (OpenRouter format)",
    )
    parser.add_argument(
        "--simulator-model",
        default=None,
        help="LLM model for student simulator (OpenRouter format)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override agent model (OpenRouter format, e.g. 'openai/gpt-5.2')",
    )
    parser.add_argument(
        "--save-result",
        action="store_true",
        help="Save scores.md, trace.md, cost.md, and agent_files/",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="Max parallel workers (default: 3, each standard container ~1 CPU + 768MB; LEAN ~2 CPU + 1GB)",
    )
    parser.add_argument(
        "--runonly",
        action="store_true",
        help="Run agent only (no evaluation). Implies --save-result. "
        "Saves trace.md + agent_files/ + cost.md + run_state.json, skips scores.md.",
    )
    parser.add_argument(
        "--evalonly",
        action="store_true",
        help="Evaluate previously saved --runonly results. "
        "Reads run_state.json from result dirs, runs evaluation, "
        "saves scores.md + updated cost.md. No agent/docker needed.",
    )


def main():
    # ── DeepEval retry policy — strengthen defaults for parallel execution ──
    # DeepEval's @retry_openai uses Tenacity. Defaults (max=2, cap=5s) are
    # too aggressive for 10-worker parallel runs hitting OpenRouter rate limits.
    # setdefault() allows users to override via env vars if needed.
    os.environ.setdefault("DEEPEVAL_RETRY_MAX_ATTEMPTS", "6")
    os.environ.setdefault("DEEPEVAL_RETRY_INITIAL_SECONDS", "3")
    os.environ.setdefault("DEEPEVAL_RETRY_CAP_SECONDS", "60")
    os.environ.setdefault("DEEPEVAL_RETRY_JITTER", "5")

    parser = argparse.ArgumentParser(description="QuantTutorBench CLI")
    subparsers = parser.add_subparsers(dest="command")

    # run command
    run_parser = subparsers.add_parser("run", help="Run full benchmark")
    run_parser.add_argument(
        "--agent",
        default="generic",
        choices=["generic", "openai", "anthropic", "google"],
        help="Agent SDK / model family",
    )
    run_parser.add_argument(
        "--condition",
        default="agent",
        choices=CONDITION_NAMES,
        help="Test condition (2x2 matrix cell)",
    )
    run_parser.add_argument("--docker", action="store_true", help="Use Docker sandbox")
    run_parser.add_argument(
        "--tasks", default=None, help="Comma-separated task IDs to run"
    )
    run_parser.add_argument(
        "--personas", default=None, help="Comma-separated persona IDs"
    )
    run_parser.add_argument(
        "--trials", type=int, default=1, help="Number of trials per task"
    )
    run_parser.add_argument(
        "--max-turns", type=int, default=5, help="Max conversation turns"
    )
    run_parser.add_argument(
        "--eval-model",
        default=None,
        help="LLM model for GEval judge (OpenRouter format)",
    )
    run_parser.add_argument(
        "--simulator-model",
        default=None,
        help="LLM model for student simulator (OpenRouter format)",
    )
    run_parser.add_argument(
        "--layer",
        default="all",
        choices=["all", "1", "2"],
        help="Which layer(s) to evaluate: all (default), 1 (single-turn), 2 (multi-turn)",
    )
    run_parser.add_argument(
        "--l1-max-items",
        type=int,
        default=None,
        help="Max Layer 1 items to evaluate (default: all)",
    )
    run_parser.add_argument(
        "--no-deepeval",
        action="store_true",
        help="Disable DeepEval for Layer 1 (use simple scoring)",
    )
    run_parser.add_argument(
        "--model",
        default=None,
        help="Override agent model (OpenRouter format, e.g. 'openai/gpt-5.2')",
    )
    run_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Max parallel workers for Layer 2 (default: 1 = sequential)",
    )
    run_parser.add_argument(
        "--save-result",
        action="store_true",
        help="Save per-job results (only used with --workers > 1)",
    )

    # run-single command
    single_parser = subparsers.add_parser("run-single", help="Run single task")
    single_parser.add_argument("--task", required=True, help="Task ID")
    single_parser.add_argument(
        "--persona",
        default=None,
        help="Persona ID (omit to run all personas in parallel)",
    )
    _add_common_args(single_parser)

    # run-group command
    group_parser = subparsers.add_parser(
        "run-group", help="Run all tasks in a category group"
    )
    group_parser.add_argument(
        "--group",
        required=True,
        choices=_LAYER2_GROUPS,
        help="Task category/group to run",
    )
    group_parser.add_argument(
        "--persona",
        default=None,
        help="Single persona (omit for all)",
    )
    _add_common_args(group_parser)

    # run-layer2 command
    layer2_parser = subparsers.add_parser("run-layer2", help="Run all Layer 2 tasks")
    layer2_parser.add_argument(
        "--persona",
        default=None,
        help="Single persona (omit for all)",
    )
    _add_common_args(layer2_parser)

    # run-layer1 command
    layer1_parser = subparsers.add_parser(
        "run-layer1", help="Run Layer 1 single-turn evaluation"
    )
    layer1_parser.add_argument(
        "--max-items", type=int, default=None, help="Max items to evaluate"
    )
    layer1_parser.add_argument(
        "--no-deepeval",
        action="store_true",
        help="Disable DeepEval (use simple scoring)",
    )
    layer1_parser.add_argument(
        "--eval-model", default=None, help="LLM model for evaluation judge"
    )

    # list-tasks command
    subparsers.add_parser("list-tasks", help="List all tasks")

    # validate-tasks command
    subparsers.add_parser("validate-tasks", help="Validate task JSONs")

    # test-e2e command
    subparsers.add_parser("test-e2e", help="Run end-to-end validation test")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "run-single":
        cmd_run_single(args)
    elif args.command == "run-group":
        cmd_run_group(args)
    elif args.command == "run-layer2":
        cmd_run_layer2(args)
    elif args.command == "run-layer1":
        cmd_run_layer1(args)
    elif args.command == "list-tasks":
        cmd_list_tasks(args)
    elif args.command == "validate-tasks":
        cmd_validate_tasks(args)
    elif args.command == "test-e2e":
        cmd_test_e2e(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
