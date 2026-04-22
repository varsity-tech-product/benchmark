"""Simulate BetaToolRunner iteration to verify incremental tool_result capture.

This test mocks the runner's behavior: yielding assistant messages with tool_use,
executing tools (appending tool_result to messages), and applying context_management
(clearing old tool_use/tool_result pairs). It verifies that the proposed fix
correctly captures ALL tool_results before they are cleared.

Run: cd bench && python tests/test_capture_tool_results.py
"""

import copy
import sys

# ── Mock objects to simulate SDK types ─────────────────────────


class MockBlock:
    """Simulate an Anthropic content block (tool_use, text, thinking)."""

    def __init__(self, type, **kwargs):
        self.type = type
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockMessage:
    """Simulate a BetaMessage with content blocks."""

    def __init__(self, content_blocks):
        self.content = content_blocks
        self.role = "assistant"
        self.usage = type("Usage", (), {"input_tokens": 100, "output_tokens": 50})()


class MockRunner:
    """Simulate BetaToolRunner iteration with context_management.

    Reproduces the exact timing:
    1. API call → assistant message (with tool_use)
    2. yield message to caller
    3. Execute tools → generate tool_result → append to messages
    4. Context management → clear old tool_use/tool_result (keep last N)
    5. Next API call
    """

    def __init__(self, scenario, keep_tool_uses=6):
        self._scenario = scenario  # list of (message, tool_results) per iteration
        self._params = {"messages": []}
        self._keep = keep_tool_uses
        self._tool_use_count = 0

    def __iter__(self):
        """Simulate BetaToolRunner.__run__ timing exactly:

        while not _should_stop():
            yield message                    # caller receives assistant message
            # --- after yield resumes ---
            iteration_count += 1
            if not _check_and_compact():     # context_management may clear old msgs
                response = generate_tool_call_response()  # execute tools
                if response is None: return  # no tool_use → done
                append_messages(message, response)  # add tool_result to messages
        """
        for i, (message, tool_results) in enumerate(self._scenario):
            # Step 1: Yield assistant message (caller receives it here)
            yield message

            # --- After yield resumes (caller got message, continues loop) ---

            # Step 2: Append assistant message to history
            assistant_msg = {
                "role": "assistant",
                "content": [
                    {
                        "type": b.type,
                        "id": getattr(b, "id", None),
                        "name": getattr(b, "name", None),
                        "input": getattr(b, "input", None),
                        "text": getattr(b, "text", None),
                        "thinking": getattr(b, "thinking", None),
                    }
                    for b in message.content
                ],
            }
            self._params["messages"].append(assistant_msg)

            # Step 3: Context management — runs BEFORE tool execution in real SDK
            # (API returns modified messages as part of the response)
            if self._tool_use_count > self._keep:
                self._apply_context_management()

            # Step 4: Execute tools → append user message with tool_results
            if tool_results:
                user_msg = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tr_id,
                            "content": tr_content,
                            "is_error": tr_err,
                        }
                        for tr_id, tr_content, tr_err in tool_results
                    ],
                }
                self._params["messages"].append(user_msg)
                self._tool_use_count += len(tool_results)

    def _apply_context_management(self):
        """Simulate clear_tool_uses: keep only last N tool_use/tool_result pairs."""
        # Collect all tool_use IDs in order
        all_ids = []
        for msg in self._params["messages"]:
            if msg["role"] == "assistant":
                for b in msg["content"]:
                    if b.get("type") == "tool_use" and b.get("id"):
                        all_ids.append(b["id"])

        if len(all_ids) <= self._keep:
            return

        # IDs to remove (oldest)
        remove_ids = set(all_ids[: -self._keep])

        # Remove from assistant messages
        for msg in self._params["messages"]:
            if msg["role"] == "assistant":
                msg["content"] = [
                    b
                    for b in msg["content"]
                    if not (b.get("type") == "tool_use" and b.get("id") in remove_ids)
                ]
            elif msg["role"] == "user":
                msg["content"] = [
                    b
                    for b in msg["content"]
                    if not (
                        b.get("type") == "tool_result"
                        and b.get("tool_use_id") in remove_ids
                    )
                ]

        # Clean up empty messages
        self._params["messages"] = [
            m for m in self._params["messages"] if m.get("content")
        ]


# ── Capture logic (current vs proposed) ────────────────────────


def capture_current(runner):
    """Current implementation: capture tool_use incrementally, tool_result from final history."""
    current_turn_blocks = []

    for message in runner:
        # _capture_iteration_blocks equivalent
        for block in message.content:
            if block.type == "tool_use":
                current_turn_blocks.append(
                    {
                        "type": "tool_use",
                        "name": block.name,
                        "_tool_id": block.id,
                    }
                )
            elif block.type == "text":
                current_turn_blocks.append({"type": "text", "text": block.text})
            elif block.type == "thinking":
                current_turn_blocks.append({"type": "thinking", "text": block.thinking})

    # _finalize_turn_blocks equivalent: read tool_results from final history
    input_history = list(runner._params["messages"])
    tool_results = {}
    for msg in input_history:
        if msg.get("role") != "user":
            continue
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tui = block.get("tool_use_id")
                if tui:
                    tool_results[tui] = {
                        "content": block.get("content", ""),
                        "is_error": block.get("is_error", False),
                    }

    # Inject tool_results
    final_blocks = []
    for block in current_turn_blocks:
        if block["type"] == "tool_use":
            tool_id = block.pop("_tool_id", "")
            final_blocks.append(block)
            if tool_id in tool_results:
                final_blocks.append({"type": "tool_result", **tool_results[tool_id]})
        else:
            final_blocks.append(block)

    return final_blocks


def _scan_tool_results(messages, captured):
    """Extract tool_results from messages into captured dict (incremental, idempotent)."""
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            tui = block.get("tool_use_id")
            if not tui or tui in captured:
                continue
            raw = block.get("content", "")
            if not isinstance(raw, str):
                raw = str(raw)
            is_err = block.get("is_error", False)
            if len(raw) > 800:
                raw = raw[:800] + "…"
            captured[tui] = {"content": raw, "is_error": bool(is_err)}


def capture_proposed(runner):
    """Proposed fix: capture tool_results incrementally from runner._params DURING iteration,
    plus one final scan AFTER the loop to catch the last iteration's results."""
    current_turn_blocks = []
    captured_tool_results = {}  # incremental capture

    for message in runner:
        # _capture_iteration_blocks equivalent
        for block in message.content:
            if block.type == "tool_use":
                current_turn_blocks.append(
                    {
                        "type": "tool_use",
                        "name": block.name,
                        "_tool_id": block.id,
                    }
                )
            elif block.type == "text":
                current_turn_blocks.append({"type": "text", "text": block.text})
            elif block.type == "thinking":
                current_turn_blocks.append({"type": "thinking", "text": block.thinking})

        # Incrementally capture tool_results from runner messages
        try:
            _scan_tool_results(
                runner._params.get("messages", []), captured_tool_results
            )
        except Exception as e:
            print(f"  [WARN] capture_tool_results failed: {e}")

    # Final scan: catch the last iteration's tool_result
    # (yielded message's tools are executed AFTER yield, so the last
    #  iteration's results only appear in messages after the loop exits)
    try:
        _scan_tool_results(runner._params.get("messages", []), captured_tool_results)
    except Exception:
        pass

    # Finalize: use captured_tool_results (complete) instead of final history
    final_blocks = []
    for block in current_turn_blocks:
        if block["type"] == "tool_use":
            tool_id = block.pop("_tool_id", "")
            final_blocks.append(block)
            if tool_id in captured_tool_results:
                final_blocks.append(
                    {"type": "tool_result", **captured_tool_results[tool_id]}
                )
        else:
            final_blocks.append(block)

    return final_blocks


# ── Test scenarios ─────────────────────────────────────────────


def build_scenario_basic():
    """8 tool calls, keep=6 → first 2 should be cleared by context_management."""
    scenario = []
    for i in range(8):
        tool_id = f"tool_{i}"
        msg = MockMessage(
            [
                MockBlock("text", text=f"Step {i}: running tool {i}"),
                MockBlock(
                    "tool_use",
                    id=tool_id,
                    name="shell_exec",
                    input={"command": f"echo {i}"},
                ),
            ]
        )
        result = [(tool_id, f"output of step {i}", False)]
        scenario.append((msg, result))
    return scenario


def build_scenario_mixed_tools():
    """Mixed tools including file_write, run_lean_backtest, plot_chart — mimics real I02 task."""
    scenario = []
    tools = [
        ("file_write", "Written 5569 bytes"),
        ("file_read", "using System; ..."),
        ("get_environment_info", '{"directories": ...}'),
        ("file_write", "Written 5903 bytes"),
        ("run_lean_backtest", "Status: success, Trades: 3302"),
        ("shell_exec", "equity curve data..."),
        ("shell_exec", "9922 spike rejections"),
        ("plot_chart", "Error generating chart: KeyError"),
        ("plot_chart", "Chart saved to /workspace/chart.png"),
        ("shell_exec", "final summary data"),
    ]
    for i, (name, result_text) in enumerate(tools):
        tool_id = f"tu_{i}_{name}"
        is_err = "Error" in result_text and name == "plot_chart"
        msg = MockMessage(
            [
                MockBlock("tool_use", id=tool_id, name=name, input={}),
            ]
        )
        # Add thinking between some iterations
        if i in (0, 3):
            msg = MockMessage(
                [
                    MockBlock("thinking", thinking=f"Planning step {i}..."),
                    MockBlock("text", text=f"Let me {name} now."),
                    MockBlock("tool_use", id=tool_id, name=name, input={}),
                ]
            )
        result = [(tool_id, result_text, is_err)]
        scenario.append((msg, result))
    return scenario


def build_scenario_parallel_tools():
    """Multiple tool_use blocks in one message (parallel calls)."""
    scenario = []
    # Iteration 1: 3 parallel shell_exec
    ids = [f"par_{j}" for j in range(3)]
    msg = MockMessage(
        [
            MockBlock("text", text="Running 3 checks in parallel"),
            MockBlock(
                "tool_use", id=ids[0], name="shell_exec", input={"cmd": "check1"}
            ),
            MockBlock(
                "tool_use", id=ids[1], name="shell_exec", input={"cmd": "check2"}
            ),
            MockBlock(
                "tool_use", id=ids[2], name="shell_exec", input={"cmd": "check3"}
            ),
        ]
    )
    results = [(ids[j], f"check{j} result", False) for j in range(3)]
    scenario.append((msg, results))

    # Iterations 2-6: single tools to push past keep=3
    for i in range(5):
        tid = f"single_{i}"
        msg = MockMessage([MockBlock("tool_use", id=tid, name="shell_exec", input={})])
        scenario.append((msg, [(tid, f"single result {i}", False)]))

    return scenario


def build_scenario_error_handling():
    """Runner._params access raises exception during capture."""

    class BrokenRunner(MockRunner):
        @property
        def _params(self):
            if not hasattr(self, "_access_count"):
                self._access_count = 0
            self._access_count += 1
            if self._access_count == 5:  # Break on 5th access
                raise RuntimeError("Simulated SDK internal error")
            return super()._params

        @_params.setter
        def _params(self, val):
            (
                super(BrokenRunner, type(self))._params.fset(self, val)
                if hasattr(super(), "_params")
                else None
            )
            self.__dict__["_params_val"] = val

    # Can't easily override property on MockRunner, so test with try/except in capture
    return build_scenario_basic()


def run_test(name, scenario, keep=6):
    print(f"\n{'='*60}")
    print(f" TEST: {name}")
    print(f" Scenario: {len(scenario)} iterations, keep={keep}")
    print(f"{'='*60}")

    # Run current implementation
    runner_current = MockRunner(copy.deepcopy(scenario), keep_tool_uses=keep)
    blocks_current = capture_current(runner_current)

    # Run proposed implementation
    runner_proposed = MockRunner(copy.deepcopy(scenario), keep_tool_uses=keep)
    blocks_proposed = capture_proposed(runner_proposed)

    # Count results
    def count_blocks(blocks):
        tu = sum(1 for b in blocks if b["type"] == "tool_use")
        tr = sum(1 for b in blocks if b["type"] == "tool_result")
        orphans = 0
        for i, b in enumerate(blocks):
            if b["type"] == "tool_use":
                has_result = (
                    i + 1 < len(blocks) and blocks[i + 1]["type"] == "tool_result"
                )
                if not has_result:
                    orphans += 1
        return tu, tr, orphans

    tu_c, tr_c, orp_c = count_blocks(blocks_current)
    tu_p, tr_p, orp_p = count_blocks(blocks_proposed)

    total_tool_calls = sum(len(results) for _, results in scenario)

    print(f"\n  Total tool calls in scenario: {total_tool_calls}")
    print("\n  CURRENT implementation:")
    print(f"    tool_use blocks:   {tu_c}")
    print(f"    tool_result blocks: {tr_c}")
    print(f"    orphaned tool_use: {orp_c}")
    print(f"    {'✗ MISSING RESULTS' if orp_c > 0 else '✓ All paired'}")

    print("\n  PROPOSED implementation:")
    print(f"    tool_use blocks:   {tu_p}")
    print(f"    tool_result blocks: {tr_p}")
    print(f"    orphaned tool_use: {orp_p}")
    print(f"    {'✗ MISSING RESULTS' if orp_p > 0 else '✓ All paired'}")

    # Verify proposed fix
    ok = True
    if orp_p > 0:
        print(f"\n  ✗ FAIL: proposed fix still has {orp_p} orphaned tool_use blocks")
        ok = False
    if tr_p != total_tool_calls:
        print(f"\n  ✗ FAIL: expected {total_tool_calls} tool_results, got {tr_p}")
        ok = False
    if tu_p != total_tool_calls:
        print(f"\n  ✗ FAIL: expected {total_tool_calls} tool_use, got {tu_p}")
        ok = False

    # Show block sequence for proposed
    if not ok or orp_c > 0:
        print("\n  Block sequence comparison:")
        print(f"  {'CURRENT':<35s}  {'PROPOSED':<35s}")
        max_len = max(len(blocks_current), len(blocks_proposed))
        for j in range(max_len):
            left = ""
            right = ""
            if j < len(blocks_current):
                b = blocks_current[j]
                if b["type"] == "tool_use":
                    left = f"tool_use({b['name']})"
                elif b["type"] == "tool_result":
                    left = f"tool_result({b['content'][:20]})"
                else:
                    left = f"{b['type']}"
            if j < len(blocks_proposed):
                b = blocks_proposed[j]
                if b["type"] == "tool_use":
                    right = f"tool_use({b['name']})"
                elif b["type"] == "tool_result":
                    right = f"tool_result({b['content'][:20]})"
                else:
                    right = f"{b['type']}"
            marker = "  ← DIFF" if left != right else ""
            print(f"  {left:<35s}  {right:<35s}{marker}")

    if ok:
        print("\n  ✓ PASS")

    return ok


def main():
    results = []

    results.append(
        run_test(
            "Basic: 8 tools, keep=6 (2 should be cleared)",
            build_scenario_basic(),
            keep=6,
        )
    )

    results.append(
        run_test(
            "Mixed tools (file_write, backtest, plot_chart) keep=6",
            build_scenario_mixed_tools(),
            keep=6,
        )
    )

    results.append(
        run_test(
            "Parallel tool calls (3 in one message) + 5 single, keep=3",
            build_scenario_parallel_tools(),
            keep=3,
        )
    )

    results.append(
        run_test(
            "Small scenario (3 tools, keep=6, no clearing)",
            build_scenario_basic()[:3],
            keep=6,
        )
    )

    results.append(
        run_test("Stress: 8 tools, aggressive keep=2", build_scenario_basic(), keep=2)
    )

    print(f"\n{'='*60}")
    print(f" SUMMARY: {sum(results)}/{len(results)} tests passed")
    print(f"{'='*60}")

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
