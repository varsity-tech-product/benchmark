# Programmatic Evaluation 深度调查报告

> 调查范围：E-01 ~ E-07，覆盖全部 6 个任务类别的 test_scripts、code_eval、scoring 聚合
> 调查目标：精确定位每个评估环节的误判风险、权重影响、缺失逻辑

---

## 评估体系架构速览

```
QR（Quant Result Score）的构成路径：

Task Score = 0.70 × Quant + 0.30 × Tutor
                ↓
Quant = 0.50 × QR + 0.50 × QP
           ↓
QR = f(test_script_score, code_eval_score, result_judge_score)
     ↓                   ↓                  ↓
     per-task 定制       3 层加权            LLM 2D评分
     checklist           A:15% B:35% C:50%  completeness+correctness
```

**关键问题**：这三个 QR 来源如何合并？→ 见 E-07

---

## E-01：关键词/正则匹配的脆弱性

### 问题分类

审查全部 test_scripts 后，将检查项分为 4 类：

| 类型 | 检测方式 | 可靠度 | 占比 |
|------|---------|--------|------|
| **A. 产物存在性** | 文件/CSV/JSON 是否存在 | 高 | ~25% |
| **B. 工具调用证据** | tool_logs 中特定工具是否被调用且成功 | 高 | ~20% |
| **C. 关键词子串匹配** | `"sharpe" in text` | **低** | ~35% |
| **D. 正则数值提取** | `r"Sharpe.*?(-?\d+\.?\d*)"` | **中** | ~20% |

### 按类别分析

#### D 系列（Data Analysis）—— 关键词依赖最严重

**D01** (`D01_load_inspect_ohlcv.py`):
- `data_loaded_successfully` (0.50): 检测 "describe"/"mean"/"std"/"count" 子串
- `basic_stats_computed` (0.50): 检测 "summary"/"statistics"/"info" 子串
- **False Positive 实例**：agent 在对话中说 "let me describe the mean and std of the data" → 即使没有执行任何代码也通过
- **False Negative 实例**：agent 使用 `df.agg(['average', 'stdev'])` → 不含 "mean"/"std" → 不通过

**D05** (`D05_return_computation.py`):
- 检测 "pct_change"/"np.log"/"cumsum"
- **False Positive**：`# TODO: use pct_change and np.log for cumsum` → 注释中包含关键词即通过

#### S 系列（Strategy）—— 正则提取风险

**S01** (`S01_ma_crossover.py:175-178`):
```python
r"[Ss]harpe.*?(-?\d+\.?\d*)"  # 最宽松的 pattern
```
- **False Positive**：文本 "Sharpe analysis from 2024 shows..." → 提取到 `2024`，不在 [-0.5, 3.0] 范围，该项 **不通过**（幸运地被范围检查拦截）
- **但如果**：文本 "Sharpe in the 2.5 range" → 提取 `2`（非贪婪匹配到第一个数字），范围检查通过，但值不准确

#### B 系列（Backtest）—— 解释存在性不验证正确性

**B01** (`B01_interpret_metrics.py:108-131`):
```python
interpretation_keywords = [
    "good", "bad", "strong", "weak", "high", "low",
    "risk-adjusted", "benchmark", "above", "below",
    "indicates", "suggests", "means", "overfitting",
]
# interpretation_present = any(kw in assistant_text for kw in keywords)
```
- **False Positive**："The Sharpe of 1.5 is bad" → 包含 "bad" → 通过，但解释错误
- **False Negative**："A Sharpe ratio of 1.5 demonstrates strong risk-adjusted performance relative to the benchmark" → 如果不含这些特定词就不通过（实际上大多数情况会通过，因为词表较宽）

#### X 系列（Debug）—— Pattern 匹配脆弱

**X01** (`X01_ma_offbyone.py`):
- 检测 `rolling(20)` 存在 + `rolling(19)` 不存在
- **False Negative**：agent 写 `rolling(window=20)` 或 `df.rolling(n)` where `n=20` → 不匹配
- **False Positive**：注释 `# Changed rolling(19) to rolling(20)` → 如果旧代码的注释仍在文件中，`rolling(19)` 被检测到 → `bug_is_fixed=False`

### 影响量化

对于一个"完美 agent"（正确执行了所有步骤），关键词匹配的预估 false negative 率：

| 类别 | 检查项数 | 预估 FN 率 | 原因 |
|------|---------|-----------|------|
| D 系列 | ~20 | 5-10% | 术语变体（mean vs average） |
| S 系列 | ~15 | 10-15% | 正则提取不准 + 格式变体 |
| B 系列 | ~12 | 3-5% | 关键词表较宽，FN 低 |
| X 系列 | ~10 | 15-20% | Pattern 极其脆弱 |
| E 系列 | ~10 | 5-10% | 类似 D + S |

### 解决方案方向

**不建议全面替换关键词匹配**——成本太高且部分检查（如"是否讨论了过拟合"）只能用关键词。

**建议分层加固**：
1. **A/B 类检查保留**（产物存在性 + 工具调用证据）——这些可靠度高
2. **C 类关键词匹配**：对高权重项（≥0.25）追加 LLM 二次确认。低权重项保留关键词
3. **D 类正则提取**：优先从结构化输出（JSON、tool results）中提取数值，regex 仅作 fallback

**论文角度**：在论文中报告 programmatic eval 的覆盖率和 LLM judge 的补充作用，承认关键词匹配的局限性，展示两者的互补效果。

---

## E-02：I 系列 Behavioral Matching 容差

### 事实发现

**`implementation_check.py` 关键容差值**：

| 参数 | 值 | 位置 | 含义 |
|------|------|------|------|
| `time_tolerance_bars` | 1 | 各 I 系列脚本默认 | 入场时间允许偏差 ±1 根 bar |
| `count_tolerance` | 0.10 | `count_within_tolerance()` | 交易笔数允许偏差 ±10% |
| `return_tolerance` | 0.20 | `return_within_tolerance()` | 总回报允许偏差 ±20% |
| PnL 最小匹配数 | 3 | `match_trades():520` | ≥3 笔 matched trade 才算 PnL correlation |

**`compute_behavioral_score()` 四维权重**：

| 维度 | 默认权重 | 计算依据 |
|------|---------|---------|
| signal | 0.40 | Reference signals vs agent signals 的 IC 相关性 |
| position | 0.30 | 仓位时间序列的重叠度 |
| performance | 0.20 | Sharpe / return / drawdown 对比 |
| trade | 0.10 | Entry/exit 时间 + 方向匹配率 |

**缺失维度时的权重重分配** (`lines 1239-1250`)：
```
如果 signal reference 缺失：
  available = position(0.30) + performance(0.20) + trade(0.10) = 0.60
  scale = 1.0 / 0.60 = 1.667
  → position 权重从 0.30 变为 0.50, performance 从 0.20 变为 0.33
```

### 核心问题

1. **固定 tolerance 不适应不同数据粒度**：
   - 1-hour bar 的 1-bar tolerance = 1 小时偏差 → 合理
   - 1-day bar 的 1-bar tolerance = 1 天偏差 → 对日频策略过于宽松
   - 当前所有 I 系列任务使用相同数据粒度（8h crypto），所以暂不构成问题

2. **参数偏差导致全军覆没**：
   - Agent 实现 SMA(21) 代替 SMA(20) → 所有信号偏移 → signal_score ≈ 0, trade matching ≈ 0
   - 即使策略逻辑完全正确，behavioral_score → 接近 0
   - **但**：这恰恰是 behavioral matching 的设计意图——验证"是否实现了**指定的**策略"而非"是否实现了**某个**策略"

3. **权重重分配的合理性**：
   - 当 signal reference 缺失时，position/performance 权重膨胀
   - 这可能导致分数不可跨任务对比（不同任务 reference 完整度不同 → 有效权重不同）

### 建议

- **tolerance 暂不改动**——当前所有 I 系列任务统一数据粒度，1-bar tolerance 合理
- **如果未来添加不同粒度的任务**：tolerance 应从 task JSON 中读取，或根据 bar_duration 自动计算
- **权重重分配**：在论文中声明"behavioral score 的有效维度数取决于 reference 完整度"，并在结果表中标注各任务的 reference 覆盖情况
- **参数偏差**：这是 feature 不是 bug——I 系列任务明确要求特定参数（如 SMA(20)），偏差应被惩罚

---

## E-03：Code Eval Layer C 硬零分

### 事实发现

`code_eval.py:510-518`：
```python
layer_c = evaluate_code_output(workspace_path, reference, tool_logs)
# 当 reference=None 时，layer_c = {"applicable": False, "score": 0.0}

score = 0.15 * layer_a["score"] + 0.35 * layer_b["score"] + 0.50 * layer_c["score"]
# → 0.50 * 0.0 = 0 → 最高可得 0.15 + 0.35 = 0.50
```

**注释明确写**（line 517）：
> "When no reference, Layer C scores 0.0 (hard zero) — no renormalization."

### 影响范围

| 类别 | reference 通常存在？ | Layer C 影响 |
|------|---------------------|-------------|
| I 系列 | ✓（有 reference trades/signals） | 正常评分 |
| D 系列 | 部分有（key_results） | 缺失时 code_eval 天花板 0.50 |
| S 系列 | 部分有 | 同上 |
| B 系列 | 部分有 | 同上 |
| X 系列 | 少 | 多数被 test_script 覆盖，code_eval 权重低 |
| E 系列 | 少 | 同上 |

### 设计意图 vs 实际效果

**设计意图**（从注释推断）：Layer C 的数值准确性是"主信号"（50% 权重），因为 LLM judge 的数值判断不可靠。没有 reference 就没有数值对比的基础 → 给 0 是保守但合理的。

**实际效果**：对缺少 reference 的任务，code_eval 最高 0.50，即使 agent 代码写得完美、执行无误。

### 解决方案

**方案 A：renormalize**（Layer C 不可用时，A:30% + B:70%）
- 优势：不惩罚 reference 缺失
- 风险：去掉了最重要的评估维度，分数膨胀

**方案 B：要求所有 task 都有 reference**
- 优势：消除问题根源
- 风险：增加 benchmark 维护成本

**方案 C（推荐）：条件性 renormalize + 标注**
```python
if layer_c["applicable"]:
    score = 0.15 * A + 0.35 * B + 0.50 * C
else:
    score = 0.30 * A + 0.70 * B  # renormalize
    # 标注：score_without_output_verification = True
```
- 在结果中标注该分数"未经数值验证"
- 论文中分开报告 "with reference" vs "without reference" 的 code_eval 分数

---

## E-04：解释正确性不可验证

### 事实发现

B 系列的解释检查只验证"存在"不验证"正确"：

**B01** (`B01_interpret_metrics.py:108-131`):
```python
# interpretation_present (0.20 weight):
# 只检查对话中是否出现解释性关键词
```

**A03** (`A03_sharpe_misconception.py`):
- 这是一个反面案例——adversarial task 检测 agent 是否纠正了学生的 Sharpe 误解
- 使用关键词匹配："misconception"/"wrong"/"incorrect"/"actually"
- **也不验证纠正内容是否正确**

### 分析

这个问题**本质上是 programmatic eval 的能力边界**：
- "解释是否存在" → 关键词可以判定 → programmatic 能做
- "解释是否正确" → 需要语义理解 → programmatic 做不到
- "解释是否正确" → LLM judge 可以判定 → 已有 Tutor 7D 中的 `correctness` 维度

### 建议

**不需要为 B 系列单独引入 LLM judge**——Tutor 7D 的 correctness 维度已经覆盖了解释正确性。

**但需要在论文中说清楚分工**：
```
Programmatic eval (test_scripts) → 验证"做了什么"（执行层面的事实检查）
LLM judge (result_judge)         → 验证"做得对不对"（结果的正确性和完整性）
Tutor eval (tutor_conv_geval)    → 验证"教得好不好"（解释的准确性、适应性）
```

三者互补，不是替代关系。Programmatic eval 的关键词检查承认只能判"做了"，而"做对了"由 LLM judge 负责。

---

## E-05：SWE-bench 风格执行验证的可行性

### 按类别可行性评估

| 类别 | 可行？ | 验证方式 | 难度 |
|------|--------|---------|------|
| I 系列 | **已有** | LEAN 编译 + 回测运行 + behavioral matching | 已实现 |
| D 系列 | 部分可行 | 验证 DataFrame shape / 列名 / 数值范围 | 中 |
| S 系列 | 困难 | 策略研究无固定输出格式 | 高 |
| B 系列 | 部分可行 | 验证回测引擎的输出结构 | 中 |
| X 系列 | 可行 | 修复前后的行为差异（类似 fail-to-pass） | 中 |
| E 系列 | 困难 | 端到端无固定 checkpoint | 高 |
| A 系列 | 不可行 | 对话质量无法用 assertion 测 | N/A |

### I 系列已有的执行验证

I 系列实际上**已经有执行验证**——只是不是 SWE-bench 的 pass/fail 测试，而是 behavioral matching：

```
I 系列评估流程：
1. Agent 写 C# 代码 → LEAN 编译 → 编译成功/失败（binary）
2. LEAN 回测运行 → 生成 trades/summary/signals
3. Behavioral matching → 与 reference 对比（continuous score）
```

这比 SWE-bench 的 pass/fail 更丰富——SWE-bench 只知道"测试通过/不通过"，我们知道"偏差有多大"。

### X 系列最适合引入 fail-to-pass

X 系列（debug 任务）的结构天然适合 SWE-bench 模式：
- **fail**：给定 buggy code，特定 test case 失败
- **agent 修复** → 重新运行
- **pass**：修复后 test case 通过

当前 X 系列只用 regex 检查修复模式（如 `rolling(20)` 替代 `rolling(19)`），但可以添加：
```python
# X01 增强：执行验证层
def verify_fix(workspace_path):
    fixed_code = read_agent_code(workspace_path)
    result = execute_ma_strategy(fixed_code, window=20)
    # 验证 MA window=20 的输出与 reference 一致
    assert abs(result.sharpe - REFERENCE_SHARPE) < 0.01
```

### 建议

- **I 系列**：现有 behavioral matching 已经足够，不需要改为 pass/fail
- **X 系列**：最值得添加执行验证（P2），作为 regex 检查的补充层
- **D/B 系列**：可选添加输出格式验证（DataFrame 结构检查），但优先级低
- **S/E/A 系列**：不适合执行验证，继续依赖 LLM judge

---

## E-06：Data Source Cap 严厉度

### 事实发现

`evidence_helpers.py:72`:
```python
if not ds["verified"]:
    score *= max(0.25, ds["fraction"])
```

**惩罚曲线**：

| fraction_accessed | 惩罚乘数 | 分数损失 |
|-------------------|---------|---------|
| 1.0 (全部访问) | 1.0 | 0% |
| 0.5 | 0.50 | 50% |
| 0.3 | 0.30 | 70% |
| 0.0 (未访问) | 0.25 | 75% |

### 问题场景

1. **Agent 用替代数据源达到相同结果**：被惩罚 → 不公平
2. **Task 指定 3 个 data_files，agent 只需要 1 个就够了**：fraction=0.33 → 67% 惩罚 → 不公平
3. **data_files 在 task JSON 中未设置**：`data_files=None` → cap 不生效 → 不同任务惩罚不一致

### 设计意图

这个机制的目的是确保 agent 使用了 benchmark 提供的数据（而非幻想数据或预训练记忆）。这是合理的——但 multiplicative penalty 过于严厉。

### 建议

**方案 A：改为 additive penalty（推荐）**
```python
if not ds["verified"]:
    penalty = 0.15  # 固定扣分，不按比例
    score = max(0.0, score - penalty)
```

**方案 B：改为 soft flag + 降档**
```python
if not ds["verified"]:
    results["data_source_warning"] = True
    score *= 0.85  # 固定 15% 折扣，不按 fraction
```

**方案 C：只在 fraction=0 时惩罚**
```python
if not ds["verified"] and ds["fraction"] == 0:
    score *= 0.50  # 完全没用数据 → 半价
# fraction > 0 → 不惩罚（至少用了部分数据）
```

推荐方案 B：简单、一致、不过度惩罚。

---

## E-07：QR 合并逻辑与 Divergence

### 事实发现

**QR 的构成**（从 orchestrator 追踪）：

QR 由 `orchestrator.py` 的 Phase 4 聚合：
```
quant_result_score = f(test_script_score, code_eval_score, result_judge_score)
```

具体合并逻辑分散在 `orchestrator.py` 的评估调用中，不在 `scoring.py`。

**`scoring.py` 只负责最终聚合**：
```python
Task_Score = 0.70 × (0.50 × QR + 0.50 × QP) + 0.30 × Tutor
```

### Divergence Dampening

**结论：当前系统中没有 divergence dampening 逻辑。**

`scoring.py` 中：
- 没有 `divergence` / `dampening` / `agreement` 关键词
- QR 直接取值，QP 直接取值，无交叉验证
- Tutor 缺失时 → `tutor_score = 0.0`，无 flag 区分"未评估"与"评为 0"

### 核心问题

1. **"评估缺失" vs "表现差" 不可区分**：
   - Tutor 未运行 → tutor_score=0.0 → Overall 被拉低 30%
   - Tutor 运行但表现极差 → tutor_score≈0.0 → 同样效果
   - 外部观察者无法区分这两种情况

2. **QR 内部 divergence 无检测**：
   - test_script 给 0.9，result_judge 给 0.4 → QR 取某种加权平均
   - 没有 flag 说"这两个评估不一致"
   - 如果经常 diverge → 说明至少一个评估不可靠

3. **跨维度 divergence 无检测**：
   - QR=0.95, QP=0.20 → agent 结果对但过程差？这是合理的还是评估错误？
   - 没有机制提醒审查

### 建议

**Phase 1：标注评估完整性（P1）**
```python
# 在 compute_task_score 返回值中添加：
"eval_completeness": {
    "quant_result": bool,  # test_script 或 result_judge 至少有一个
    "quant_process": bool,  # process_metrics 有结果
    "tutor": bool,          # tutor_dimension_scores 非空
}
```

**Phase 2：报告 divergence（P2，需要实验数据）**
- 跑完一批任务后，计算 test_script vs result_judge 的 Pearson r
- 如果 r < 0.5 → 需要审查两者的评估逻辑
- 在论文中报告这个相关性

---

## 设计哲学澄清（2026-04-06 讨论确认）

### Programmatic Eval 的本质定位

它不是"不完美的全能评估器"，而是**有明确作用域的事实检查器**：

```
能做的：产物是否存在、代码是否执行、数值是否匹配 reference
不能做的：解释是否正确、过程是否合理、教学是否有效
不应做的：试图用 regex 模拟语义理解
```

E-01（关键词匹配脆弱）不是 programmatic eval 的 bug，而是它被过度使用到超出能力边界的场景。论文中需要声明分工而非试图修复。

### 每个类别的评估策略分工

| 类别 | 产出明确度 | Programmatic 角色 | LLM Judge 角色 |
|------|-----------|------------------|---------------|
| I 系列 | 高（固定 spec → C# 代码 + 回测） | **主导**：behavioral matching | 补充 |
| D 系列 | 高（固定数据 → 数值输出） | **主导**：输出格式/数值检查 | 补充 |
| X 系列 | 高（固定 bug → 修复验证） | **主导**：可加执行验证 | 补充 |
| S 系列 | 低（开放研究） | 兜底：产物存在性 | **主导** |
| B 系列 | 低（开放解读） | 兜底 | **主导** |
| E 系列 | 中（端到端） | 混合 | 混合 |
| A 系列 | 无（纯对话） | 不适用 | **唯一** |

### 任务设计的核心约束

I 系列固定参数（如 SMA(20)）不是限制 agent 创造力，而是评估的前提约束——类似 SWE-bench 给出明确 issue 而非"修个 bug"。Agent 能力体现在"能否正确实现指定 spec"。

S 系列刻意不固定参数，因为研究任务的价值在于探索过程，programmatic eval 降级为"是否产出了分析产物"。

---

## 综合评估矩阵（已根据设计哲学重新定级）

| 问题 | 误判方向 | 优先级 | 行动 | 论文需讨论？ |
|------|---------|--------|------|-------------|
| **E-01** 关键词匹配 | FP+FN | **P3 论文声明** | 不修复代码，论文中明确 programmatic vs LLM 分工 | 是 |
| **E-02** Behavioral tolerance | FN 为主 | **P3** | 当前合理，声明容差设计 | 是 |
| **E-03** Layer C 硬零分 | FN | **P1 修复** | renormalize（A:30%+B:70%）+ 标注 | 是 |
| **E-04** 解释不验正确性 | FP | **关闭** | Tutor 7D correctness 已覆盖 | 否 |
| **E-05** 执行验证 | N/A | **P2** | X 系列可加执行验证，I 系列已有 | 是 |
| **E-06** Data Source Cap | FN | **P2 修复** | multiplicative → 固定 15% 折扣 | 否 |
| **E-07** Divergence | 信息缺失 | **P1** | 加 eval_completeness flag + 报告 divergence rate | 是 |

### 论文中必须讨论的 E 类问题

1. **E-01**：声明 programmatic eval 的作用域边界，展示与 LLM judge 的分工
2. **E-03**：报告有/无 reference 的 code_eval 分数分布差异
3. **E-05**：与 SWE-bench 的 pass/fail 模式对比，解释 behavioral matching 为何更适合
4. **E-07**：报告 test_script vs result_judge 的相关性，证明两者互补而非矛盾
