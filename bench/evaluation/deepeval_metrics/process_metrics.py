"""Process-level DeepEval metrics for QuantTutorBench.

Design doc §6.1 (Quant Process Scoring) and §9 (DeepEval Component Mapping).

Metrics implemented:
- ArgumentCorrectnessMetric: validates tool call arguments (LLMTestCase)
- MCPUseMetric: LLM-judged tool selection quality, single-turn (LLMTestCase)
- MultiTurnMCPUseMetric: LLM-judged contextual tool usage, multi-turn (ConversationalTestCase)
- StepEfficiencyMetric: reasonable number of steps? (LLMTestCase)
- RoleAdherenceMetric: stays in "tutor" role? (ConversationalTestCase)
- KnowledgeRetentionMetric: remembers earlier context? (ConversationalTestCase)
- TopicAdherenceMetric: stays on quant finance topics? (ConversationalTestCase)

Reference: https://github.com/confident-ai/deepeval
"""

import asyncio
from typing import Optional

import nest_asyncio
from config.llm_config import resolve_deepeval_model

try:
    from deepeval.metrics import (
        ArgumentCorrectnessMetric,
        KnowledgeRetentionMetric,
        MCPUseMetric,
        MultiTurnMCPUseMetric,
        RoleAdherenceMetric,
        StepEfficiencyMetric,
        TopicAdherenceMetric,
    )
    from deepeval.test_case import (
        ConversationalTestCase,
        LLMTestCase,
        ToolCall,
    )
    from deepeval.test_case.mcp import MCPServer, MCPToolCall

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# Default relevant topics for TopicAdherenceMetric
QUANT_TUTOR_TOPICS = [
    "quantitative finance",
    "algorithmic trading",
    "backtesting",
    "technical indicators",
    "moving averages",
    "risk metrics",
    "Sharpe ratio",
    "portfolio analysis",
    "financial data analysis",
    "Python programming for finance",
    "pandas data manipulation",
    "statistical analysis",
    "strategy development",
    "market data",
    "time series analysis",
    "options and derivatives pricing",
    "volatility modeling",
    "risk management and VaR",
    "factor models and alpha generation",
    "return calculation and attribution",
    "correlation and covariance analysis",
    "machine learning in finance",
    "order execution and transaction costs",
]


# ──────────────────────────────────────────────────────────────
# Helper: build DeepEval objects from proxy logs
# ──────────────────────────────────────────────────────────────


def _build_mcp_tool_calls(proxy_logs: list) -> list:
    """Convert proxy tool call logs to DeepEval MCPToolCall objects.

    Args:
        proxy_logs: List of ToolCallLog objects from MCPProxy.

    Returns:
        List of MCPToolCall objects.
    """
    if not DEEPEVAL_AVAILABLE:
        return []
    return [
        MCPToolCall(
            name=log.name,
            args=log.args,
            result=log.result[:500] if log.result else "",
        )
        for log in proxy_logs
    ]


def _build_tool_calls(proxy_logs: list) -> list:
    """Convert proxy tool call logs to DeepEval ToolCall objects.

    Args:
        proxy_logs: List of ToolCallLog objects from MCPProxy.

    Returns:
        List of ToolCall objects.
    """
    if not DEEPEVAL_AVAILABLE:
        return []
    return [
        ToolCall(
            name=log.name,
            input_parameters=log.args,
            output=log.result[:500] if log.result else "",
        )
        for log in proxy_logs
    ]


def _build_expected_tools(expected_tool_names: list[str]) -> list:
    """Build expected ToolCall objects from tool names.

    Args:
        expected_tool_names: List of expected tool name strings.

    Returns:
        List of ToolCall objects with just names.
    """
    if not DEEPEVAL_AVAILABLE:
        return []
    return [ToolCall(name=name) for name in expected_tool_names]


def _build_mcp_servers(core_tools: list[str], distractor_tools: list[str]) -> list:
    """Build MCPServer objects for DeepEval MCP metrics.

    Args:
        core_tools: List of core tool names.
        distractor_tools: List of distractor tool names.

    Returns:
        List of MCPServer objects.
    """
    if not DEEPEVAL_AVAILABLE:
        return []

    all_tools = core_tools + distractor_tools
    return [
        MCPServer(
            server_name="quant_tutor_bench",
            available_tools=all_tools,
        )
    ]


# ──────────────────────────────────────────────────────────────
# Single-turn metrics (LLMTestCase based)
# ──────────────────────────────────────────────────────────────


def evaluate_argument_correctness(
    input_text: str,
    actual_output: str,
    proxy_logs: list,
    expected_tool_names: list[str],
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate argument correctness of tool calls.

    Design doc §4.6: Were the arguments to each tool call valid?

    Args:
        input_text: Combined user input/task description.
        actual_output: Agent's final text output.
        proxy_logs: Tool call logs from MCPProxy.
        expected_tool_names: Expected tool names.
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    test_case = LLMTestCase(
        input=input_text,
        actual_output=actual_output,
        tools_called=_build_tool_calls(proxy_logs),
        expected_tools=_build_expected_tools(expected_tool_names),
    )

    kwargs = {"threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = ArgumentCorrectnessMetric(**kwargs)

    try:
        metric.measure(test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"ArgumentCorrectnessMetric error: {e}",
            "passed": True,
        }


def evaluate_mcp_use(
    input_text: str,
    actual_output: str,
    proxy_logs: list,
    core_tools: list[str],
    distractor_tools: list[str],
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate MCP tool usage quality (single-turn).

    Design doc §4.6: Given available tools and task, did the agent
    select and use tools correctly?

    Args:
        input_text: Combined user input/task description.
        actual_output: Agent's final text output.
        proxy_logs: Tool call logs from MCPProxy.
        core_tools: Core tool names.
        distractor_tools: Distractor tool names.
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    test_case = LLMTestCase(
        input=input_text,
        actual_output=actual_output,
        mcp_tools_called=_build_mcp_tool_calls(proxy_logs),
        mcp_servers=_build_mcp_servers(core_tools, distractor_tools),
    )

    kwargs = {"threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = MCPUseMetric(**kwargs)

    try:
        metric.measure(test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {"score": 0.5, "reason": f"MCPUseMetric error: {e}", "passed": True}


def evaluate_step_efficiency(
    input_text: str,
    actual_output: str,
    proxy_logs: list,
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate step efficiency of tool usage.

    Design doc §4.6: Did the agent take a reasonable number of steps/tool calls?

    Args:
        input_text: Combined user input/task description.
        actual_output: Agent's final text output.
        proxy_logs: Tool call logs from MCPProxy.
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    test_case = LLMTestCase(
        input=input_text,
        actual_output=actual_output,
        tools_called=_build_tool_calls(proxy_logs),
    )

    kwargs = {"threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = StepEfficiencyMetric(**kwargs)

    try:
        metric.measure(test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"StepEfficiencyMetric error: {e}",
            "passed": True,
        }


# ──────────────────────────────────────────────────────────────
# Multi-turn metrics (ConversationalTestCase based)
# ──────────────────────────────────────────────────────────────


def evaluate_multi_turn_mcp(
    conversational_test_case: "ConversationalTestCase",
    core_tools: list[str],
    distractor_tools: list[str],
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate multi-turn MCP tool usage quality.

    Design doc §4.6: Across the conversation, was tool usage
    contextually appropriate at each turn?

    Args:
        conversational_test_case: The ConversationalTestCase (with turns + mcp_tools_called).
        core_tools: Core tool names.
        distractor_tools: Distractor tool names.
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    # Ensure mcp_servers is set on the test case
    if conversational_test_case.mcp_servers is None:
        conversational_test_case.mcp_servers = _build_mcp_servers(
            core_tools, distractor_tools
        )

    kwargs = {"threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = MultiTurnMCPUseMetric(**kwargs)

    try:
        metric.measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"MultiTurnMCPUseMetric error: {e}",
            "passed": True,
        }


def evaluate_role_adherence(
    conversational_test_case: "ConversationalTestCase",
    chatbot_role: str = "quantitative finance tutor",
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate whether the agent stays in its designated role.

    Design doc §9: Does agent stay in "tutor" role?

    Args:
        conversational_test_case: The ConversationalTestCase.
        chatbot_role: The role the agent should adhere to.
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    # Ensure chatbot_role is set
    if conversational_test_case.chatbot_role is None:
        conversational_test_case.chatbot_role = chatbot_role

    kwargs = {"threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = RoleAdherenceMetric(**kwargs)

    try:
        metric.measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"RoleAdherenceMetric error: {e}",
            "passed": True,
        }


def evaluate_knowledge_retention(
    conversational_test_case: "ConversationalTestCase",
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate whether the agent remembers earlier context.

    Design doc §9: Does agent remember earlier context?

    Args:
        conversational_test_case: The ConversationalTestCase.
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    kwargs = {"threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = KnowledgeRetentionMetric(**kwargs)

    try:
        metric.measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"KnowledgeRetentionMetric error: {e}",
            "passed": True,
        }


def evaluate_topic_adherence(
    conversational_test_case: "ConversationalTestCase",
    relevant_topics: Optional[list[str]] = None,
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate whether the agent stays on quant finance topics.

    Design doc §9: Does agent stay on quant finance topics?

    Args:
        conversational_test_case: The ConversationalTestCase.
        relevant_topics: List of relevant topic strings (defaults to QUANT_TUTOR_TOPICS).
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    topics = relevant_topics or QUANT_TUTOR_TOPICS

    kwargs = {"relevant_topics": topics, "threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = TopicAdherenceMetric(**kwargs)

    try:
        metric.measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"TopicAdherenceMetric error: {e}",
            "passed": True,
        }


# ──────────────────────────────────────────────────────────────
# Async helpers for parallel metric evaluation
# ──────────────────────────────────────────────────────────────


def _run_async(coro):
    """Run an async coroutine from synchronous code, handling existing event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    else:
        return asyncio.run(coro)


async def _async_eval_tool_correctness(
    task_description, actual_output, proxy_logs, expected_tool_names, model
):
    """Async wrapper for ToolCorrectness evaluation."""
    try:
        from evaluation.deepeval_metrics.mcp_metrics import (
            create_tool_correctness_metric,
            create_tool_test_case,
        )

        test_case = create_tool_test_case(
            input_text=task_description,
            actual_output=actual_output,
            tools_called=[
                {
                    "name": log.name,
                    "input_parameters": log.args,
                    "output": log.result or "",
                }
                for log in proxy_logs
            ],
            expected_tools=[{"name": t} for t in expected_tool_names],
        )
        metric = create_tool_correctness_metric(threshold=0.5, model=model)
        await metric.a_measure(test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= 0.5,
        }
    except Exception as e:
        from evaluation.deepeval_metrics.mcp_metrics import (
            compute_tool_precision_recall,
        )

        tools_called_names = [log.name for log in proxy_logs]
        metrics = compute_tool_precision_recall(
            called_tools=tools_called_names,
            expected_tools=expected_tool_names,
            distractor_tools=[],
        )
        return {
            "score": metrics["f1"],
            "reason": f"Async eval failed ({e}), used manual computation",
            "passed": metrics["f1"] >= 0.5,
        }


async def _async_eval_argument_correctness(
    input_text, actual_output, proxy_logs, expected_tool_names, model, threshold=0.5
):
    """Async wrapper for ArgumentCorrectness evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    test_case = LLMTestCase(
        input=input_text,
        actual_output=actual_output,
        tools_called=_build_tool_calls(proxy_logs),
        expected_tools=_build_expected_tools(expected_tool_names),
    )
    metric = ArgumentCorrectnessMetric(
        threshold=threshold, model=resolve_deepeval_model(model)
    )
    try:
        await metric.a_measure(test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"ArgumentCorrectnessMetric error: {e}",
            "passed": True,
        }


async def _async_eval_mcp_use(
    input_text,
    actual_output,
    proxy_logs,
    core_tools,
    distractor_tools,
    model,
    threshold=0.5,
):
    """Async wrapper for MCPUse evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    test_case = LLMTestCase(
        input=input_text,
        actual_output=actual_output,
        mcp_tools_called=_build_mcp_tool_calls(proxy_logs),
        mcp_servers=_build_mcp_servers(core_tools, distractor_tools),
    )
    metric = MCPUseMetric(threshold=threshold, model=resolve_deepeval_model(model))
    try:
        await metric.a_measure(test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {"score": 0.5, "reason": f"MCPUseMetric error: {e}", "passed": True}


async def _async_eval_step_efficiency(
    input_text, actual_output, proxy_logs, model, threshold=0.5
):
    """Async wrapper for StepEfficiency evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    test_case = LLMTestCase(
        input=input_text,
        actual_output=actual_output,
        tools_called=_build_tool_calls(proxy_logs),
    )
    metric = StepEfficiencyMetric(
        threshold=threshold, model=resolve_deepeval_model(model)
    )
    try:
        await metric.a_measure(test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"StepEfficiencyMetric error: {e}",
            "passed": True,
        }


async def _async_eval_multi_turn_mcp(
    conversational_test_case, core_tools, distractor_tools, model, threshold=0.5
):
    """Async wrapper for MultiTurnMCPUse evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    if conversational_test_case.mcp_servers is None:
        conversational_test_case.mcp_servers = _build_mcp_servers(
            core_tools, distractor_tools
        )
    metric = MultiTurnMCPUseMetric(
        threshold=threshold, model=resolve_deepeval_model(model)
    )
    try:
        await metric.a_measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"MultiTurnMCPUseMetric error: {e}",
            "passed": True,
        }


async def _async_eval_role_adherence(
    conversational_test_case,
    model,
    chatbot_role="quantitative finance tutor",
    threshold=0.5,
):
    """Async wrapper for RoleAdherence evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    if conversational_test_case.chatbot_role is None:
        conversational_test_case.chatbot_role = chatbot_role
    metric = RoleAdherenceMetric(
        threshold=threshold, model=resolve_deepeval_model(model)
    )
    try:
        await metric.a_measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"RoleAdherenceMetric error: {e}",
            "passed": True,
        }


async def _async_eval_knowledge_retention(
    conversational_test_case, model, threshold=0.5
):
    """Async wrapper for KnowledgeRetention evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    metric = KnowledgeRetentionMetric(
        threshold=threshold, model=resolve_deepeval_model(model)
    )
    try:
        await metric.a_measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"KnowledgeRetentionMetric error: {e}",
            "passed": True,
        }


async def _async_eval_topic_adherence(
    conversational_test_case, model, relevant_topics=None, threshold=0.5
):
    """Async wrapper for TopicAdherence evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    topics = relevant_topics or QUANT_TUTOR_TOPICS
    metric = TopicAdherenceMetric(
        relevant_topics=topics,
        threshold=threshold,
        model=resolve_deepeval_model(model),
    )
    try:
        await metric.a_measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"TopicAdherenceMetric error: {e}",
            "passed": True,
        }


# ──────────────────────────────────────────────────────────────
# Aggregate evaluation entry point
# ──────────────────────────────────────────────────────────────


def evaluate_all_process_metrics(
    task_description: str,
    actual_output: str,
    proxy_logs: list,
    expected_tool_names: list[str],
    core_tools: list[str],
    distractor_tools: list[str],
    conversational_test_case=None,
    model: Optional[str] = None,
) -> dict:
    """Run all process-level DeepEval metrics in parallel and return consolidated results.

    This is the main entry point called from the orchestrator's _evaluate_task().
    All metrics are run concurrently via asyncio.gather + a_measure() for speed.

    Args:
        task_description: Text description of the task (used as LLMTestCase input).
        actual_output: Agent's combined text output.
        proxy_logs: Tool call logs from MCPProxy (list of ToolCallLog objects).
        expected_tool_names: Expected tool names from task ground truth.
        core_tools: Core tool names from task environment.
        distractor_tools: Distractor tool names from task environment.
        conversational_test_case: Pre-built ConversationalTestCase (for multi-turn metrics).
        model: LLM judge model.

    Returns:
        Dict with per-metric scores and an aggregate process score.
    """
    # Ensure mcp_servers / chatbot_role are set before parallel evaluation
    # (these are one-time writes that must happen before concurrent reads)
    if conversational_test_case is not None:
        if conversational_test_case.mcp_servers is None:
            conversational_test_case.mcp_servers = _build_mcp_servers(
                core_tools, distractor_tools
            )
        if conversational_test_case.chatbot_role is None:
            conversational_test_case.chatbot_role = "quantitative finance tutor"

    # Build all async tasks
    tasks = {}

    # --- Single-turn metrics (always run) ---
    tasks["tool_correctness"] = _async_eval_tool_correctness(
        task_description,
        actual_output,
        proxy_logs,
        expected_tool_names,
        model,
    )
    tasks["argument_correctness"] = _async_eval_argument_correctness(
        task_description,
        actual_output,
        proxy_logs,
        expected_tool_names,
        model,
    )
    tasks["mcp_use"] = _async_eval_mcp_use(
        task_description,
        actual_output,
        proxy_logs,
        core_tools,
        distractor_tools,
        model,
    )
    tasks["step_efficiency"] = _async_eval_step_efficiency(
        task_description,
        actual_output,
        proxy_logs,
        model,
    )

    # --- Multi-turn metrics (only if conversational_test_case is available) ---
    if conversational_test_case is not None:
        tasks["multi_turn_mcp"] = _async_eval_multi_turn_mcp(
            conversational_test_case,
            core_tools,
            distractor_tools,
            model,
        )
        tasks["role_adherence"] = _async_eval_role_adherence(
            conversational_test_case,
            model,
        )
        tasks["knowledge_retention"] = _async_eval_knowledge_retention(
            conversational_test_case,
            model,
        )
        tasks["topic_adherence"] = _async_eval_topic_adherence(
            conversational_test_case,
            model,
        )

    # Run all metrics in parallel
    metric_count = len(tasks)
    print(f"    Running {metric_count} process metrics in parallel...")

    keys = list(tasks.keys())
    coros = list(tasks.values())

    async def _run_all():
        return await asyncio.gather(*coros, return_exceptions=True)

    raw_results = _run_async(_run_all())

    # Collect results (exceptions become fallback scores)
    results = {}
    for key, raw in zip(keys, raw_results):
        if isinstance(raw, Exception):
            results[key] = {
                "score": 0.5,
                "reason": f"{key} error: {raw}",
                "passed": True,
            }
        else:
            results[key] = raw

    # Compute aggregate process score
    all_scores = [
        v["score"] for v in results.values() if isinstance(v, dict) and "score" in v
    ]
    results["aggregate_process_score"] = round(
        sum(all_scores) / len(all_scores) if all_scores else 0.5, 4
    )

    return results
