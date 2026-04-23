# 评估管道架构设计 v2

> **创建日期**: 2026-04-22
> **相关文档**: `qp_qr_restructuring_plan.md`（QP/QR 维度变更、rubric 摘要、批量验证计划）
> **本文档范围**: 评估管道的完整架构设计。覆盖 CLI/REST 统一入口、三轨隔离、LLM 调用容错、结果持久化、中断处理。**不包含**具体 rubric 文本。

---

## 0. 核心设计原则

1. **诚实评分**：宁可报错也不输出假分数。不得用默认分、正则抽分、半解析响应参与评分；任何无法可靠解析的 LLM 响应都应导致该维度 `failed`，并阻断其所属总分计算。
2. **三轨隔离**：QR、QP、Tutor 各自独立运行、独立失败、独立报告。一轨崩溃不拖累其他两轨。
3. **入口统一**：CLI 和 REST 共享同一套参数解析、结果查找、score run 创建逻辑。入口差异仅是参数来源与返回格式。
4. **结果完整**：LLM judge 返回的 evidence、reason、score 全链路保留，持久化到 JSON，可机器读取。
5. **追加式评分**：同一个 session 可有多次评估结果，按 `score_1`、`score_2`、... 追加保存；任何新评估都不覆盖旧评估。

---

## 1. 统一入口层

### 1.1 评估创建参数（CLI 与 REST 共享）

```
                    ┌─────────────────────────────────────┐
                    │        EvalRequest (dataclass)       │
                    ├─────────────────────────────────────┤
                    │  session_id: str                     │  ← 唯一主键（必填）
                    │  eval_mode:  "full"|"qr"|"qp"|"tutor"│
                    │  tutor_dims: list[str] | None        │  ← None=全部6D
                    │  eval_model: str = config_default    │
                    │  idempotency_key: str | None         │  ← 可选，防网络重试重复创建
                    └─────────────────────────────────────┘
```

**session_id 是唯一查找键**。CLI 和 REST 用完全相同的方式——通过 session_id 定位结果目录。不提供 task_id / persona_id / 路径等替代查询方式。

**创建语义**：`EvalRequest` 总是请求创建一次新的评分 run（`score_n`）。没有任何历史评分时创建 `score_1`；已有 completed/failed/interrupted 评分时创建 `score_{n+1}`；若当前已有 running 评分，则返回该 running score 的状态，不并发创建第二个 running score。

**参数解析共享**：`eval_request.py` 提供 `parse_eval_request()` 函数，同时被 CLI 的 argparse 和 REST 的 query params 调用。输出统一的 `EvalRequest` 对象。

### 1.2 评分读取参数（GET / CLI get）

```
                    ┌─────────────────────────────────────┐
                    │        ScoreQuery (dataclass)       │
                    ├─────────────────────────────────────┤
                    │  session_id: str                     │
                    │  score_id: str | None                │  ← "score_3" 或 "latest"
                    │  score_ids: list[str] | None         │  ← 指定多次评分
                    │  history: bool = False               │  ← 只返回 index
                    │  status_filter: list[str] | None     │
                    └─────────────────────────────────────┘
```

**读取语义**：GET 和 CLI get 是纯读取，不触发 LLM 调用、不创建新评分、不修改 `index.json`。默认读取最新 completed score；若没有 completed score，则返回最新 running/failed/interrupted 状态。

### 1.3 结果目录查找（session_id → Path）

session_id 支持两种输入形式：完整 32 位或短 12 位前缀。

```
resolve_result_dir(session_id: str, results_root: Path) -> Path

逻辑：
  short_id = session_id[:12]

  1. 扫描 results_root 下所有目录:
     results_root/{task_id}/{persona_id}/{ts}_{short_id}/

  2. 匹配规则: 目录名以 f"_{short_id}" 结尾

  3. 精确校验（防碰撞）:
     ・目录下必须有 .session_id 文件
     ・读取 .session_id，精确比对完整 session_id 或前缀
     ・比对失败 → 跳过，继续扫描

  4. 结果:
     ・恰好 1 个匹配 → 返回该 Path
     ・0 个匹配 → raise EvalError("No result found for session {short_id}")
     ・多个匹配 → raise EvalError("Ambiguous: {N} results match {short_id}")
       （理论上 12 位 hex = 2^48 种组合，碰撞概率极低）
```

### 1.4 score_n 分配规则

```
allocate_score_run(result_dir: Path, request: EvalRequest) -> ScoreRun

逻辑:
  1. 对 result_dir/evaluations/.lock 加文件锁
  2. 读取或初始化 evaluations/index.json
  3. 若存在 status="running" 的 score:
     ・若 idempotency_key 匹配 → 返回该 score
     ・若无 idempotency_key 或不匹配 → 返回 "already running" 状态，不创建新 score
  4. 否则:
     ・score_id = f"score_{next_score_number}"
     ・next_score_number += 1
     ・创建 evaluations/{score_id}/
     ・index 中写入 status="running" + request metadata
  5. 释放锁
```

编号一旦分配，不复用。失败、中断、不可计算的评分也保留对应 `score_n`。

### 1.5 两种入口的对称关系

```
┌─ CLI ──────────────────────────────────────────────────────────────┐
│                                                                    │
│  # 用完整 session_id                                               │
│  python -m server.scripts.eval_single \                            │
│    --session d5aea2395b6a \                                        │
│    --mode tutor --tutor-dims D3,D4                                 │
│                                                                    │
│  # 默认 full mode                                                  │
│  python -m server.scripts.eval_single --session d5aea2395b6a       │
│                                                                    │
│  1. argparse → parse_eval_request() → EvalRequest                  │
│  2. resolve_result_dir(request.session_id, local_results_root)     │
│  3. allocate_score_run(result_dir, request) → score_id             │
│  4. eval_coordinator.run(result_dir, score_id, request) → EvalOutput│
│  5. print(output.to_summary())                                     │
│                                                                    │
│  # 读取历史评分                                                    │
│  python -m server.scripts.eval_single get \                        │
│    --session d5aea2395b6a --score score_2                          │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘

┌─ REST ─────────────────────────────────────────────────────────────┐
│                                                                    │
│  POST /session/{sid}/evaluate?eval_mode=tutor&tutor_dims=D3,D4     │
│                                                                    │
│  1. path_param + query_params → parse_eval_request() → EvalRequest │
│  2. resolve_result_dir(request.session_id, server_results_root)    │
│  3. allocate_score_run(result_dir, request) → score_id             │
│  4. eval_coordinator.run(result_dir, score_id, request) → EvalOutput│
│  5. return JSONResponse(output.to_summary())                       │
│                                                                    │
│  GET /session/{sid}/scores?score=score_2                           │
│  GET /session/{sid}/scores?history=true                            │
│  GET /session/{sid}/scores?scores=score_1,score_3                  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘

差异仅有:
  ・参数来源:  argparse vs query_params (path_param 中的 {sid})
  ・数据位置:  local_results_root vs server_results_root (未来 S3)
  ・输出格式:  stdout vs JSONResponse
  ・读取方式:  CLI get 子命令 vs REST GET /scores

CLI 便捷命令（列出所有可评估的 session）:
  python -m server.scripts.eval_single --list
  输出:
    SESSION_ID    TASK              PERSONA               STATUS
    d5aea2395b6a  D05_return_comp   double_novice         pending
    674546a746bc  D05_return_comp   fullstack_practitioner pending
    e7d78f7e21bf  D03_data_type_c   developer_crossover   pending
    ...
```

---

## 2. 前置检查层（Preflight）

在 `eval_coordinator.run()` 执行前做统一前置检查，但区分两类失败:

- **Global hard failure**：基础会话数据不可用，整次 score run 标记为 `failed`，不进入评估流程。
- **Per-track / per-dimension failure**：只影响对应 track 或维度，写入 `blocking_missing`，不阻止其他 track 运行。

```
preflight_check(result_dir: Path, request: EvalRequest) -> PreflightResult

检查项:
  ┌─ Global hard（所有 mode）───────────────────────────────┐
  │  ① run_state.json 存在且可解析                           │
  │  ② task JSON 存在且可加载为 QuantTutorTask                │
  │  ③ persona JSON 存在且可加载为 StudentPersona             │
  │  ④ conversation 非空（至少 1 轮 user + 1 轮 assistant）  │
  └──────────────────────────────────────────────────────────┘
  ┌─ Per-track / per-dimension ──────────────────────────────┐
  │  ⑤ tool_logs 字段完整性                                  │
  │     每条 log 必须有: name, args, success, turn_index      │
  │  ⑥ eval_script 路径存在（若 ground_truth.quant_validation │
  │    有定义）                                               │
  │  ⑦ OPENROUTER_API_KEY 已设置且非空                       │
  │  ⑧ eval_model 可解析为有效 provider/model                │
  │  ⑨ tutor_dims 中每个维度名在 DIMENSIONS 常量中存在        │
  │  ⑩ 该 persona_id 在 rubric variant applies_to 中有匹配   │
  └──────────────────────────────────────────────────────────┘

返回:
  hard_errors: list[str]           ← 非空则 score.status="failed"
  track_blockers: dict[str, list]  ← 对应 track.score=None / overall_score=None
  skipped_dependencies: dict       ← reference_unavailable 等非阻断跳过项
```

---

## 3. 评估协调器（Eval Coordinator）

替代当前 `pipeline.evaluate_task()` 的 God Function。

```
eval_coordinator.run(
    result_dir: Path,
    score_id: str,
    request: EvalRequest,
    cancel_event: threading.Event | None = None,
) -> EvalOutput

职责清单:
  1. 加载数据: run_state.json → task, persona, conversation, tool_logs
  2. 预计算共享数据: enriched_conversation（QP + Tutor 共用）
  3. 根据 eval_mode 决定跑哪些 track
  4. 并行分发 track（每个 track 有独立 cancel_event + 独立 error）
  5. 收集 TrackResult，组装 EvalOutput
  6. 计算 overall_score（通过 scoring.py）
  7. 持久化: evaluations/{score_id}/score.json + cost.json
  8. 更新 evaluations/index.json 中该 score 的状态

NOT 做的事:
  ✗ 不做 QR blending（在 qr_track 内部）
  ✗ 不做 QP 权重聚合（在 qp_track 内部）
  ✗ 不做 Tutor 维度加权（在 tutor_track 内部）
  ✗ 不做 LLM 调用（在各 track 内部）
```

### 3.1 Track 并行与隔离

```
                         eval_coordinator
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ QR Track │   │ QP Track │   │Tutor Track│
        │          │   │          │   │           │
        │cancel_qr │   │cancel_qp │   │cancel_tut │ ← 三个独立 Event
        │error_qr  │   │error_qp  │   │error_tut  │ ← 独立错误状态
        └────┬─────┘   └────┬─────┘   └────┬──────┘
             │              │              │
             ▼              ▼              ▼
        TrackResult    TrackResult    TrackResult
        (或 error)     (或 error)     (或 error)

规则:
  ・QR 崩 → QR.error="xxx", QP/Tutor 正常完成
  ・Tutor 崩 → Tutor.error="xxx", QR/QP 正常完成
  ・SIGINT → 三个 cancel_event 全部 set
  ・三个 thread 用 ThreadPoolExecutor(max_workers=3)
  ・每个 thread 内部的 LLM 并行用 asyncio（已有的 guarded_gather）
```

### 3.2 中断处理（SIGINT / cancel_event）

```
signal.signal(SIGINT):
  │
  ├── 第一次 SIGINT:
  │   ├── 设置 coordinator 级别的 interrupted=True
  │   ├── 设置三个 track 的 cancel_event
  │   ├── 打印 "Interrupted. Waiting for in-flight LLM calls (5s grace)..."
  │   ├── 等待 ThreadPoolExecutor 完成（timeout=5s）
  │   ├── 收集已完成的 TrackResult
  │   └── 保存 interrupted 结果:
  │       EvalOutput(interrupted=True, ...)
  │       index.json 中 score.status = "interrupted"
  │       score.json 正常写入已有结果
  │       cost.json 写入已产生的评分成本
  │
  └── 第二次 SIGINT (grace period 内):
      └── 立即 sys.exit(1)，不保存

状态转换:
  pending → running → completed_scored          (正常且总分可计算)
  pending → running → completed_not_computable  (完成但 required 维度缺失)
  pending → running → interrupted               (SIGINT 中断)
  pending → running → failed                    (异常)
  interrupted/failed/not_computable 不会被覆盖；再次 POST 创建新的 score_n
```

---

## 4. Track 评估器

### 4.1 QR Track

```
qr_track.evaluate(
    task, workspace_path, tool_logs, conversation,
    eval_model, cancel_event, reference
) -> TrackResult

内部流程:
  ┌─ Step 1: Programmatic Eval ──────────────────────────────────┐
  │  eval_script → module.evaluate(workspace, logs, conv)        │
  │  结果: programmatic_score (float | None)                     │
  │  错误处理: Exception → status="failed" + blocking_missing     │
  │  若任务没有 quant_validation → status="skipped", 不阻断       │
  └──────────────────────────────────────────────────────────────┘
  ┌─ Step 2: Code Eval ─────────────────────────────────────────┐
  │  evaluate_code_combined(workspace, logs, reference, ...)     │
  │  结果: {score, applicable, layers: {A, B, C}}               │
  │  错误处理: Exception → status="failed" + blocking_missing     │
  │  仅 reference 相关层缺失 → status="skipped", 不阻断           │
  └──────────────────────────────────────────────────────────────┘
  ┌─ Step 3: LLM Result Judge ──────────────────────────────────┐
  │  多模型并行 → 跨模型平均                                     │
  │  结果: {score, reason, evidence, per_model: {...}}           │
  │  错误处理: 见 §5 LLM 容错                                    │
  └──────────────────────────────────────────────────────────────┘
  ┌─ Step 4: QR Blending ───────────────────────────────────────┐
  │  required component 全部 success 后才执行                     │
  │  dampening sigmoid + 三层权重                                │
  │  结果: final_qr_score + blend_weights                        │
  └──────────────────────────────────────────────────────────────┘

输出:
  TrackResult(
    track="qr",
    score=0.75,
    status="success",
    blocking_missing=[],
    detail={
      "programmatic": {"score": 0.8, "status": "success", "detail": {...}},
      "code_eval": {"score": 0.7, "status": "success", "applicable": true, ...},
      "result_judge": {
        "score": 0.72,
        "status": "success",
        "required_for_track_score": true,
        "reason": "...",
        "evidence": ["quote1", "quote2"],
        "per_model": {
          "anthropic/claude-sonnet-4-6": {
            "score": 0.75, "reason": "...", "evidence": [...]
          },
          ...
        }
      },
      "blend_weights": {"programmatic": 0.25, "code_eval": 0.30, "judge": 0.45}
    },
    eval_cost=0.003,
    eval_cost_by_model={...},
    error=None,
  )
```

### 4.2 QP Track

```
qp_track.evaluate(
    task, tool_logs, enriched_conversation,
    eval_model, cancel_event,
    reference, tool_usage_result,
    category, task_requires_code, is_adversarial,
    required_capabilities
) -> TrackResult

内部流程 (5 维度):
  ┌─ Programmatic (不需要 LLM，先同步跑) ───────────────────────┐
  │  tool_usage     (0.20) — 数学公式                            │
  │  action_economy (0.15) — step ratio 阶梯                     │
  │  code_lifecycle (0.15) — 4 子指标                             │
  └──────────────────────────────────────────────────────────────┘
  ┌─ LLM-based (多模型×维度 并行) ──────────────────────────────┐
  │  task_planning    (0.25) — always                            │
  │  problem_solving  (0.25) — only when has_explicit_errors()   │
  │                                                              │
  │  每个返回: {score, reason, evidence, per_model: {...}}       │
  │  错误处理: 见 §5 LLM 容错                                    │
  └──────────────────────────────────────────────────────────────┘

聚合:
  ・required 维度 failed → QP.score=None，不计算 aggregate
  ・reference_unavailable / not_applicable / no_explicit_errors → skipped，不进入分母
  ・其余 success 维度按权重重归一化
  aggregate = Σ(weight_i × score_i) / Σ(weight_i)  # only success + required/skipped 规则后

输出:
  TrackResult(
    track="qp",
    score=0.79,       ← aggregate over success dims
    detail={
      "tool_usage":     {"score": 0.85, "breakdown": {...}},
      "action_economy": {
        "score": null,
        "status": "skipped",
        "skip_reason": "reference_unavailable",
        "required_for_track_score": false
      },
      "code_lifecycle": {
        "score": null,
        "status": "skipped",
        "skip_reason": "not_applicable",
        "required_for_track_score": false
      },
      "task_planning":  {
        "score": 0.75, "status": "success", "reason": "...", "evidence": [...],
        "per_model": {m: {"score": ..., "reason": ..., "evidence": [...]}}
      },
      "problem_solving": {
        "score": null,
        "status": "skipped",
        "skip_reason": "no_explicit_errors",
        "required_for_track_score": false
      },
      "_weights_used": {"tool_usage": 0.20, "action_economy": 0.15, ...},
      "_weights_effective": {"tool_usage": 0.444, "task_planning": 0.556},  ← skipped 维度排除后
      "_blocking_missing": []
    },
    eval_cost=0.005,
    eval_cost_by_model={...},
    error=None,
  )
```

### 4.3 Tutor Track

```
tutor_track.evaluate(
    conversation, enriched_conversation,
    persona_id, scenario, user_description,
    eval_model, cancel_event,
    category, requires_code,
    dimension_filter
) -> TrackResult

内部流程:
  1. 按 CATEGORY_DIMENSION_WEIGHTS 过滤活跃维度 (weight > 0)
  2. 与 dimension_filter 取交集 (None = 不过滤)
  3. 构建 (维度 × 模型) 任务矩阵
  4. 并行调用 EwanConvGEval（共享 concurrency semaphore）
  5. 每个维度: 跨模型平均
  6. 加权聚合 tutor_score

输出:
  TrackResult(
    track="tutor",
    score=0.72,       ← tutor aggregate
    detail={
      "D1_finance_adaptation": {
        "score": 0.75,
        "status": "success",
        "reason": "...",
        "evidence": ["quote1", "quote2"],
        "per_model": {
          "anthropic/claude-sonnet-4-6": {
            "score": 0.75, "reason": "...", "evidence": [...]
          }
        }
      },
      "D3_pedagogical_method": {同上},
      "D4_instructional_accuracy": {同上},
      ...
      "_weights_used": {"D1": 1, "D2": 0, "D3": 1, ...},
      "_blocking_missing": [],
    },
    eval_cost=0.012,
    eval_cost_by_model={...},
    error=None,
  )

未请求的维度 (因 category weight=0 或 dimension_filter 排除):
  不出现在 detail 中。
  _weights_used 中标为 0。
  前端读取 score.json 时显示 "N/A (not evaluated for this category)"。
```

---

## 5. LLM 调用容错（统一）

### 5.1 当前问题

当前 Tutor 有 3 层 fallback，其中 Layer 2 (`_fallback_direct_eval`) 会在 JSON 解析失败时注入 `max_score // 2` 作为默认分数；部分方案还会尝试从 malformed prose 中正则抽取分数。这两类行为都违反"诚实评分"原则：解析失败就是该维度失败，不能把不可靠文本转成可聚合分数。

### 5.2 统一容错策略

```
所有 LLM judge 维度（Tutor D1-D6、task_planning、problem_solving、result_judge）
共享同一套容错逻辑:

┌─ a_measure() (EwanConvGEval) ──────────────────────────────────────┐
│                                                                     │
│  LLM call → response_text                                          │
│       │                                                             │
│       ▼                                                             │
│  extract_json_from_response(response_text) → parsed                 │
│       │                                                             │
│       ├─ "score" in parsed → 正常路径                               │
│       │   score, reason, evidence 全部提取                          │
│       │   normalize: (score-1)/(max_score-1)                        │
│       │   return score                                              │
│       │                                                             │
│       └─ "score" not in parsed → 解析失败                           │
│           存储 _raw_failed_response                                  │
│           raise ValueError("Invalid JSON")                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                         │ (异常)
                         ▼
┌─ llm_call_with_retry() (共享包装函数) ─────────────────────────────┐
│                                                                     │
│  Layer 0: Retry（最多 3 次，仅限 JSON 解析失败）                     │
│       │                                                             │
│       └─ 3 次全部失败                                               │
│           │                                                         │
│           ▼                                                         │
│  Layer 1: 标记失败（不抽分、不注入假分数）                            │
│       返回 DimensionResult(                                         │
│         score=None,                                                 │
│         status="failed",                                            │
│         required_for_track_score=True,                              │
│         error="LLM JSON parse failed after 3 retries",              │
│         diagnostics={"raw_response_excerpt": response_text[:500]}   │
│       )                                                             │
│                                                                     │
│  ✗ 删除: _fallback_direct_eval() 及其 max_score//2 默认值注入       │
│  ✗ 删除: fallback_extract / regex score extraction                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

关键变更:
  1. 删除 Layer 2 的 _fallback_direct_eval()（消灭假分数注入点）
  2. 删除 fallback_extract（不从 prose / regex 抽取可聚合分数）
  3. 最终失败返回 score=None + status="failed"
  4. required 维度 failed 会使所属 track.score=None
  5. 任一请求 track.score=None 会使 overall_score=None
  6. QR/QP/Tutor 共享此逻辑（消除三种不同的错误处理路径）
```

### 5.3 失败维度的处理

```
当某个 LLM 维度返回 score=None (解析失败):

Tutor Track:
  ・该维度 status="failed"
  ・该维度 required_for_track_score=True
  ・Tutor 子维度仍完整写入 score.json 供展示
  ・Tutor TrackResult.score=None
  ・EvalOutput.overall_score=None（若 eval_mode 请求了 Tutor）

QP Track:
  ・task_planning failed → QP TrackResult.score=None
  ・problem_solving 仅在有显式错误触发时 required；触发后 failed → QP TrackResult.score=None
  ・problem_solving 无触发点 → status="skipped", required_for_track_score=False，不阻断聚合

QR Track:
  ・result_judge failed → QR TrackResult.score=None
  ・programmatic/code_eval 可继续展示，但不单独顶替 result_judge 产出 QR 总分

Reference 相关缺失:
  ・reference_trace / reference key_results 暂时不是 required dependency
  ・依赖 reference 的维度若缺 reference → status="skipped",
    required_for_track_score=False, skip_reason="reference_unavailable"
  ・此类 skipped 不阻断所属 track 聚合，也不计 0 分
```

---

## 6. 数据模型

### 6.1 DimensionResult

```python
@dataclass
class DimensionResult:
    score: float | None               # None = skipped 或 failed
    status: str                       # success | skipped | failed
    required_for_track_score: bool    # True 且 failed → track.score=None
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    per_model: dict = field(default_factory=dict)
    error: str | None = None
    skip_reason: str | None = None    # reference_unavailable | not_applicable | no_explicit_errors | ...
    diagnostics: dict = field(default_factory=dict)
```

### 6.2 TrackResult

```python
@dataclass
class TrackResult:
    track: str                        # "qr" | "qp" | "tutor"
    score: float | None               # None = 有 required missing，track 不可计算
    status: str                       # success | not_computable | failed | skipped
    detail: dict                      # 全部子维度 + evidence + reason
    blocking_missing: list[dict] = field(default_factory=list)
    eval_cost: float = 0.0
    eval_cost_by_model: dict = field(default_factory=dict)
    error: str | None = None
    duration_seconds: float = 0.0
```

### 6.3 EvalOutput

```python
@dataclass
class EvalOutput:
    score_id: str                     # "score_1" | "score_2" | ...
    score_status: str                 # completed_scored | completed_not_computable | failed | interrupted
    qr: TrackResult | None            # None = 未请求（eval_mode 不含 QR）
    qp: TrackResult | None
    tutor: TrackResult | None

    overall_score: float | None       # None = 请求的 track 中存在 blocking missing
    eval_mode: str                    # "full" | "qr" | "qp" | "tutor"
    eval_model: str
    created_at: str                   # ISO 8601
    completed_at: str | None
    duration_seconds: float
    interrupted: bool = False
    blocking_missing: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """完整序列化（含 evidence）→ score.json"""
        ...

    def to_summary(self) -> dict:
        """摘要（分数 + 错误）→ API 返回 / CLI stdout"""
        return {
            "score_id": self.score_id,
            "score_status": self.score_status,
            "quant_result": self.qr.score if self.qr else None,
            "quant_process": self.qp.score if self.qp else None,
            "tutor_score": self.tutor.score if self.tutor else None,
            "overall_score": self.overall_score,
            "eval_mode": self.eval_mode,
            "blocking_missing": self.blocking_missing,
            "interrupted": self.interrupted,
        }
```

### 6.4 Overall Score 计算

```
scoring.compute_overall(qr: TrackResult | None,
                        qp: TrackResult | None,
                        tutor: TrackResult | None,
                        eval_mode: str) -> float | None

规则:
  前置:
    ・未请求的 track 不参与计算
    ・请求的 track 若 score=None → overall_score=None
    ・overall_score=None 时，不进入 benchmark KPI / leaderboard 聚合

  full mode:
    quant = 0.50 * qr.score + 0.50 * qp.score
    overall = 0.70 * quant + 0.30 * tutor.score

  qr mode:   overall = qr.score
  qp mode:   overall = qp.score
  tutor mode: overall = tutor.score

  不存在 "missing track 贡献 0" 的规则；缺失导致不可计算，而不是低分。
```

---

## 7. 持久化

### 7.1 目录结构

```
results/server/{task_id}/{persona_id}/{ts}_{session_id[:12]}/
  run_state.json          ← 会话数据 + 学生模拟器成本（唯一 run state 文件）
  agent_files/            ← 工作区文件（不变）
  .session_id             ← 完整 32 位 session_id（新增，用于精确匹配）
  evaluations/
    index.json            ← score_n 索引、状态、请求参数、时间戳
    .lock                 ← score_n 分配锁
    score_1/
      score.json          ← EvalOutput.to_dict()（完整机器可读评分）
      cost.json           ← 本次 eval 成本明细（仅评分成本）
    score_2/
      score.json
      cost.json
```

不保存任何 Markdown 文件。`run_state.md`、`scores.md`、`trace.md`、`cost.md` 全部删除；前端展示直接读取 JSON 并渲染。

### 7.2 index.json 结构

```json
{
  "version": "2.0",
  "next_score_number": 3,
  "latest_completed_score_id": "score_1",
  "scores": [
    {
      "score_id": "score_1",
      "status": "completed_scored",
      "eval_mode": "full",
      "eval_model": "anthropic/claude-sonnet-4-6",
      "tutor_dims": null,
      "created_at": "2026-04-22T14:30:55",
      "completed_at": "2026-04-22T14:31:40",
      "overall_score": 0.76,
      "score_path": "score_1/score.json",
      "cost_path": "score_1/cost.json"
    },
    {
      "score_id": "score_2",
      "status": "running",
      "eval_mode": "tutor",
      "eval_model": "anthropic/claude-sonnet-4-6",
      "tutor_dims": ["D3_pedagogical_method", "D4_instructional_accuracy"],
      "created_at": "2026-04-22T15:00:00",
      "completed_at": null,
      "overall_score": null,
      "score_path": "score_2/score.json",
      "cost_path": "score_2/cost.json"
    }
  ]
}
```

### 7.3 score.json 结构

```json
{
  "version": "2.0",
  "score_id": "score_1",
  "score_status": "completed_scored",
  "created_at": "2026-04-22T14:30:55",
  "completed_at": "2026-04-22T14:31:40",
  "eval_model": "anthropic/claude-sonnet-4-6",
  "eval_mode": "full",
  "duration_seconds": 45.2,
  "interrupted": false,
  "blocking_missing": [],
  "overall_score": 0.76,

  "qr": {
    "track": "qr",
    "status": "success",
    "score": 0.75,
    "error": null,
    "blocking_missing": [],
    "duration_seconds": 12.3,
    "eval_cost": 0.003,
    "eval_cost_by_model": {"anthropic/claude-sonnet-4-6": 0.003},
    "detail": {
      "programmatic": {"score": 0.8, "detail": {"...eval script output..."}},
      "code_eval": {
        "score": 0.7,
        "status": "success",
        "applicable": true,
        "layers": {
          "A": {"score": 0.9, "status": "success"},
          "B": {"score": 0.6, "status": "success"},
          "C": {
            "score": null,
            "status": "skipped",
            "skip_reason": "reference_unavailable",
            "required_for_track_score": false
          }
        }
      },
      "result_judge": {
        "score": 0.72,
        "status": "success",
        "required_for_track_score": true,
        "reason": "Agent correctly computed returns but missed log return aggregation.",
        "evidence": ["'Here are the simple returns computed using pct_change'", "'log returns were not aggregated'"],
        "per_model": {
          "anthropic/claude-sonnet-4-6": {
            "score": 0.72,
            "reason": "...",
            "evidence": ["...", "..."]
          }
        }
      },
      "blend_weights": {"programmatic": 0.25, "code_eval": 0.30, "judge": 0.45}
    }
  },

  "qp": {
    "track": "qp",
    "status": "success",
    "score": 0.79,
    "error": null,
    "blocking_missing": [],
    "detail": {
      "tool_usage": {"score": 0.85, "breakdown": {"selection": 0.9, "effectiveness": 0.8}},
      "action_economy": {
        "score": null,
        "status": "skipped",
        "skip_reason": "reference_unavailable",
        "required_for_track_score": false
      },
      "code_lifecycle": {
        "score": null,
        "status": "skipped",
        "skip_reason": "not_applicable",
        "required_for_track_score": false
      },
      "task_planning": {
        "score": 0.75,
        "status": "success",
        "required_for_track_score": true,
        "reason": "Clear decomposition of return computation into steps.",
        "evidence": ["'Let me break this into three parts'", "'First compute simple returns'"],
        "per_model": {"...": {"score": 0.75, "reason": "...", "evidence": ["..."]}}
      },
      "problem_solving": {
        "score": null,
        "status": "skipped",
        "skip_reason": "no_explicit_errors",
        "required_for_track_score": false
      },
      "_weights_used": {"tool_usage": 0.20, "action_economy": 0.15, "code_lifecycle": 0.15, "task_planning": 0.25, "problem_solving": 0.25},
      "_weights_effective": {"tool_usage": 0.444, "task_planning": 0.556}
    }
  },

  "tutor": {
    "track": "tutor",
    "status": "success",
    "score": 0.72,
    "error": null,
    "blocking_missing": [],
    "detail": {
      "D1_finance_adaptation": {
        "score": 0.75,
        "status": "success",
        "required_for_track_score": true,
        "reason": "Tutor correctly identified unknown concepts and scaffolded explanations.",
        "evidence": ["'Since you mentioned you are new to returns...'", "'Let me explain pct_change step by step'"],
        "per_model": {"...": {"score": 0.75, "reason": "...", "evidence": ["..."]}}
      },
      "D3_pedagogical_method": {"...同上结构..."},
      "_weights_used": {"D1": 1, "D2": 0, "D3": 1, "D4": 1, "D5": 1, "D6": 0},
      "_blocking_missing": []
    }
  }
}
```

### 7.4 cost.json 结构

```json
{
  "version": "2.0",
  "score_id": "score_1",
  "eval_cost_usd": 0.020,
  "eval_cost_by_track": {
    "qr": 0.003,
    "qp": 0.005,
    "tutor": 0.012
  },
  "eval_cost_by_model": {
    "anthropic/claude-sonnet-4-6": 0.020
  },
  "eval_cost_by_stage_model": {
    "QR Result Judge": {"anthropic/claude-sonnet-4-6": 0.003},
    "QP Task Planning": {"anthropic/claude-sonnet-4-6": 0.005},
    "Tutor 6D": {"anthropic/claude-sonnet-4-6": 0.012}
  }
}
```

### 7.5 前端 cost 展示规则

```
前端读取:
  ・run_state.json:
      simulator_cost
      tc_checker_cost
      duration_seconds
      agent/session 相关成本
  ・evaluations/{score_id}/cost.json:
      eval_cost_usd
      eval_cost_by_track
      eval_cost_by_model

展示:
  total_cost = run_state.simulator_cost + cost.eval_cost_usd
  student_simulator_cost = run_state.simulator_cost
  tc_checker_cost = run_state.tc_checker_cost   # 单独展示，不并入学生模拟器 + 评分总成本
  evaluation_cost = cost.eval_cost_usd

注意:
  ・cost.json 只保存本次评分成本，不复制 run_state 中的学生模拟器成本
  ・最终总成本在读取/展示层拼接计算，不落盘为第三份重复数据
```

---

## 8. expected_outcome 清理

彻底移除所有代码路径中的 `expected_outcome` (EO) 引用。

```
删除:
  server/eval/ewan_eval/result_judge.py:156
    - expected_outcome: str | None = None,       ← 删除参数
  server/eval/ewan_eval/tutor_conv_geval.py:283
    - expected_outcome: Optional[str] = None,    ← 删除参数
  server/scripts/eval_tutor_dims.py:98
    - expected_outcome = task.ground_truth...     ← 删除变量
    - expected_outcome=expected_outcome,          ← 删除传参

保留:
  task JSON 文件中的 expected_outcome 字段    ← 数据层不改
  schemas.py GroundTruth.expected_outcome     ← 模型层保留（数据兼容）
```

---

## 9. Session ID 保存 / 查找 / 迁移

### 9.1 保存端变更

```
session_api.py _save_results():

  现状:
    dir_name = f"{ts}_{self.session_id[:8]}"

  改为:
    dir_name = f"{ts}_{self.session_id[:12]}"

  同时新增: 保存完成后写入 .session_id 文件
    path: result_dir / ".session_id"
    内容: 完整 32 位 session_id（纯文本，单行，无换行）
    用途: resolve_result_dir 精确校验，避免碰撞
```

涉及修改的文件:
- `session_api.py:1080` — `[:8]` → `[:12]`
- `result_writer.py` — 只保存 `run_state.json`，不再生成 `run_state.md`
- `http_app.py:569,583,602` — docstring + `find_archived_result_dir` 中的 `[:8]` → `[:12]`
- `import_old_results.py` — 删除或仅作为一次性迁移脚本使用，运行时代码不再依赖旧格式

### 9.2 查找端变更

`resolve_result_dir()` 提取为 `eval_request.py` 中的独立函数，CLI 和 REST 共享:

```python
def resolve_result_dir(session_id: str, results_root: Path) -> Path:
    """通过 session_id 定位结果目录。

    支持完整 32 位或短 12 位输入。
    """
    short_id = session_id[:12]
    candidates = []

    for task_dir in results_root.iterdir():
        if not task_dir.is_dir():
            continue
        for persona_dir in task_dir.iterdir():
            if not persona_dir.is_dir():
                continue
            for run_dir in persona_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                if not run_dir.name.endswith(f"_{short_id}"):
                    continue
                # 精确校验：新架构要求 .session_id 必须存在
                sid_file = run_dir / ".session_id"
                if not sid_file.exists():
                    continue
                stored_sid = sid_file.read_text().strip()

                # 比对: 输入可能是 12 位或 32 位，stored 总是 32 位
                if stored_sid.startswith(session_id):
                    candidates.append(run_dir)

    if len(candidates) == 0:
        raise EvalError(f"No result found for session {short_id}")
    if len(candidates) > 1:
        raise EvalError(f"Ambiguous: {len(candidates)} results match {short_id}")
    return candidates[0]
```

`http_app.py` 中的 `find_archived_result_dir()` 和 `get_or_restore_session()` 改为调用此函数。运行时不保留旧目录 / 旧 JSON fallback；本地旧结果通过一次性迁移脚本改名、补 `.session_id`、转换为新 `evaluations/index.json + score_1/score.json + cost.json`。

### 9.3 现有数据迁移

7 个现有结果目录需一次性迁移（8 位 → 12 位），并补写 `.session_id` 文件。若这些目录已有旧评分文件，则同步转换为新结构:

```
evaluations/eval_meta.json 或 evaluations/latest/eval_meta.json
  → evaluations/index.json
  → evaluations/score_1/score.json
  → evaluations/score_1/cost.json
```

迁移完成后删除旧 `eval_*` 目录、`latest` symlink、`eval_meta.json`、所有 Markdown 报告。运行时代码不读取旧格式。

目录重命名:

```
执行脚本（一次性）:

D03.../developer_crossover/20260417_220406_e7d78f7e
  → 20260417_220406_e7d78f7e21bf  + .session_id = e7d78f7e21bf4d87b27ae48446a3e4d0

D03.../double_novice/20260417_212613_783a207b
  → 20260417_212613_783a207bd0a8  + .session_id = 783a207bd0a847359a8ff57bfbbcafd8

D03.../finance_veteran/20260417_183659_9d5b7058
  → 20260417_183659_9d5b70586a5b  + .session_id = 9d5b70586a5b49bcba58052aa8a56175

D05.../double_novice/20260417_212244_d5aea239
  → 20260417_212244_d5aea2395b6a  + .session_id = d5aea2395b6a49b6a3dd6a465c3aa1ac

D05.../fullstack_practitioner/20260417_184001_674546a7
  → 20260417_184001_674546a746bc  + .session_id = 674546a746bc4399b491ca7966e7c7b4

I01.../developer_crossover/20260417_215747_e5807cf9
  → 20260417_215747_e5807cf99627  + .session_id = e5807cf996274b948fff645a07dd6d10

X09.../fullstack_practitioner/20260417_185053_c2bf885f
  → 20260417_185053_c2bf885fcefd  + .session_id = c2bf885fcefd45269633a24d469219dd
```

迁移方式: 实施阶段 Phase 1 ⑤ 时编写一次性脚本自动执行，确认结果后删除脚本。

---

## 10. 文件变更清单

```
新增:
  server/eval/eval_request.py          ← EvalRequest + ScoreQuery + parse/resolve
  server/eval/eval_output.py           ← DimensionResult + TrackResult + EvalOutput
  server/eval/eval_coordinator.py      ← 协调器（替代 pipeline.py 编排逻辑）
  server/eval/preflight.py             ← 统一前置检查
  server/eval/llm_call.py              ← llm_call_with_retry() 共享容错逻辑（无抽分 fallback）
  server/eval/tracks/__init__.py
  server/eval/tracks/qr_track.py       ← QR 评估（含 blending）
  server/eval/tracks/qp_track.py       ← QP 评估（5 维度）
  server/eval/tracks/tutor_track.py    ← Tutor 评估（6D 或子集）
  server/storage/score_store.py        ← score_n 分配、index.json 读写、文件锁
  server/scripts/eval_single.py        ← 统一 CLI 入口

修改:
  server/eval/ewan_eval/conv_geval.py
    ・a_measure() 返回值包含 evidence
  server/eval/ewan_eval/result_judge.py
    ・保留 evidence 到 per_model 和 final result
    ・删除 expected_outcome 参数
  server/eval/ewan_eval/process_metrics.py
    ・提取 evidence
    ・使用 llm_call_with_retry() 替代内联容错
    ・reference_unavailable → skipped，不硬零、不阻断
  server/eval/ewan_eval/tutor_conv_geval.py
    ・提取 evidence
    ・使用 llm_call_with_retry() 替代 3 层 fallback
    ・删除 _fallback_direct_eval()、fallback_extract 及 max_score//2 注入
    ・删除 expected_outcome 参数
  server/storage/eval_writer.py
    ・写 evaluations/{score_id}/score.json
    ・写 evaluations/{score_id}/cost.json
    ・更新 evaluations/index.json
    ・不生成任何 Markdown 文件
  server/storage/result_writer.py
    ・目录名 session_id[:12]
    ・写入 .session_id 文件
    ・只保存 run_state.json，不生成 run_state.md
  server/api/http_app.py
    ・POST /evaluate 简化为: resolve → allocate score_n → coordinator.run
    ・GET /scores 支持 latest/history/score/scores/status_filter
    ・find_archived_result_dir 使用 12 位 + .session_id 校验
  server/api/session_api.py
    ・_run_evaluation 委托给 eval_coordinator
    ・删除 expected_outcome 相关逻辑
    ・评分状态从单一 _eval_status 迁移到 score_store/index.json
  server/scripts/eval_tutor_dims.py
    ・删除（功能由 eval_single.py --mode tutor 替代）
  server/scripts/batch_eval.py
    ・删除（功能由 eval_single.py --scan-dir 或循环调用替代）

删除:
  server/eval/reports/score_report.py
    ・不再生成 scores.md；前端直接读取 score.json 渲染
  任何 run_state.md / scores.md / trace.md / cost.md 生成路径

废弃:
  server/eval/pipeline.py
    ・由 eval_coordinator.py + tracks/ 替代
    ・新架构切换后删除，不保留运行时兼容路径
```

---

## 11. 实施顺序

```
Phase 1: 基础设施（断代切换）
  ① eval_request.py + eval_output.py (数据模型)
  ② score_store.py (score_n/index.json/文件锁)
  ③ preflight.py (global + per-track 前置检查)
  ④ llm_call.py (统一 LLM 容错，删除假分数注入和抽分 fallback)
  ⑤ result_writer.py 改 12 位 + .session_id + 不生成 MD
  ⑥ 一次性迁移本地结果，迁完删除脚本

Phase 2: Track 分离
  ⑦ tracks/qr_track.py (从 pipeline.py 提取)
  ⑧ tracks/qp_track.py (从 pipeline.py + process_metrics.py 提取)
  ⑨ tracks/tutor_track.py (从 pipeline.py + tutor_conv_geval.py 提取)

Phase 3: 协调器 + 入口
  ⑩ eval_coordinator.py (编排 + 中断处理 + not_computable)
  ⑪ eval_single.py (CLI create/get)
  ⑫ http_app.py 改造 (POST create score_n, GET read scores)

Phase 4: 持久化 + 前端读取
  ⑬ eval_writer.py 改造 (score.json + cost.json + index.json)
  ⑭ 前端 scores/cost 读取: run_state simulator cost + score cost 拼接展示
  ⑮ evidence 全链路保留验证

Phase 5: 清理
  ⑯ expected_outcome 清理
  ⑰ 删除 pipeline.py / score_report.py / eval_tutor_dims.py / batch_eval.py
  ⑱ 删除所有 Markdown 报告生成路径
  ⑲ 端到端测试
```

---

## 12. 实现状态与残余清理项

> **合并来源**: 原审核报告的有效结论。独立审核文档已删除，后续以本文档作为评估管道架构与清理项的单一入口。

截至 2026-04-22，本文档定义的核心机制已基本落地：

| 模块 | 状态 | 说明 |
|------|------|------|
| 入口统一 | 已落地 | CLI/REST 共享 `EvalRequest`、`parse_eval_request()`、`resolve_result_dir()`、`allocate_score_run()` |
| 三轨隔离 | 已落地 | QR/QP/Tutor 独立 track、独立 `cancel_event`、独立错误记录 |
| 诚实评分 | 已落地 | LLM 解析失败不再注入默认分；失败维度 `score=None`，阻断对应 track 聚合 |
| Evidence 全链路 | 已落地 | QR/QP/Tutor 的 `reason`、`evidence`、`per_model` 进入 `score.json` |
| 持久化 | 已落地 | `evaluations/{score_id}/score.json`、`cost.json`、`index.json` 追加式保存 |
| `expected_outcome` 清理 | 已落地 | 评估逻辑改用 RC；schema 中字段保留用于历史数据兼容 |

剩余清理项：

| 优先级 | 项目 | 影响 | 建议处理 |
|--------|------|------|----------|
| P2 | 历史结果目录迁移 | 旧 `{ts}_{session_id[:8]}` 目录缺少 `.session_id`，`resolve_result_dir()` 无法精确查找历史 session | 编写一次性迁移脚本：目录改为 `[:12]`，补写完整 `.session_id`，确认后删除脚本 |
| P3 | 日志中的 `session_id[:8]` | 不影响功能，但日志可读性与目录规则不一致 | 全局替换日志/调试输出中的 `session_id[:8]` 为 `session_id[:12]` |
| P3 | `OPENROUTER_API_KEY` 空字符串校验 | preflight 已多数情况拦截；底层 client 仍可静默接受空字符串 | 在 `llm_client.py` 初始化时对空 key 抛出明确配置错误 |
| P4 | 删除 `pipeline.py` wrapper | 当前仅是兼容性薄壳，不影响正确性 | 所有调用方迁移到 coordinator 后删除 |
