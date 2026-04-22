# Two-Layer Eval Architecture: Chat Loop + Agent Loop

> Status: Planning (v3.0)
> Author: Ewan + Claude
> Date: 2026-03-16

## 1. Problem Statement

Current evaluation operates at the **Chat Loop** level only — we observe
conversation turns and MCPProxy tool logs (tool name, args, result).
This captures **what** the agent did but not **why**.

For agents with visible internal loops (direct API mode), each conversation
turn contains multiple LLM↔tool cycles with **intermediate reasoning text**
between tool calls. This data is currently discarded.

```
Chat Loop (orchestrator manages):
  Turn 1: student message → agent response
  Turn 2: student message → agent response
                              ↑ we evaluate here

Agent Loop (inside each response, invisible for black-box agents):
  API call 1 → "Let me check the data first..." → tool_use(fetch_market_data)
  tool_result → "Data has 252 rows. Now I'll compute..." → tool_use(shell_exec)
  tool_result → "The Sharpe ratio is 1.3. Let me verify..." → tool_use(compute_statistics)
  tool_result → end_turn: final response
                ↑ we COULD evaluate here (for visible agents)
```

## 2. Two-Layer Design

### Layer 1: Chat Loop Eval (all agents, including black-box / human)

**Input**: conversation history + MCPProxy tool logs (name, args, result, success)

**Dimensions** (retained from current QP):

| Dimension | Type | Rationale |
|---|---|---|
| tool_usage | Programmatic | Pure math on tool sets — no reasoning chain needed |
| role_adherence | LLM judge | Based on conversation content |
| topic_adherence | LLM judge | Based on conversation content |
| knowledge_retention | LLM judge | Based on conversation content |
| step_efficiency (lite) | Programmatic + LLM | Count-based ratio + basic sequencing |

**QR dimensions** (unchanged):
- code_eval (programmatic)
- result_judge (LLM judge, multi-model)

### Layer 2: Agent Loop Eval (only agents with visible internal loop)

**Input**: per-turn agent loop trace — list of intermediate steps, each with:
- LLM text output (reasoning between tool calls)
- tool_use blocks (name, input)
- tool_result content
- stop_reason per API call
- token usage per API call

**Dimensions** (migrated + enhanced from current QP):

| Dimension | Type | Enhancement over Chat Loop version |
|---|---|---|
| process_reasonableness | LLM judge | Can read actual reasoning text, not just infer from tool names |
| process_alignment | LLM judge | Can verify reasoning matches reference approach, not just tool overlap |
| step_efficiency (full) | Programmatic + LLM | Can distinguish "useful thinking" from "redundant retries" by reading intermediate text |
| code_process (full) | Programmatic + LLM | Can see debugging reasoning, not just sequential tool calls |

### Scoring Integration

```
Black-box agent:
  QP = weighted_avg(Chat Loop Eval dimensions only)

Visible agent:
  QP = w1 * weighted_avg(Chat Loop dimensions) + w2 * weighted_avg(Agent Loop dimensions)

  Suggested: w1 = 0.4, w2 = 0.6 (Agent Loop has richer signal)
```

## 3. Data Flow

### 3.1 Adapter Interface

```python
class BaseAgentAdapter:
    def get_agent_loop_trace(self) -> list[dict] | None:
        """Return internal agent loop trace, or None for black-box agents."""
        return None
```

### 3.2 Trace Format (per conversation turn)

```python
# Each entry = one API call within the agent loop
AgentLoopStep = {
    "turn": int,              # which conversation turn (1-indexed)
    "step": int,              # step within this turn's agent loop (1-indexed)
    "text": str | None,       # intermediate reasoning text (if any)
    "tool_uses": [            # tool calls in this step
        {"name": str, "input": dict, "id": str}
    ],
    "tool_results": [         # results (after execution)
        {"tool_use_id": str, "content": str, "is_error": bool}
    ],
    "stop_reason": str,       # "tool_use" | "end_turn"
    "input_tokens": int,
    "output_tokens": int,
}
```

### 3.3 Which Adapters Support It

| Adapter | Agent Loop Trace | How |
|---|---|---|
| anthropic (OAuth) | Yes | `_oauth_loop` accumulates steps |
| anthropic (SDK) | No | ClaudeSDKClient black-box |
| openai (direct API) | Yes | `_generate_direct` accumulates steps |
| openai (SDK) | No | Runner.run_sync black-box |
| google (ADK) | No (future) | Would need similar direct API path |
| generic (OpenRouter) | Partial | Single-round tool call only |
| baseline | No | Dumps ground truth, no agent loop |

### 3.4 Orchestrator Integration

```python
# orchestrator.py — after generate_response()
agent_loop_trace = adapter.get_agent_loop_trace()

# Step 3: Evaluation
if agent_loop_trace is not None:
    agent_loop_scores = evaluate_agent_loop(agent_loop_trace, reference, ...)
    # Merge into QP with weighting
```

## 4. Migration Path (from current single-layer)

### Phase A: Data Capture (minimal change)
1. Add `_loop_trace: list[dict]` accumulation in `_oauth_loop` and `_generate_direct`
2. Add `get_agent_loop_trace()` to BaseAgentAdapter (returns None by default)
3. Override in anthropic (OAuth) and openai (direct) adapters
4. Save trace alongside existing trace_report.py output

### Phase B: Agent Loop Eval Dimensions
1. Create `bench/evaluation/deepeval_metrics/agent_loop_eval.py`
2. Migrate process_reasonableness to use intermediate text when available
3. Migrate process_alignment to use intermediate text when available
4. Enhanced step_efficiency with reasoning-aware analysis
5. Enhanced code_process with debugging reasoning analysis

### Phase C: Scoring Integration
1. Modify `process_metrics.py` weighted aggregate to support two tiers
2. Adjust QP weights: Chat Loop dims vs Agent Loop dims
3. Update score_report.py to show both tiers
4. Ensure black-box agents gracefully degrade (Agent Loop dims = None)

## 5. Key Design Principles

1. **Backward compatible**: Black-box agents (SDK mode, human, third-party)
   continue to work with Chat Loop Eval only. No regression.

2. **Additive, not replacement**: Agent Loop Eval adds signal on top of
   Chat Loop Eval. Both run for visible agents.

3. **Same MCPProxy**: Tool calls still route through MCPProxy regardless
   of eval layer. Agent Loop Eval reads the intermediate reasoning text
   that Chat Loop Eval cannot see.

4. **Config-driven**: `OPENAI_USE_DIRECT_API` / `AGENT_USE_OAUTH` switches
   control which mode each adapter uses. Eval automatically adapts based
   on whether `get_agent_loop_trace()` returns data.

## 6. Dimension Placement Summary

| Current Dimension | Chat Loop Eval | Agent Loop Eval | Notes |
|---|---|---|---|
| tool_usage | Keep (0.20) | — | Pure math, no reasoning needed |
| role_adherence | Keep (0.10) | — | Conversation-level |
| topic_adherence | Keep (0.10) | — | Conversation-level |
| knowledge_retention | Keep (display) | — | Excluded from QP aggregate |
| step_efficiency | Lite (0.15) | Full | Lite=count ratio; Full=reasoning-aware |
| process_reasonableness | — | Move here (0.20) | Needs intermediate text |
| process_alignment | — | Move here (0.10) | Needs intermediate text |
| code_process | Lite (0.15) | Full | Lite=tool patterns; Full=debug reasoning |

## 7. Not In Scope

- Google ADK direct API mode (future work)
- Extended thinking / chain-of-thought extraction (model-specific feature)
- Streaming mode observation (we use non-streaming API calls)
- Modifying the Chat Loop evaluation dimensions beyond re-weighting
