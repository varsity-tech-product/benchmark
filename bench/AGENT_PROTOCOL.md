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
- `send_message(text, attachments?, reasoning?)` -- sends your message,
  returns the student's reply.
  - `text` *(required)* — the message delivered to the student.
  - `attachments` *(optional)* — up to 3 workspace file paths to share.
  - `reasoning` *(optional)* — your private rationale for this turn
    (why this wording, what you expect to learn from the reply, what
    hypothesis you are testing). It is recorded for post-hoc analysis
    and is **never shown to the student**. Including it is encouraged
    but not required.

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
- The optional `reasoning` field on `send_message` is the recommended
  channel for capturing your turn-level rationale alongside the
  outgoing message. It does not affect the student's reply but is
  available to evaluators reviewing the trace.
- Distractor tools may be present. Calling them is not penalized
  heavily but indicates poor tool selection judgment.
- The server records every tool call. Work as you naturally would.
