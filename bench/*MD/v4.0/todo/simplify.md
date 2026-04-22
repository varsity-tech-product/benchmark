# Simplify Review — 整合 TODO v2

**审计日期**: 2026-04-06
**数据来源**: 4 轮审查报告 + 2 份深入调研 + 实际代码验证

---

## 已完成（25 项）

所有 bug 修复、安全加固、核心重构均已完成。不再逐项列出。
详见旧 `todo.md` 中 ✅ 标记的条目。

---

## 旧文件处理记录

以下文件已在文档整合中删除，内容已归入本文件或 consensus 报告：
- `todo.md`、`simplify_round1-4`、`simplify_batch2_investigation.md`、`simplify_batch3_investigation.md`
| `simplify_round2_mcp_scripts.md` | **删除** | 同上 |
| `simplify_round3_evaluation.md` | **删除** | 同上 |
| `simplify_round4_web_tests.md` | **删除** | 同上 |
| `simplify_batch2_investigation.md` | **删除** | 全部方案已执行完毕 |
| `simplify_batch3_investigation.md` | **保留** | 含 #21-#25/#27 的未执行修改方案，仍有参考价值 |

---

## 未解决问题

### A. 已跟踪待定（7 项，原 todo.md 遗留）

#### A1. `scoring.py` pass@k 非标准公式 [原 #19]

- **文件**: `bench/evaluation/scoring.py:392`
- **问题**: 当前实现 `passed >= k ? 1.0 : passed/k`，标准公式是 `1 - C(n-c,k)/C(n,k)`
- **影响**: pass@1（k=1）两种公式**结果相同**，无影响。pass@3（k=3）语义不同，当前返回 `passed/3`（线性比例），标准公式返回"至少抽到一次"的概率
- **紧急性**: **低** — 只在 `compute_benchmark_kpis` 中使用，不影响单任务评分
- **决策**: 需 owner 确认是否依赖 pass@3 历史数据

#### A2. `generate_reference_signals.py` 17 处 iterrows() [原 #21]

- **文件**: `bench/reference/script/generate_reference_signals.py`
- **问题**: 所有信号生成函数逐行迭代 DataFrame
- **影响**: 一次性参考数据生成脚本，非 benchmark 热路径
- **紧急性**: **低** — 只影响参考数据重建速度
- **方案**:

| 难度 | 函数数 | 说明 |
|------|--------|------|
| EASY（13 个） | I01-I04, I06-I10, E04, X07-X09 | 纯无状态行过滤 + 阈值比较。`dropna` + `np.where`/`np.select` 即可 |
| HARD（2 个） | I05, E02 | "hold zone" 逻辑需 carry previous signal。`np.select` + `ffill` |
| MEDIUM（2 个） | E05, X10 | 日期收集改为 set comprehension |

```python
# EASY 模式统一替换:
valid = df.dropna(subset=["sma20"])
sig = np.where(valid["close"] > valid["sma20"], 1, -1)
signals = [{"date": str(d), "signal": int(s)} for d, s in zip(valid["date"], sig)]

# HARD 模式（I05 hold zone）:
sig = np.select([merged["zscore"] > 2, merged["zscore"] < -2, merged["zscore"].abs() < 0.5], [-1, 1, 0], default=np.nan)
sig = pd.Series(sig).ffill().fillna(0).astype(int)
```

**风险**: 输出 JSON 必须与现有参考数据完全一致，改写后需对比。

#### A3. `convert_binance_to_lean.py` 逐行转换 [原 #22]

- **文件**: `bench/scripts/convert_binance_to_lean.py:133-148`
- **问题**: `df.iterrows()` 逐行做 timestamp 转换，百万行分钟级数据极慢
- **紧急性**: **低** — 数据管线脚本，一次性运行
- **方案**: 3 处 iterrows 向量化：

| 位置 | 向量化方案 |
|------|-----------|
| `convert_to_lean_lines()` L133-148 | `pd.to_datetime(unit="ms")` + `.apply(lambda)` 格式化 |
| `write_high_res_zips()` L202-205 | `pd.to_datetime().dt.strftime("%Y%m%d").tolist()` |
| `aggregate_minute_to_daily()` L335-341 | 同 Location 1 |

**风险**: LEAN 格式对精度极敏感，需逐字节对比。

#### A4. Trade matching O(R×A) [原 #23]

- **文件**: `bench/evaluation/test_scripts/common/implementation_check.py:465`
- **问题**: 每个参考交易线性扫描所有 agent 交易，`_parse_time()` 重复调用 O(R×A) 次
- **影响**: I01（85 trades）无感，I04（4000 trades ~16M 操作）可能慢
- **紧急性**: **中低** — 当前跑分数据量下可接受，若交易量增长则成瓶颈
- **方案**:
  1. 预解析所有 ref 和 agent trades 的时间（各一次）
  2. 按 (symbol, direction) 分桶
  3. 排序 + `bisect` 二分搜索容差窗口内候选
  4. 复杂度 O(R+A) 预解析 + O((R+A) log A) 搜索，vs 当前 O(R×A)
- **风险**: 匹配结果须与旧算法完全一致，需回归测试

#### A5. checklist_score 64 处内联 [原 #24]

- **文件**: 64 个 test_scripts（41 个 bool-only + 23 个 float-score 变体）
- **问题**: 应统一使用 `evidence_helpers.checklist_score()`
- **紧急性**: **中** — 纯代码整理，新增 eval 指标时需改两处
- **方案**: 扩展 `checklist_score` 支持 `score` float 键（向后兼容）：

```python
def checklist_score(checklist):
    total = 0.0
    for c in checklist:
        w = c["weight"]
        if "score" in c:
            total += w * c["score"]
        elif c.get("passed", False):
            total += w
    return total
```

D-series 的 11 个已有调用者无需改动。

#### A6. `tutor_conv_geval.py` 42 次 deep copy [原 #25]

- **文件**: `bench/evaluation/deepeval_metrics/tutor_conv_geval.py`
- **问题**: 2 模型 × 3 轮 × 7 维度 = 42 次 `copy.deepcopy(ConversationalTestCase)`，~8MB 冗余
- **紧急性**: **中低** — 内存优化，不影响正确性
- **方案**: 从共享不可变 turn dict 列表重新构造轻量对象：

```python
_dim_turn_dicts[dim] = preprocess_turns(src, dim)  # 预存，共享
tc_turns = [Turn(role=t["role"], content=t["content"]) for t in _dim_turn_dicts[metric.name]]
all_test_cases.append(ConversationalTestCase(turns=tc_turns, **_tc_kwargs))
```

Python 字符串不可变，引用共享安全。`a_measure()` 只修改 test case 级 metadata，不修改 turn content。

#### A7. Funding rate 下载逻辑重复 [原 #27]

- **文件**: `download_binance_klines.py` vs `download_binance_full_universe.py`
- **问题**: 相同的 Binance API 分页 + DataFrame 转换在两处独立实现
- **紧急性**: **低** — 脚本级重复，不影响 benchmark
- **方案**: 抽取 `_fetch_funding_rows(session, symbol, start_ms, end_ms, *, raise_on_http_error=True, on_batch=None) -> DataFrame | None`。klines 传 `raise_on_http_error=True`，full_universe 传 `False`。验证逻辑保留在 klines 版

---

### B. 审计中发现的未跟踪问题（从 4 轮审查报告中遗漏，MEDIUM+ 级别）

#### B1. `_extract_numerical_outputs` 完全重复 [来源: Round 3 R1]

- **文件**: `bench/evaluation/code_eval.py:342-401` vs `bench/evaluation/trace_utils.py:110-179`
- **问题**: 两处实现功能完全相同的 `_collect_numeric_scalars` 递归 + workspace JSON 扫描 + metric tool 提取。唯一区别是 log 访问方式（attribute vs dict）
- **紧急性**: **中** — ~60 行真实重复，新增 metric 类型需改两处
- **方案**: 抽取到 `trace_utils.py` 共享函数，加 log-format 适配参数

#### B2. Google adapter 参数类型全部硬编码为 string [来源: Round 1 E13]

- **文件**: `bench/orchestrator/agent_adapters/google_adapter.py:110-131`
- **问题**: `_build_declarations` 将所有工具参数 `"type"` 硬编码为 `"string"`，忽略 schema 中的实际类型信息
- **影响**: LLM 收到错误的 schema metadata，可能降低 tool-call 准确率
- **紧急性**: **中** — 影响 Google agent 的工具调用质量
- **方案**: 改用 `normalize_tool_params` 的类型信息（已在 base_adapter.py 中可用）

#### B3. Google adapter 无 token usage 跟踪 [来源: Round 1 Q8]

- **文件**: `bench/orchestrator/agent_adapters/google_adapter.py`
- **问题**: 不记录 token 使用，`get_token_records()` 始终返回空列表
- **影响**: Google agent 跑分的成本报告显示 $0.00
- **紧急性**: **中低** — 成本追踪不准确，但不影响评分

#### B4. Google adapter 每次调用创建新 client [来源: Round 1 Q7/E10]

- **文件**: `bench/orchestrator/agent_adapters/google_adapter.py:68`
- **问题**: `genai.Client()` 在每次 `generate_response()` 中新建，不复用连接池
- **紧急性**: **低** — 增加延迟但不影响正确性
- **方案**: 移到 `__init__` 缓存为 `self._client`

#### B5. `_group_cancel` 在锁外修改 [来源: Round 4 Q6]

- **文件**: `bench/web/api/runs.py:516`
- **问题**: `_group_cancel = None` 在 `finally` 块中赋值但未持有 `_group_lock`，而 `stop_group_run` 在锁内读取
- **紧急性**: **低** — Web UI 单用户场景，竞态窗口极小
- **方案**: 将赋值移入 `with _group_lock:` 块内

#### B6. `marked.parse()` XSS 风险 [来源: Round 4 Q8]

- **文件**: `bench/web/static/js/render.js:80`
- **问题**: LLM 输出的 markdown 通过 `marked.parse()` 转为 HTML 后直接渲染，无 DOMPurify 净化
- **影响**: 如果 LLM 输出含 `<img onerror="...">` 等 payload 可执行 JS
- **紧急性**: **低** — localhost-only 开发工具，且 LLM 输出一般不含恶意 payload
- **方案**: 添加 DOMPurify 净化

#### B7. OAuth token 未缓存 [来源: Round 1 E6]

- **文件**: `bench/config/auth.py:16-72`
- **问题**: `get_oauth_token()` 每次调用都 shell out 到 `security` 或读文件。benchmark 运行中被多次调用
- **紧急性**: **低** — 每次 ~50ms，总计十几次 = 约 1 秒开销
- **方案**: `functools.lru_cache` 或带 TTL 的模块级缓存

#### B8. Tutor + Process 评估串行执行 [来源: Round 3 E1]

- **文件**: `bench/orchestrator/orchestrator.py` `_evaluate_task` 方法
- **问题**: tutor 7D 评估和 process 评估按顺序执行，两者独立可并行
- **影响**: 评估阶段墙钟时间可减半
- **紧急性**: **低** — 性能优化，不影响正确性
- **方案**: 用 `asyncio.gather` 或 `ThreadPoolExecutor` 并行两个评估阶段

---

## 优先级总结

| 优先级 | 项 | 说明 |
|--------|-----|------|
| **中（值得做）** | A4, A5, B1, B2 | trade matching 性能、checklist 统一、code_eval 重复、Google 参数类型 |
| **中低（有空再做）** | A1, A6, B3 | pass@k 公式、deep copy 优化、Google token 跟踪 |
| **低（不急）** | A2, A3, A7, B4-B8 | 一次性脚本性能、脚本重复、Web UI 安全/性能 |
