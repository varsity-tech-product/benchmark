"""Trace Assembler for QuantTutorBench.

Combines MCP proxy logs and conversation dialogue into DeepEval test cases.
Assembles ConversationalTestCase (for multi-turn) and LLMTestCase (for single-turn)
with proper MCPToolCall attachments per the DeepEval API.

Reference: https://github.com/confident-ai/deepeval
"""

from typing import Optional

try:
    from deepeval.test_case import (
        ConversationalTestCase,
        LLMTestCase,
        ToolCall,
        Turn,
    )
    from deepeval.test_case.mcp import MCPServer, MCPToolCall

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.schemas import (
    MCPToolCallRecord,
    QuantTutorTask,
    StudentPersona,
    TaskResult,
)


def assemble_conversational_test_case(
    task_result: TaskResult,
    task: QuantTutorTask,
    persona: StudentPersona,
    user_description: Optional[str] = None,
    scenario: Optional[str] = None,
) -> "ConversationalTestCase":
    """Assemble a DeepEval ConversationalTestCase from a completed task result.

    Maps conversation turns + tool call logs into DeepEval's Turn objects
    with both ToolCall and MCPToolCall attachments.

    Args:
        task_result: The completed task result with conversation and tool logs.
        task: The original task definition.
        persona: The student persona used.
        user_description: Full user description (from build_user_description).
        scenario: Full scenario string (from build_scenario).

    Returns:
        ConversationalTestCase for DeepEval metric evaluation.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    turns = []
    for conv_turn in task_result.turns:
        if conv_turn.role == "user":
            turns.append(Turn(role="user", content=conv_turn.content))
        elif conv_turn.role == "assistant":
            # Attach both ToolCall and MCPToolCall to assistant turns
            tool_calls = None
            mcp_tool_calls = None
            if conv_turn.tool_calls:
                tool_calls = [
                    ToolCall(
                        name=tc.name,
                        input_parameters=tc.args,
                        output=tc.result,
                    )
                    for tc in conv_turn.tool_calls
                ]
                mcp_tool_calls = [
                    MCPToolCall(
                        name=tc.name,
                        args=tc.args,
                        result=tc.result or "",
                    )
                    for tc in conv_turn.tool_calls
                ]

            turns.append(
                Turn(
                    role="assistant",
                    content=conv_turn.content,
                    tools_called=tool_calls,
                    mcp_tools_called=mcp_tool_calls,
                )
            )

    opening = task.student_openings.get(persona.persona_id, "")

    # Build MCP servers for MCP metrics
    all_tools = (
        task.environment.core_mcp_tools + task.environment.distractor_mcp_tools_pool
    )
    mcp_servers = [
        MCPServer(server_name="quant_tutor_bench", available_tools=all_tools)
    ]

    return ConversationalTestCase(
        turns=turns,
        scenario=scenario or f"{task.description}\nStudent opening: {opening}",
        expected_outcome=task.ground_truth.expected_outcome,
        user_description=user_description or persona.description,
        chatbot_role="quantitative finance tutor",
        name=f"{task.task_id}_{persona.persona_id}",
        mcp_servers=mcp_servers,
    )


def assemble_single_turn_test_case(
    question: str,
    actual_output: str,
    expected_output: str,
    context: Optional[list[str]] = None,
    tool_calls: Optional[list[MCPToolCallRecord]] = None,
    expected_tools: Optional[list[str]] = None,
    name: Optional[str] = None,
) -> "LLMTestCase":
    """Assemble a DeepEval LLMTestCase for single-turn (Layer 1) evaluation.

    Args:
        question: The input question.
        actual_output: The agent's response.
        expected_output: The reference answer.
        context: Optional context passages.
        tool_calls: Optional tool calls made during this interaction.
        expected_tools: Optional list of expected tool names.
        name: Optional test case name.

    Returns:
        LLMTestCase for DeepEval metric evaluation.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    kwargs = {
        "input": question,
        "actual_output": actual_output,
        "expected_output": expected_output,
    }

    if context:
        kwargs["context"] = context

    if tool_calls:
        kwargs["tools_called"] = [
            ToolCall(
                name=tc.name,
                input_parameters=tc.args,
                output=tc.result,
            )
            for tc in tool_calls
        ]

    if expected_tools:
        kwargs["expected_tools"] = [ToolCall(name=t) for t in expected_tools]

    if name:
        kwargs["name"] = name

    return LLMTestCase(**kwargs)


def extract_tool_call_summary(task_result: TaskResult) -> dict:
    """Extract a summary of tool usage from a task result.

    Returns:
        Dict with tool call statistics useful for evaluation.
    """
    all_calls = task_result.tool_call_log
    unique_tools = set(tc.name for tc in all_calls)
    successful_calls = [tc for tc in all_calls if tc.success]
    failed_calls = [tc for tc in all_calls if not tc.success]

    # Group by turn
    by_turn = {}
    for tc in all_calls:
        turn = tc.turn_index or 0
        by_turn.setdefault(turn, []).append(tc.name)

    return {
        "total_calls": len(all_calls),
        "unique_tools": list(unique_tools),
        "unique_tool_count": len(unique_tools),
        "successful_calls": len(successful_calls),
        "failed_calls": len(failed_calls),
        "calls_per_turn": {k: len(v) for k, v in by_turn.items()},
        "tool_sequence": [tc.name for tc in all_calls],
    }


def task_result_to_eval_payload(
    task_result: TaskResult,
    task: QuantTutorTask,
    persona: StudentPersona,
) -> dict:
    """Convert a TaskResult into a complete evaluation payload.

    This combines all necessary information for both DeepEval and custom evaluation.

    Returns:
        Dict with conversation, tool logs, task metadata, and assembled test cases info.
    """
    tool_summary = extract_tool_call_summary(task_result)

    payload = {
        "task_id": task.task_id,
        "persona_id": persona.persona_id,
        "difficulty": task.difficulty.value,
        "category": task.category.value,
        "conversation": [
            {"role": t.role, "content": t.content} for t in task_result.turns
        ],
        "tool_call_log": [
            {
                "name": tc.name,
                "args": tc.args,
                "result": tc.result,
                "success": tc.success,
                "turn_index": tc.turn_index,
            }
            for tc in task_result.tool_call_log
        ],
        "tool_summary": tool_summary,
        "expected_tools": task.ground_truth.expected_mcp_tools,
        "expected_outcome": task.ground_truth.expected_outcome,
        "scores": {
            "quant_result": task_result.quant_result_score,
            "quant_process": task_result.quant_process_score,
            "tutor_scores": task_result.tutor_scores,
            "overall": task_result.overall_score,
        },
        "sandbox_info": getattr(task_result, "sandbox_info", {}),
    }

    return payload


def enrich_test_case_with_mcp(
    test_case: "ConversationalTestCase",
    proxy_logs: list,
    core_tools: list[str],
    distractor_tools: list[str],
    tool_schemas: list[dict] | None = None,
) -> "ConversationalTestCase":
    """Enrich an existing ConversationalTestCase with MCP data from proxy logs.

    When the ConversationSimulator produces a test case, it doesn't include
    MCP tool call data.  This function **rebuilds** assistant Turn objects
    (rather than mutating them post-init) so that the Pydantic model_validator
    sets ``_mcp_interaction = True`` — which MultiTurnMCPUseMetric needs to
    recognise tool-call steps.

    Args:
        test_case: Existing ConversationalTestCase (from ConversationSimulator).
        proxy_logs: Raw tool call logs from MCPProxy.
        core_tools: Core tool names.
        distractor_tools: Distractor tool names.
        tool_schemas: Full tool schema dicts from proxy.get_available_tools().

    Returns:
        The same test case, enriched with MCP data (turns replaced in place).
    """
    if not DEEPEVAL_AVAILABLE:
        return test_case

    # Import the CallToolResult wrapper from process_metrics
    from evaluation.deepeval_metrics.process_metrics import (
        _build_mcp_servers,
        _wrap_result_as_call_tool_result,
    )

    # Group proxy logs by turn index
    logs_by_turn: dict[int, list] = {}
    for log in proxy_logs:
        turn_idx = getattr(log, "turn_index", 0) or 0
        logs_by_turn.setdefault(turn_idx, []).append(log)

    # Rebuild assistant turns that have tool calls.
    #
    # MultiTurnMCPUseMetric._get_tasks() calls get_unit_interactions() which
    # splits the conversation into units bounded by user→assistant boundaries.
    # It then skips any unit with len <= 2.  Our conversation is normally
    # [user, assistant, user, assistant, …], producing units of length 2 that
    # are ALL skipped — yielding an empty task list and score 0.
    #
    # To fix this we split an assistant turn that has tool calls into TWO
    # consecutive assistant turns:
    #   1. A "tool execution" turn (with mcp_tools_called → _mcp_interaction=True)
    #   2. A "response" turn (the original text reply)
    # This gives units of length >= 3, which _get_tasks() processes.
    new_turns = []
    assistant_turn_idx = 0
    for turn in test_case.turns:
        if turn.role == "assistant":
            turn_logs = logs_by_turn.get(assistant_turn_idx, [])
            if turn_logs:
                mcp_tool_calls = [
                    MCPToolCall(
                        name=log.name,
                        args=log.args,
                        result=_wrap_result_as_call_tool_result(log.result or ""),
                    )
                    for log in turn_logs
                ]
                tool_calls = [
                    ToolCall(
                        name=log.name,
                        input_parameters=log.args,
                        output=log.result[:500] if log.result else "",
                    )
                    for log in turn_logs
                ]
                # Turn 1: tool execution step.
                # NOTE: DeepEval has a bug where model_validator(mode="before")
                # sets data["_mcp_interaction"] = True, but that doesn't
                # propagate to the PrivateAttr.  We set it manually below.
                tool_names = ", ".join(log.name for log in turn_logs)
                tool_turn = Turn(
                    role="assistant",
                    content=f"[Executed tools: {tool_names}]",
                    tools_called=tool_calls,
                    mcp_tools_called=mcp_tool_calls,
                )
                tool_turn._mcp_interaction = True
                new_turns.append(tool_turn)
                # Turn 2: the actual text response
                new_turns.append(Turn(role="assistant", content=turn.content))
            else:
                new_turns.append(turn)
            assistant_turn_idx += 1
        else:
            new_turns.append(turn)

    test_case.turns = new_turns

    # Set MCP servers with proper mcp.types.Tool objects
    test_case.mcp_servers = _build_mcp_servers(
        core_tools, distractor_tools, tool_schemas
    )

    return test_case
