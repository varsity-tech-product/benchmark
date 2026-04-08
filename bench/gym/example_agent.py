#!/usr/bin/env python3
"""Example agents showing how to use QuantTutorEnv.

Usage::

    python bench/gym/example_agent.py --task D01_load_inspect_ohlcv --docker
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))


def run_echo_agent(task_id: str, use_docker: bool = False):
    """Trivial agent — echoes student messages. For smoke-testing the env."""
    from bench.gym.env import QuantTutorEnv

    env = QuantTutorEnv(use_docker=use_docker)
    obs = env.reset(task_id)

    print(f"Task: {task_id}")
    print(f"Student: {obs.student_message[:200]}")
    print(f"Tools: {[t['name'] for t in obs.available_tools]}")
    print()

    while not obs.done:
        # Echo agent: just acknowledge the student
        reply = (
            f"Thanks for your question. I see you're asking about: "
            f"{obs.student_message[:100]}. Let me help you with that."
        )
        obs = env.send_message(reply)
        if obs.student_message:
            print(f"Student (turn {obs.turn}): {obs.student_message[:200]}")

    print(f"\nConversation done. Reason: {obs.info.get('termination_reason', 'unknown')}")
    print(f"Total turns: {obs.turn}")

    # Evaluate
    print("\nRunning evaluation...")
    scores = env.evaluate()
    print(f"  OAS:   {scores.overall:.4f}")
    print(f"  QR:    {scores.quant_result:.4f}")
    print(f"  QP:    {scores.quant_process:.4f}")
    print(f"  Tutor: {scores.tutor:.4f}")
    if scores.error:
        print(f"  Error: {scores.error}")

    env.close()
    return scores


def run_openai_agent(
    task_id: str,
    model: str = "gpt-4o",
    use_docker: bool = True,
    max_tool_calls: int = 10,
):
    """Agent using OpenAI API with tool calling.

    Demonstrates the standard gym loop:
    1. Observe student message
    2. Decide whether to call tools or reply
    3. If tools: call env.call_tool(), accumulate results
    4. Send reply to student via env.send_message()
    """
    import openai
    from bench.gym.env import QuantTutorEnv

    client = openai.OpenAI()
    env = QuantTutorEnv(use_docker=use_docker)
    obs = env.reset(task_id)

    system_prompt = (
        "You are a quantitative finance tutor. Help the student understand "
        "the concepts and complete the task. You have access to tools — "
        "call them when needed to analyze data, run code, or execute backtests. "
        "Explain your reasoning clearly.\n\n"
        f"Task: {obs.info.get('task_id', task_id)}"
    )

    # Convert env tools to OpenAI format
    openai_tools = _to_openai_tools(obs.available_tools)

    # Agent's internal message history (for the LLM)
    agent_messages = [{"role": "system", "content": system_prompt}]

    while not obs.done:
        # Add student message to agent's context
        agent_messages.append({"role": "user", "content": obs.student_message})

        # LLM loop: generate response, handle tool calls
        tool_calls_this_turn = 0
        while tool_calls_this_turn < max_tool_calls:
            response = client.chat.completions.create(
                model=model,
                messages=agent_messages,
                tools=openai_tools if openai_tools else None,
            )
            choice = response.choices[0]

            if not choice.message.tool_calls:
                # No tool calls — final text response
                break

            # Execute tool calls through the env
            agent_messages.append(choice.message.model_dump())
            for tc in choice.message.tool_calls:
                try:
                    kwargs = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    result = env.call_tool(tc.function.name, **kwargs)
                except Exception as e:
                    result = f"Error: {e}"

                agent_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
                tool_calls_this_turn += 1

        # Extract final text and send to student
        reply = choice.message.content or "Let me continue."
        agent_messages.append({"role": "assistant", "content": reply})

        print(f"Tutor (turn {obs.turn}): {reply[:200]}")
        obs = env.send_message(reply)
        if obs.student_message:
            print(f"Student (turn {obs.turn}): {obs.student_message[:200]}")
        print()

    print(f"\nDone. Reason: {obs.info.get('termination_reason', 'unknown')}")

    scores = env.evaluate()
    print(f"OAS={scores.overall:.3f}  QR={scores.quant_result:.3f}  "
          f"QP={scores.quant_process:.3f}  Tutor={scores.tutor:.3f}")

    env.close()
    return scores


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """Convert benchmark tool schemas to OpenAI function calling format."""
    result = []
    for tool in tools:
        params = tool.get("parameters", {})
        properties = {}
        required = []
        for pname, pinfo in params.items():
            if isinstance(pinfo, dict):
                prop = {
                    "type": pinfo.get("type", "string"),
                    "description": pinfo.get("description", pname),
                }
                if "items" in pinfo:
                    prop["items"] = pinfo["items"]
                properties[pname] = prop
                if pinfo.get("required", False):
                    required.append(pname)
            else:
                properties[pname] = {"type": "string", "description": pname}

        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required

        result.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": schema,
            },
        })
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Example gym agents")
    parser.add_argument("--task", default="D01_load_inspect_ohlcv")
    parser.add_argument("--agent", choices=["echo", "openai"], default="echo")
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--docker", action="store_true")
    args = parser.parse_args()

    if args.agent == "echo":
        run_echo_agent(args.task, use_docker=args.docker)
    else:
        run_openai_agent(args.task, model=args.model, use_docker=args.docker)
