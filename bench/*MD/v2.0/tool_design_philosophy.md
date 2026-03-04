# QuantTutorBench Tool Design Philosophy

## 1. Overall Design Philosophy

The tool design follows a **"Atomic Core + Domain Shortcuts + Noise Distractors"** three-tier structure. The core idea is: provide agents with minimal-granularity base capabilities (filesystem + shell), offer domain-specific convenient tools as optional paths, and pad the toolset with fully-functional distractor tools to form a standardized 15-slot evaluation environment.

---

## 2. Tool Classification System & Hierarchical Relationships

The three tool categories have a clear hierarchical relationship:

```
Atomic Tools                ← Minimum sufficient condition for task completion
   ↑ subset
Convenient Tools            ← Each is fully replaceable by atomic tool combinations
   ↑ mutually exclusive
Distractor Tools            ← Task-irrelevant, fully functional, should not be used
```

**Core constraint:** Every capability of a convenient tool is an encapsulation of an atomic tool operation sequence. Convenient tools provide no exclusive capability that atomic tool combinations cannot achieve. Distractor tools are strictly mutually exclusive with the other two categories and are randomly sampled per task.

### 2.1 Atomic Tools

These form the **minimum sufficient condition** for an agent to complete any task. Each tool does exactly one thing.

| Tool | Responsibility | Design Principle |
|------|---------------|-----------------|
| `shell_exec` | Execute any shell command in the sandbox | Maximum-freedom universal tool. Agent can directly execute inline code (`python3 -c "..."`), pipe commands, or run existing scripts — no other tools required |
| `file_write` | Write a file to /workspace | Single responsibility: accepts path + content, no side effects |
| `file_read` | Read file contents | Supports path resolution across /workspace, /data, /docs, /student_code; auto-preview for large CSVs |
| `file_list` | List directory contents | Shares path resolution logic with file_read |
| `get_environment_info` | Return environment information (directory structure, available files, installed packages) | Discovery tool to help agent understand the runtime environment |

**On the sufficiency of shell_exec:** Strictly speaking, `shell_exec` alone is theoretically sufficient to complete all tasks — an agent can use inline Python (`python3 -c "..."`), heredoc (`python3 << 'EOF'`), or pipe commands to handle data processing, computation, and file writing, entirely bypassing `file_write` and `file_read`. This is a known characteristic of the current design: `shell_exec`'s capability boundary effectively covers the responsibilities of all other atomic tools. Retaining `file_write`/`file_read`/`file_list` as separate tools provides clearer semantic separation and finer-grained behavior tracking (each file operation gets its own log entry), rather than being a necessity in terms of capability.

### 2.2 Convenient Tools

Convenient tools are **domain encapsulations of atomic tool operation sequences** — every one of them can be fully replaced by combinations of atomic tools. They are configured per task. In the tool list the agent sees, convenient tools are **visually indistinguishable** from atomic tools.

| Tool | Encapsulated Steps | Equivalent Atomic Operations |
|------|-------------------|------------------------------|
| `fetch_market_data` | Glob search symbol → read CSV → date filter → save to workspace → return summary | `file_list` to find file → `file_read` to load → `shell_exec` to run pandas filter → `file_write` to save |
| `compute_indicator` | Load CSV → compute technical indicator (SMA/EMA/RSI/BB/MACD) → save enriched CSV | `file_read` → `shell_exec` to run computation script → `file_write` |
| `run_backtest` | Load data → apply strategy → generate signals → compute returns → 8 performance metrics → save equity curve + metrics JSON | Complete Python script, roughly 50–80 lines of code |
| `compute_statistics` | Load data → execute statistical method (ADF/correlation/cointegration/descriptive/missing) → return result | `shell_exec` to run statsmodels/scipy script |
| `plot_chart` | exec() matplotlib code → force Agg backend → auto savefig | `file_write` to create .py → `shell_exec` to execute |
| `analyze_backtest_results` | Load CSV → auto-detect returns column → compute 8 performance metrics → save JSON | `shell_exec` to run statistics script |
| `search_web` | DuckDuckGo instant answer search | Agent can achieve equivalent via `shell_exec` with curl/wget + jq |
| `search_docs` | Keyword search across /docs/ markdown files, ranked by hit count | `shell_exec` with grep/awk combination, or a Python script to traverse files |

**Design notes:**
- Every convenient tool is a strict subset operation of atomic tools — no exclusive capabilities
- Their value is providing **domain-idiomatic shortcut paths**, analogous to IDE hotkeys versus command line
- Configured per task via the `ground_truth.convenient_tools` field in task JSONs; different tasks expose different convenient tools

### 2.3 Distractor Tools

Randomly sampled from a global pool (currently 21 tools) to fill the toolset to the standard 15 slots. **Strictly mutually exclusive** with atomic and convenient tools.

**Key design principle: all distractors are fully functional.** When called, they return well-formatted, plausible-looking results (real computations or high-fidelity hardcoded data) — not error messages or empty results. This means an agent cannot distinguish distractors from legitimate tools by trial-and-error; it must judge based on task understanding.

**Current distractor pool examples:**
- Real computation: `compute_var` (Value at Risk), `compute_greeks` (option Greeks), `optimize_portfolio` (mean-variance optimization)
- Hardcoded data: `fetch_fundamentals` (fundamental analysis), `fetch_live_price` (real-time quotes), `query_database` (SQL query)
- Pseudo-random: `fetch_crypto_data` (cryptocurrency OHLCV)

---

## 3. Toolset Assembly Mechanism

### 3.1 Fixed 15-Slot Model

Each task exposes a fixed number of **15 tools** to the agent (`_TOTAL_TOOL_SLOTS = 15`):

```
n_distractor = 15 - len(atomic_tools) - len(convenient_tools)
```

For example, a data_analysis task: 5 atomic + 3 convenient = 8 useful tools + 7 distractors.

**Rationale for fixed slots:**
- Consistent toolset size across tasks, controlling the "number of tools" variable
- Signal-to-noise ratio varies naturally with task complexity (more atomic + convenient → fewer distractors)
- Sampling uses a fixed seed (`random.Random(seed)`) to ensure the same task sees the same distractor combination across runs

### 3.2 Mutual Exclusivity Guarantee

`registry.py` excludes all registered atomic and convenient tool names when sampling distractors, ensuring strict mutual exclusivity across the three tool categories.

---

## 4. Tool Usage Scoring (tool_usage dimension)

### 4.1 Why expected_tools Almost Only Contains shell_exec

`expected_tools` defines the tools an "ideal agent should use." It almost exclusively contains `shell_exec` for **scoring fairness** reasons:

- The tool_usage dimension uses a **hard mathematical formula** for scoring (missing an expected tool: **-0.15**, calling a distractor: **-0.10**)
- LLMs have a **strong behavioral inclination** (from training) to prefer atomic tools — writing scripts with shell_exec, manually processing data — this is an inherent model characteristic, not incorrect behavior
- If convenient tools were listed in expected_tools, agents that use atomic tools would be **unfairly penalized** for "not using expected tools"
- Therefore, expected_tools contains only the minimum set that the agent **will almost certainly use**, avoiding punishment for the normal atomic-tool usage tendency

### 4.2 Convenient Tool Bonus Mechanism

Using convenient tools is not required, but earns a small bonus:

```
base = 0.8 (when task has convenient tools) or 1.0 (no convenient tools)
bonus = 0.2 / n (each used convenient tool earns 0.2/n, where n = total convenient count)
```

This means:
- Not using any convenient tool → base score of 0.8, no severe penalty
- Reasonable use of convenient tools → approaches 1.0
- Using all convenient tools → exactly 1.0

### 4.3 Effectiveness Dimension

tool_usage also includes a 40% weight effectiveness assessment: checking whether tool calls actually succeeded (whether the result contains "Error:" prefix or "Traceback"). Final score = 0.60 × selection score + 0.40 × effectiveness.

---

## 5. Execution Sandbox

All tools (not just shell_exec) execute inside a Docker v2.0 container:

- **tool_executor.py**: A long-lived JSON-lines RPC daemon inside the container. Receives tool call requests and dispatches to the corresponding function in the `CORE_TOOLS` dictionary
- **tools.py is container-unaware**: Its default paths (/data, /workspace, /docs, etc.) naturally match container mount points — no environment variable switching needed
- **tool_wrappers.py**: In Docker mode, uses the `make_container_tool()` factory to uniformly wrap all tool calls; in local mode, shell_exec goes through `subprocess.run()`, others call functions directly

---

## 6. Guide for Adding New Tools

When writing new tools, follow these principles:

### 6.1 Determine Tool Type

| Question | → Type |
|----------|--------|
| Is this capability a fundamental prerequisite for task completion? Not replaceable by other atomic tools? | → Atomic |
| Can this be replaced by atomic tool combinations, but encapsulates a domain-idiomatic operation? | → Convenient |
| Does this reasonably exist in quantitative finance, but is irrelevant to the current task? | → Distractor |

### 6.2 Atomic Tool Design Principles

- **Single responsibility**: One tool does one thing — do not bundle I/O and computation
- **No implicit side effects**: Do not auto-save files, auto-truncate data, or auto-convert formats
- **Parameters as control**: All behavior is explicitly controlled by parameters — no "smart inference"
- **Irreplaceability**: Confirm this capability cannot be achieved through existing atomic tools (especially shell_exec)
- **Unified path resolution**: Use the `_resolve_path()` function to ensure consistent cross-directory lookup

### 6.3 Convenient Tool Design Principles

- **Replaceability**: Must be fully replaceable by atomic tool combinations — no exclusive capabilities
- **Domain focus**: Encapsulate quantitative finance domain-idiomatic workflows, not general programming operations
- **Self-contained results**: Return values contain sufficient information for the agent to understand execution results without needing follow-up tool calls

### 6.4 Distractor Tool Design Principles

- **Fully functional**: Must return well-formatted results — no errors or empty values
- **Domain relevant**: Tool names and descriptions should belong to the quantitative finance domain; the agent must judge relevance based on task understanding, not tool availability
- **Non-destructive**: Calling a distractor should not modify workspace state or affect other tools' behavior

### 6.5 Registration Specification

New tools must be registered in `CORE_TOOLS` (tools.py) or `DISTRACTOR_TOOLS` (distractor_tools.py) with the following structure:

```python
"tool_name": {
    "function": tool_function,        # Actual execution function
    "description": "...",             # Tool description visible to the agent
    "parameters": {                   # JSON Schema format parameter definition
        "type": "object",
        "properties": { ... },
        "required": [ ... ]
    }
}
```

Convenient tools do not require separate registration — they are a subset of core tools, designated per task via the `ground_truth.convenient_tools` field in task JSONs.
