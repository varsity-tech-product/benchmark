# Parallelized Benchmark Runner — Implementation Plan

## 1. Goal

Replace the sequential benchmark runner with a parallelized architecture that maximizes throughput while maintaining per-job isolation. Each **job** = one `(task, persona, trial)` tuple. Jobs run in a `ThreadPoolExecutor` worker pool, each with its own Agent, Docker container, MCP proxy, and workspace.

## 2. Command Matrix

| Command | Scope | Job Count | Default `--workers` |
|---------|-------|-----------|---------------------|
| `run-single --task D01 --persona beginner` | 1 task × 1 persona | 1 | 1 (no pool) |
| `run-single --task D01` (no `--persona`) | 1 task × 3 personas | 3 | 3 |
| `run-group --group data_analysis` | N tasks × 3 personas | N×3 | 3 |
| `run-layer2` | 17 tasks × 3 personas | 51 | 3 |
| `run` (full benchmark) | L1 sequential + L2 parallel | 51 (L2 only) | 3 |

## 3. Compatibility Check (Pre-Implementation)

Verified that recent modifications do **not** break the current `run-single` pipeline:

- **Signature alignment**: `run_single_task()` ↔ `cmd_run_single()` ↔ `compute_task_score()` ↔ `evaluate_all_process_metrics()` — all parameter lists match.
- **Custom metrics wiring**: `role_adherence` and `topic_adherence` correctly routed through `_async_eval_role_adherence()` / `_async_eval_topic_adherence()` to `custom_conv_metrics.py`.
- **QP 7D weights**: Identical in `process_metrics.py`, `score_report.py`, and `run_benchmark.py` display.
- **`set_agent_max_steps()`**: Called at orchestrator L191; OpenAI adapter overrides, others no-op via base class.
- **Cost aggregation**: `token_usage` dict correctly populated with `agent`, `simulator`, `eval.by_stage_model`.
- **Report generation**: `generate_score_report()`, `generate_trace_md()`, `generate_cost_report()` all have matching signatures.

**Verdict**: No breaking changes. Safe to proceed with parallelization.

## 4. Architecture

### 4.1 Core Concept: One Job = One Isolated Stack

```
Job (thread N)
├── Agent instance     (fresh per job — new adapter object)
├── Docker container   (unique container_id)
├── MCP Proxy          (fresh per job — task-specific tools)
├── Workspace          (isolated temp dir or Docker volume)
├── Staged dirs        (per-job temp data/docs directories)
└── ConversationSimulator (DeepEval, fresh per job)
```

**Why per-job Agent?** The agent adapters carry mutable state:
- `_input_history`, `_agent`, `_tool_callback` (OpenAI)
- `system_prompt` mutation (all adapters)
- `set_task_context()` resets (all adapters)

Sharing an agent across concurrent jobs would cause data races.

### 4.2 Thread Safety Analysis

| Component | Thread-safe? | Strategy |
|-----------|-------------|----------|
| `ContainerManager` | Yes | Each job creates a unique container_id; `_containers` dict keyed by unique ID |
| `_ExecutorHandle.lock` | Yes | Serializes tool calls within a single container (no cross-container contention) |
| `MCPProxy` | Yes (per-job) | Created fresh per job in `create_proxy_for_task()` |
| `os.environ` mutations | **No** | Must be removed or replaced with per-job env passing |
| `ReferenceStore.load()` | Yes | Read-only file access |
| DeepEval evaluators | Yes | Each runs in its own asyncio event loop |

### 4.3 `os.environ` Fix (Critical)

Current code at orchestrator.py L160-163:
```python
os.environ["QTB_DATA_DIR"] = staged_data_dir
os.environ["QTB_WORKSPACE_DIR"] = container.workspace_path
```

This is a **race condition** when multiple jobs run concurrently. Fix: pass paths directly to tools via the `MCPProxy` or `ContainerManager` context, not via environment variables. In Docker mode these are already unused (container has fixed paths `/data`, `/workspace`). For local mode, thread-local or per-call parameter passing is needed.

**Decision**: Since `--docker` is the standard mode for benchmarking, and local mode is only used for quick development tests (always single-job), we can:
1. Keep env vars as-is for local mode (single-threaded guarantee: `--workers 1` forced when `--docker` is not set).
2. In Docker mode, env vars are irrelevant (container uses default paths). No change needed.
3. Add a guard: if `use_docker=False` and `workers > 1`, raise an error.

## 5. Detailed Changes

### 5.1 NEW: `bench/orchestrator/job_runner.py`

Extract the single-job execution logic from `cmd_run_single()` into a reusable function.

```python
@dataclass
class JobSpec:
    """One benchmark job = (task, persona, trial)."""
    task: QuantTutorTask
    persona: StudentPersona
    agent_type: str          # "generic", "openai", "anthropic", "google"
    condition_name: str      # "agent", "baseline", ...
    max_turns: int
    use_docker: bool
    save_result: bool
    result_base_dir: Path    # e.g. bench/results/run-single/openai/gpt-5.2/
    eval_model: str | None
    simulator_model: str | None
    model_override: str | None
    trial_index: int = 0

@dataclass
class JobResult:
    """Result of a single benchmark job."""
    job: JobSpec
    task_result: TaskResult | None
    trace_captured: dict      # proxy_logs, workspace snapshot
    error: str | None
    duration_seconds: float

def run_single_job(job: JobSpec) -> JobResult:
    """Execute one benchmark job in isolation.

    Creates a fresh agent, orchestrator, and runs the full lifecycle.
    Thread-safe: no shared mutable state.
    """
    start = time.time()

    # 1. Create fresh agent (per-job isolation)
    agent = _create_agent_from_spec(job)

    # 2. Create orchestrator (shares nothing except ContainerManager config)
    orchestrator = BenchmarkOrchestrator(
        bench_root=str(BENCH_ROOT),
        use_docker=job.use_docker,
        eval_model=job.eval_model,
        simulator_model=job.simulator_model,
    )

    # 3. Prepare trace capture hook
    trace_captured = {}
    if job.save_result:
        result_dir = job.result_base_dir / job.task.task_id / job.persona.persona_id
        result_dir.mkdir(parents=True, exist_ok=True)
        agent_files_dir = result_dir / "agent_files"

        def _capture(*, result, proxy, workspace_path):
            trace_captured["proxy_logs"] = list(proxy.get_logs())
            if workspace_path and os.path.isdir(workspace_path):
                if agent_files_dir.exists():
                    shutil.rmtree(agent_files_dir)
                shutil.copytree(workspace_path, str(agent_files_dir))

    # 4. Run the job
    try:
        task_result = orchestrator.run_single_task(
            task=job.task,
            persona=job.persona,
            agent=agent,
            max_turns=job.max_turns,
            tools_enabled=CONDITIONS[job.condition_name].tools_enabled,
            pre_teardown_hook=_capture if job.save_result else None,
        )

        # 5. Save reports
        if job.save_result:
            _save_job_reports(result_dir, task_result, trace_captured, agent, job)

        return JobResult(job=job, task_result=task_result,
                         trace_captured=trace_captured, error=None,
                         duration_seconds=time.time() - start)
    except Exception as e:
        return JobResult(job=job, task_result=None,
                         trace_captured=trace_captured, error=str(e),
                         duration_seconds=time.time() - start)

def _save_job_reports(result_dir, result, trace_captured, agent, job):
    """Save scores.md, trace.md, cost.md, agent_files/."""
    from evaluation.score_report import generate_score_report
    from evaluation.cost_report import generate_cost_report

    (result_dir / "scores.md").write_text(
        generate_score_report(result), encoding="utf-8"
    )
    (result_dir / "cost.md").write_text(
        generate_cost_report(result), encoding="utf-8"
    )

    if "proxy_logs" in trace_captured:
        from evaluation.trace_report import generate_trace_md
        (result_dir / "trace.md").write_text(
            generate_trace_md(
                result, trace_captured["proxy_logs"],
                agent_name=job.agent_type,
                model=agent.model,
                condition=job.condition_name,
            ),
            encoding="utf-8",
        )
```

### 5.2 NEW: `bench/orchestrator/parallel_runner.py`

Orchestrates parallel execution of multiple jobs.

```python
def run_jobs_parallel(
    jobs: list[JobSpec],
    max_workers: int = 3,
    progress_callback: Callable | None = None,
) -> list[JobResult]:
    """Run multiple benchmark jobs in parallel.

    Args:
        jobs: List of job specifications.
        max_workers: ThreadPoolExecutor concurrency limit.
        progress_callback: Called with (completed, total, latest_result).

    Returns:
        List of JobResult in same order as input jobs.
    """
    results: list[JobResult | None] = [None] * len(jobs)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {
            pool.submit(run_single_job, job): i
            for i, job in enumerate(jobs)
        }

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = JobResult(
                    job=jobs[idx], task_result=None,
                    trace_captured={}, error=str(e),
                    duration_seconds=0.0,
                )
            completed += 1
            if progress_callback:
                progress_callback(completed, len(jobs), results[idx])

    return results
```

### 5.3 MODIFY: `bench/run_benchmark.py`

#### A. Make `--persona` optional in `run-single`

```python
# Change from:
single_parser.add_argument("--persona", default="beginner_no_finance", help="Persona ID")

# Change to:
single_parser.add_argument(
    "--persona", default=None,
    help="Persona ID (omit to run all 3 personas in parallel)",
)
```

#### B. Update `cmd_run_single()` to support multi-persona parallel

```python
def cmd_run_single(args):
    """Run a single task (one persona or all personas in parallel)."""
    task = _find_and_load_task(args.task)
    workers = getattr(args, "workers", 3)

    if args.persona:
        # Single persona — run directly (no pool overhead)
        persona_ids = [args.persona]
        workers = 1
    else:
        # All personas — run in parallel
        persona_ids = task.persona_ids  # ["beginner_no_finance", "intermediate_developer", "advanced_quant"]

    jobs = [_build_job_spec(task, pid, args) for pid in persona_ids]

    if len(jobs) == 1:
        result = run_single_job(jobs[0])
        _print_single_result(result)
    else:
        results = run_jobs_parallel(jobs, max_workers=workers, progress_callback=_progress)
        _print_group_summary(results)
```

#### C. Add `run-group` subcommand

```python
group_parser = subparsers.add_parser("run-group", help="Run all tasks in a category group")
group_parser.add_argument("--group", required=True,
    choices=["data_analysis", "strategy", "backtest", "implementation",
             "end_to_end", "debug", "adversarial"],
    help="Task category/group to run")
group_parser.add_argument("--persona", default=None, help="Single persona (omit for all)")
group_parser.add_argument("--agent", default="generic", ...)
group_parser.add_argument("--workers", type=int, default=3, help="Parallel workers")
group_parser.add_argument("--docker", action="store_true")
group_parser.add_argument("--max-turns", type=int, default=5)
group_parser.add_argument("--save-result", action="store_true")
# ... (same shared args as run-single)
```

```python
def cmd_run_group(args):
    """Run all tasks in a category group."""
    tasks = _load_tasks_by_group(args.group)
    persona_ids = [args.persona] if args.persona else None  # None = all

    jobs = []
    for task in tasks:
        pids = persona_ids or task.persona_ids
        for pid in pids:
            jobs.append(_build_job_spec(task, pid, args))

    print(f"=== run-group: {args.group} ({len(jobs)} jobs, {args.workers} workers) ===")
    results = run_jobs_parallel(jobs, max_workers=args.workers, progress_callback=_progress)
    _print_group_summary(results)
    if args.save_result:
        _save_group_summary(results, args.group)
```

#### D. Add `run-layer2` subcommand

```python
layer2_parser = subparsers.add_parser("run-layer2", help="Run all Layer 2 tasks")
# Same args as run-group minus --group

def cmd_run_layer2(args):
    """Run all Layer 2 tasks in parallel."""
    tasks = _load_all_layer2_tasks()  # all 17 tasks
    # ... same pattern as run-group
```

#### E. Update `run` command for L2 parallelism

```python
def cmd_run(args):
    """Run full benchmark (L1 sequential + L2 parallel)."""
    # Layer 1 (if requested): sequential, single-threaded
    if layer in ("all", "1"):
        _run_layer1(args)

    # Layer 2 (if requested): parallel via run_jobs_parallel()
    if layer in ("all", "2"):
        tasks = _load_all_layer2_tasks()
        jobs = _build_all_jobs(tasks, args)
        results = run_jobs_parallel(jobs, max_workers=args.workers)
        _aggregate_and_report(results)
```

#### F. Add `--workers` to all relevant subcommands

```python
# Add to: run-single, run-group, run-layer2, run
parser.add_argument("--workers", type=int, default=3,
    help="Max parallel workers (default: 3, each needs ~2 CPU + 4GB RAM)")
```

### 5.4 MODIFY: `bench/orchestrator/orchestrator.py`

#### A. `os.environ` guard for local mode

Add at the top of `run_single_task()`:
```python
# Safety: local mode uses os.environ for tool paths.
# This is NOT thread-safe — enforce single-worker in local mode.
# (Docker mode ignores these env vars; containers use fixed /data etc.)
```

No code change needed — the guard is in `run_benchmark.py` (refuse `--workers > 1` without `--docker`).

#### B. Refactor `run_benchmark()` to use `run_jobs_parallel()`

The existing `run_benchmark()` method (L382-475) will be updated to delegate to `run_jobs_parallel()` instead of its current sequential loop:

```python
def run_benchmark(self, agent_type, condition, ...) -> BenchmarkReport:
    """Run the full benchmark suite using parallel job runner."""
    tasks = self._discover_tasks(task_filter)
    jobs = []
    for task in tasks:
        for persona_id in task.persona_ids:
            if persona_filter and persona_id not in persona_filter:
                continue
            for trial in range(num_trials):
                jobs.append(JobSpec(...))

    results = run_jobs_parallel(jobs, max_workers=self.max_concurrent)
    return self._aggregate_report(results)
```

### 5.5 Helper Functions in `run_benchmark.py`

```python
def _find_and_load_task(task_id: str) -> QuantTutorTask:
    """Find a task JSON by ID across all category directories."""
    for category_dir in (BENCH_ROOT / "tasks" / "layer2").iterdir():
        if category_dir.is_dir():
            for f in category_dir.glob("*.json"):
                if task_id in f.stem:
                    with open(f) as fh:
                        return QuantTutorTask(**json.load(fh))
    raise SystemExit(f"Task not found: {task_id}")

def _load_tasks_by_group(group: str) -> list[QuantTutorTask]:
    """Load all tasks in a category group."""
    group_dir = BENCH_ROOT / "tasks" / "layer2" / group
    if not group_dir.is_dir():
        raise SystemExit(f"Group not found: {group}")
    tasks = []
    for f in sorted(group_dir.glob("*.json")):
        with open(f) as fh:
            tasks.append(QuantTutorTask(**json.load(fh)))
    return tasks

def _load_all_layer2_tasks() -> list[QuantTutorTask]:
    """Load all Layer 2 tasks across all categories."""
    tasks = []
    for category_dir in sorted((BENCH_ROOT / "tasks" / "layer2").iterdir()):
        if category_dir.is_dir():
            for f in sorted(category_dir.glob("*.json")):
                with open(f) as fh:
                    tasks.append(QuantTutorTask(**json.load(fh)))
    return tasks

def _progress(completed: int, total: int, latest: JobResult):
    """Print progress update."""
    status = "OK" if latest.error is None else f"ERR: {latest.error[:60]}"
    print(f"  [{completed}/{total}] {latest.job.task.task_id} × "
          f"{latest.job.persona.persona_id} — {status} "
          f"({latest.duration_seconds:.1f}s)")
```

### 5.6 Group/Layer Summary Report

```python
def _print_group_summary(results: list[JobResult]):
    """Print summary table after group/layer execution."""
    print("\n=== Summary ===")
    print(f"{'Task':<30} {'Persona':<25} {'OAS':>6} {'QR':>6} {'QP':>6} {'Time':>7} {'Status'}")
    print("-" * 110)

    total_time = 0
    total_cost = 0
    for r in results:
        if r.task_result:
            tr = r.task_result
            print(f"{tr.task_id:<30} {tr.persona_id:<25} "
                  f"{tr.overall_score:>6.4f} {tr.quant_result_score:>6.4f} "
                  f"{tr.quant_process_score:>6.4f} {r.duration_seconds:>6.1f}s OK")
            total_cost += tr.cost_usd
        else:
            print(f"{r.job.task.task_id:<30} {r.job.persona.persona_id:<25} "
                  f"{'—':>6} {'—':>6} {'—':>6} {r.duration_seconds:>6.1f}s ERR")
        total_time += r.duration_seconds

    print("-" * 110)
    print(f"Total: {len(results)} jobs, {total_time:.1f}s wall time, ${total_cost:.4f}")
```

## 6. Output Directory Structure

```
bench/results/
├── run-single/                              # Single task runs
│   └── {agent}/{model}/{task_id}/{persona_id}/
│       ├── scores.md
│       ├── trace.md
│       ├── cost.md
│       └── agent_files/
│
├── run-group/                               # Group runs
│   └── {agent}/{model}/{group}/
│       ├── summary.md                       # Group-level summary
│       └── {task_id}/{persona_id}/
│           ├── scores.md
│           ├── trace.md
│           ├── cost.md
│           └── agent_files/
│
├── run-layer2/                              # Full layer 2 runs
│   └── {agent}/{model}/
│       ├── summary.md                       # Layer-level summary
│       └── {group}/{task_id}/{persona_id}/
│           ├── scores.md
│           ├── trace.md
│           ├── cost.md
│           └── agent_files/
│
└── run/                                     # Full benchmark runs
    └── {agent}/{model}_{timestamp}/
        ├── report.json                      # Full benchmark report
        ├── layer1_results.json
        └── layer2/
            └── (same as run-layer2 structure)
```

## 7. Resource Constraints

| Resource | Per Job | 3 Workers | 6 Workers |
|----------|---------|-----------|-----------|
| Docker container | 2 CPU + 4GB RAM | 6 CPU + 12GB | 12 CPU + 24GB |
| OpenRouter API concurrency | ~15 req/s | ~45 req/s | ~90 req/s |
| Estimated eval cost | ~$0.50-2.00 | same | same |
| Estimated wall time | ~120-300s | ÷3 | ÷6 |

**Recommendation**: Default `--workers 3`. Most development machines have 8+ cores and 16+ GB RAM. OpenRouter rate limits are the practical bottleneck.

## 8. Safety Guards

1. **Local mode + workers > 1**: Raise error. `os.environ` mutations are not thread-safe.
2. **Docker not available + workers > 1**: Raise error.
3. **Worker count validation**: `min(workers, len(jobs))` — don't spawn more threads than jobs.
4. **Graceful error isolation**: One job's failure must not crash the pool. Each job catches exceptions and returns `JobResult(error=...)`.
5. **Container cleanup**: Each job's `run_single_task()` has a `finally` block that destroys its container. This is preserved.
6. **Ctrl+C handling**: `ThreadPoolExecutor` responds to `KeyboardInterrupt` — pending futures are cancelled, running jobs complete their current phase.

## 9. Implementation Order

| Step | Files | Description |
|------|-------|-------------|
| 1 | `job_runner.py` (NEW) | Extract `JobSpec`, `JobResult`, `run_single_job()` |
| 2 | `parallel_runner.py` (NEW) | `run_jobs_parallel()` with progress callback |
| 3 | `run_benchmark.py` | Make `--persona` optional, add `--workers`, refactor `cmd_run_single()` |
| 4 | `run_benchmark.py` | Add `cmd_run_group()` + argparse |
| 5 | `run_benchmark.py` | Add `cmd_run_layer2()` + argparse |
| 6 | `run_benchmark.py` | Update `cmd_run()` for L2 parallelism |
| 7 | `run_benchmark.py` | Add `_print_group_summary()`, `_save_group_summary()` |
| 8 | `orchestrator.py` | Add local-mode concurrency guard comment |
| 9 | Test | `run-single --task D01 --docker --save-result` (1 job, backward compat) |
| 10 | Test | `run-single --task D01 --docker --workers 3 --save-result` (3 personas) |
| 11 | Test | `run-group --group data_analysis --docker --workers 3 --save-result` |

## 10. Example CLI Usage

```bash
# Single task, single persona (existing behavior, unchanged)
python run_benchmark.py run-single --task D01_load_inspect_ohlcv \
  --persona beginner_no_finance --agent openai --docker --max-turns 3 --save-result

# Single task, all 3 personas in parallel
python run_benchmark.py run-single --task D01_load_inspect_ohlcv \
  --agent openai --docker --max-turns 3 --workers 3 --save-result

# All data_analysis tasks × all personas
python run_benchmark.py run-group --group data_analysis \
  --agent openai --docker --workers 3 --save-result

# All Layer 2 tasks × all personas
python run_benchmark.py run-layer2 \
  --agent openai --docker --workers 3 --save-result

# Full benchmark (L1 + L2)
python run_benchmark.py run \
  --agent openai --docker --workers 3 --layer all
```

## 11. Expected Terminal Output

### `run-single` (no persona, 3 workers)

```
=== QuantTutorBench - Single Task ===
Task: D01_load_inspect_ohlcv
Agent: openai (gpt-5.2)
Condition: agent — Full agent with tools
Workers: 3 (parallel personas)

Running 3 jobs...
  [1/3] D01_load_inspect_ohlcv × beginner_no_finance — OK (142.3s)
  [2/3] D01_load_inspect_ohlcv × advanced_quant — OK (128.7s)
  [3/3] D01_load_inspect_ohlcv × intermediate_developer — OK (135.1s)

=== Summary ===
Task                           Persona                      OAS     QR     QP    Time Status
--------------------------------------------------------------------------------------------------------------
D01_load_inspect_ohlcv         beginner_no_finance        0.7234 0.8100 0.6500  142.3s OK
D01_load_inspect_ohlcv         intermediate_developer     0.7512 0.7900 0.7100  135.1s OK
D01_load_inspect_ohlcv         advanced_quant             0.7891 0.8300 0.7400  128.7s OK
--------------------------------------------------------------------------------------------------------------
Total: 3 jobs, 142.3s wall time (3× parallel), $1.2345
Results saved: bench/results/run-single/openai/gpt-5.2/D01_load_inspect_ohlcv/
```

### `run-group --group data_analysis`

```
=== QuantTutorBench - Group: data_analysis ===
Tasks: 11 | Personas: 3 each | Jobs: 33 | Workers: 3

  [1/33]  D01_load_inspect_ohlcv × beginner_no_finance — OK (142.3s)
  [2/33]  D02_missing_data_detection × advanced_quant — OK (128.7s)
  ...
  [33/33] D11_realtime_data_fetch × intermediate_developer — OK (95.2s)

=== Summary ===
...
Total: 33 jobs, 1245.6s wall time (÷3 = ~415s actual), $18.4567
Results saved: bench/results/run-group/openai/gpt-5.2/data_analysis/
```
