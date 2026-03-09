# QuantTutorBench Known Pitfalls & Design Decisions

> Version: v2.0 | A record of critical issues encountered during development and their fixes

## 1. Document Content Leaking into Scoring (P0 — Critical)

**Problem:** `data_fetch_historical.md` contained "Task Goal: Build a script that creates exactly: historical_market_prices.csv." The agent read this via `file_read` and followed the doc's requirements. The Result Judge also received this doc content through `_extract_agent_key_outputs`, causing scoring bias.

**Fix:**
1. Added `file_read`, `file_list`, `search_docs`, `search_web` to the Result Judge's `_SKIP_TOOLS` set.
2. Reference docs no longer contain task-specific requirements.
3. Scoring input only uses results from execution-type tools (`shell_exec`, `file_write`, `run_backtest`, etc.).

**Lesson:** When adding new read/query-type tools, always evaluate whether they need to be added to `_SKIP_TOOLS`. Reference doc content can influence scoring through multiple paths — docs must maintain their "general knowledge manual" positioning.

## 2. Symlinks Breaking Inside Docker Containers (P1)

**Problem:** `_create_staged_dirs()` used `os.symlink` to create data file links, but symlinks pointed to host paths. Inside the Docker container, these paths don't exist (`/data/xxx.csv → /host/path/xxx.csv`, but `/host/path` is inaccessible from within the container).

**Fix:** Replaced with `os.link` (hard link — zero-copy on same filesystem) + `shutil.copy2` fallback (for cross-filesystem cases).

**Lesson:** Any code involving host-to-container path mapping must not use symlinks. Hard links or physical copies are the only reliable approaches.

## 3. Heredoc Python Code Detection (P2)

**Problem:** `code_eval.py` initially only detected `.py` files and `python -c "..."` format, missing a large volume of multi-line code passed via `python - <<'EOF'` (one of the most common execution patterns used by agents).

**Fix:**
- Layer A added Source 4: heredoc regex `python3?\s+-\s*<<\s*['\"]?(\w+)['\"]?\n(.*?)\n\1`
- Layer B's `_PYTHON_CMD_RE` updated to include the `- <<` pattern

**Lesson:** Agents execute Python code in at least 3 ways: `.py` files, `python -c`, and heredoc stdin. Any new code detection logic must cover all three patterns.

## 4. Tool Effectiveness Check Pitfall (P2)

**Problem:** MCPProxy's `log.success` is always `True` even when the tool function returns an error string, because `tool_executor.py` catches all exceptions and serializes them as normal return values.

**Fix:** Added `_tool_call_effective()` in `tool_usage.py` that additionally checks whether `log.result` starts with `"Error:"` or contains `"Traceback"`.

**Lesson:** Never rely solely on `log.success` to determine whether a tool call succeeded. Always inspect the actual content of `log.result`.

## 5. Missing PACING Instruction Causing Students to Ask Everything at Once (P2)

**Problem:** The student simulator dumped all learning objectives in a single turn, preventing the tutor from teaching incrementally. The agent called many tools and produced a lengthy response in Turn 1, and subsequent conversation turns became low-quality repetitive confirmations.

**Fix:** Added a PACING instruction in `prompt_config.py`'s `build_scenario()`, requiring the student to "ask about one topic at a time, wait for the tutor to finish, then naturally transition to the next learning goal."

**Lesson:**
- The student simulator's behavior directly affects tutor evaluation scores.
- Opening messages (`student_openings`) must not cram all objectives — they should only provide a natural entry point.
- The PACING instruction and opening message design must work together to ensure proper conversation pacing.
