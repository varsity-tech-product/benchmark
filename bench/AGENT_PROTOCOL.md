# QuantTutorBench Agent Protocol

This document defines how an agent interacts with the QuantTutorBench
evaluation environment. Any MCP-compatible agent can be evaluated by
following this protocol.

## Connection

Connect to the QuantTutorBench MCP server. On connection you will
receive a list of available tools.

## Start

Call `get_session_info()` to receive:
- Task description (what to teach)
- Student profile (knowledge level)
- Student's opening message
- Maximum number of turns

## Interact

You have two types of tools:

**Domain tools** -- use these to prepare your teaching:
- `shell_exec`, `file_write`, `file_read`, `file_list` (coding)
- `fetch_market_data`, `compute_indicator`, `compute_statistics` (analysis)
- `run_backtest`, `search_docs` (backtesting and reference)

**Session tool** -- use this to talk to the student:
- `send_message(text)` -- sends your message, returns the student's reply

You decide when to research, when to code, and when to talk.
There is no fixed order. Call domain tools as many times as you need
between `send_message` calls.

## End

When `send_message` returns `"status": "completed"`, the session is
over. Stop calling tools.

The session also ends if:
- All learning objectives are met (detected server-side)
- Maximum turns reached
- Wall-clock timeout exceeded

## Evaluation

After the session, the server scores your performance from the
interaction log:
- **QR (Quantitative Result)**: correctness of analysis and code
- **QP (Quantitative Process)**: tool usage patterns and efficiency
- **Tutor 7D**: pedagogical quality across 7 dimensions

## Notes

- Your text responses (outside of `send_message`) are internal notes.
  Only `send_message` delivers content to the student.
- Distractor tools may be present. Calling them is not penalized
  heavily but indicates poor tool selection judgment.
- The server records every tool call. Work as you naturally would.
