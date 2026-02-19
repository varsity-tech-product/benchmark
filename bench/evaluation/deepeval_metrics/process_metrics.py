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

from typing import Optional

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
    """Run all process-level DeepEval metrics and return consolidated results.

    This is the main entry point called from the orchestrator's _evaluate_task().

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
    results = {}

    # --- Single-turn metrics (always run) ---

    # ToolCorrectnessMetric (§4.6, §6.1.2): Precision/Recall against expected_mcp_tools
    print("    Evaluating ToolCorrectness...")
    try:
        from evaluation.deepeval_metrics.mcp_metrics import evaluate_tool_correctness

        tc_result = evaluate_tool_correctness(
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
            model=model,
        )
        results["tool_correctness"] = tc_result
    except Exception as e:
        results["tool_correctness"] = {
            "score": 0.5,
            "reason": f"ToolCorrectnessMetric error: {e}",
            "passed": True,
        }

    print("    Evaluating ArgumentCorrectness...")
    results["argument_correctness"] = evaluate_argument_correctness(
        input_text=task_description,
        actual_output=actual_output,
        proxy_logs=proxy_logs,
        expected_tool_names=expected_tool_names,
        model=model,
    )

    print("    Evaluating MCPUse...")
    results["mcp_use"] = evaluate_mcp_use(
        input_text=task_description,
        actual_output=actual_output,
        proxy_logs=proxy_logs,
        core_tools=core_tools,
        distractor_tools=distractor_tools,
        model=model,
    )

    print("    Evaluating StepEfficiency...")
    results["step_efficiency"] = evaluate_step_efficiency(
        input_text=task_description,
        actual_output=actual_output,
        proxy_logs=proxy_logs,
        model=model,
    )

    # --- Multi-turn metrics (only if conversational_test_case is available) ---
    if conversational_test_case is not None:
        print("    Evaluating MultiTurnMCPUse...")
        results["multi_turn_mcp"] = evaluate_multi_turn_mcp(
            conversational_test_case=conversational_test_case,
            core_tools=core_tools,
            distractor_tools=distractor_tools,
            model=model,
        )

        print("    Evaluating RoleAdherence...")
        results["role_adherence"] = evaluate_role_adherence(
            conversational_test_case=conversational_test_case,
            model=model,
        )

        print("    Evaluating KnowledgeRetention...")
        results["knowledge_retention"] = evaluate_knowledge_retention(
            conversational_test_case=conversational_test_case,
            model=model,
        )

        print("    Evaluating TopicAdherence...")
        results["topic_adherence"] = evaluate_topic_adherence(
            conversational_test_case=conversational_test_case,
            model=model,
        )

    # Compute aggregate process score
    all_scores = [
        v["score"] for v in results.values() if isinstance(v, dict) and "score" in v
    ]
    results["aggregate_process_score"] = round(
        sum(all_scores) / len(all_scores) if all_scores else 0.5, 4
    )

    return results
