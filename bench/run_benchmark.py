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


def cmd_run(args):
    """Run the full benchmark (Layer 1 + Layer 2, or individually via --layer)."""
    from evaluation.scoring import compute_combined_benchmark_kpis, compute_task_score
    from orchestrator.orchestrator import BenchmarkOrchestrator
    from orchestrator.schemas import BenchmarkReport

    condition = CONDITIONS[getattr(args, "condition", "agent")]
    agent = _create_agent(args)
    layer = getattr(args, "layer", "all")

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
        l2_report = orchestrator.run_benchmark(
            agent=agent,
            task_filter=task_filter,
            persona_filter=persona_filter,
            num_trials=args.trials,
            max_turns_override=args.max_turns,
            tools_enabled=condition.tools_enabled,
        )
        # Merge Layer 2 results into unified report
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
    """Run a single task for debugging."""
    from orchestrator.orchestrator import BenchmarkOrchestrator

    condition = CONDITIONS[getattr(args, "condition", "agent")]
    agent = _create_agent(args)

    orchestrator = BenchmarkOrchestrator(
        bench_root=str(BENCH_ROOT),
        use_docker=args.docker,
        eval_model=getattr(args, "eval_model", None),
        simulator_model=getattr(args, "simulator_model", None),
    )

    # Find the task file
    task_file = None
    for category_dir in (BENCH_ROOT / "tasks" / "layer2").iterdir():
        if category_dir.is_dir():
            for f in category_dir.glob("*.json"):
                if args.task in f.stem:
                    task_file = f
                    break

    if not task_file:
        print(f"Task not found: {args.task}")
        sys.exit(1)

    task = orchestrator.load_task(str(task_file))
    persona = orchestrator.load_persona(args.persona)

    print("=== QuantTutorBench - Single Task ===")
    print(f"Task: {task.task_id}")
    print(f"Persona: {persona.persona_id}")
    print(f"Agent: {agent.agent_name}")
    print(f"Condition: {condition.name} — {condition.description}")
    print(f"Model: {agent.model}")
    print(f"Tools: {'enabled' if condition.tools_enabled else 'disabled'}")
    print(f"Max turns: {args.max_turns}")
    print()

    # Prepare result capture hook if --save-result requested
    save_result = getattr(args, "save_result", False)
    trace_captured: dict = {}
    result_dir = None

    if save_result:
        import shutil

        model_short = agent.model.split("/")[-1] if "/" in agent.model else agent.model
        result_dir = (
            BENCH_ROOT
            / "results"
            / "run-single"
            / getattr(args, "agent", "generic")
            / model_short
            / task.task_id
            / persona.persona_id
        )
        result_dir.mkdir(parents=True, exist_ok=True)
        agent_files_dir = result_dir / "agent_files"

        def _capture_for_trace(*, result, proxy, workspace_path):
            trace_captured["proxy_logs"] = list(proxy.get_logs())
            if workspace_path and os.path.isdir(workspace_path):
                if agent_files_dir.exists():
                    shutil.rmtree(agent_files_dir)
                shutil.copytree(workspace_path, str(agent_files_dir))

    result = orchestrator.run_single_task(
        task=task,
        persona=persona,
        agent=agent,
        max_turns=args.max_turns or 5,
        tools_enabled=condition.tools_enabled,
        pre_teardown_hook=_capture_for_trace if save_result else None,
    )

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
                f"  Numerical accuracy:  {sub.get('numerical_accuracy', '?')}"
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

    # ── Save results (scores.md + trace.md + cost.md + agent_files/) ──
    if save_result and result_dir is not None:
        saved_parts = []

        # scores.md: skip when evaluation was aborted (incomplete/misleading)
        if not result.eval_aborted:
            from evaluation.score_report import generate_score_report

            scores_md = generate_score_report(result)
            (result_dir / "scores.md").write_text(scores_md, encoding="utf-8")
            saved_parts.append("scores.md")
        else:
            # Write a marker file so the user knows this run was aborted
            (result_dir / "ABORTED.md").write_text(
                f"# Evaluation Aborted\n\n{result.error}\n",
                encoding="utf-8",
            )
            saved_parts.append("ABORTED.md")

        # trace.md: always save (valuable for debugging aborted runs)
        if "proxy_logs" in trace_captured:
            from evaluation.trace_report import generate_trace_md

            trace_md = generate_trace_md(
                result,
                trace_captured["proxy_logs"],
                agent_name=getattr(args, "agent", "generic"),
                model=agent.model,
                condition=condition.name,
            )
            (result_dir / "trace.md").write_text(trace_md, encoding="utf-8")
            saved_parts.append("trace.md")

        # cost.md: always save (shows partial spend even for aborted runs)
        from evaluation.cost_report import generate_cost_report

        cost_md = generate_cost_report(result)
        (result_dir / "cost.md").write_text(cost_md, encoding="utf-8")
        saved_parts.append("cost.md")

        n_files = (
            len(list(agent_files_dir.iterdir())) if agent_files_dir.exists() else 0
        )
        saved_parts.append(f"agent_files/ ({n_files} files)")
        print(f"\nResults saved: {result_dir}")
        print(f"  {' + '.join(saved_parts)}")


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

    # run-single command
    single_parser = subparsers.add_parser("run-single", help="Run single task")
    single_parser.add_argument("--task", required=True, help="Task ID")
    single_parser.add_argument(
        "--persona", default="beginner_no_finance", help="Persona ID"
    )
    single_parser.add_argument(
        "--agent",
        default="generic",
        choices=["generic", "openai", "anthropic", "google"],
        help="Agent SDK / model family",
    )
    single_parser.add_argument(
        "--condition",
        default="agent",
        choices=CONDITION_NAMES,
        help="Test condition (2x2 matrix cell)",
    )
    single_parser.add_argument(
        "--docker", action="store_true", help="Use Docker sandbox"
    )
    single_parser.add_argument("--max-turns", type=int, default=5, help="Max turns")
    single_parser.add_argument(
        "--eval-model",
        default=None,
        help="LLM model for GEval judge (OpenRouter format)",
    )
    single_parser.add_argument(
        "--simulator-model",
        default=None,
        help="LLM model for student simulator (OpenRouter format)",
    )
    single_parser.add_argument(
        "--model",
        default=None,
        help="Override agent model (OpenRouter format, e.g. 'openai/gpt-5.2')",
    )
    single_parser.add_argument(
        "--save-result",
        action="store_true",
        help="Save scores.md, trace.md, and agent_files/ to results/run-single/…",
    )

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
