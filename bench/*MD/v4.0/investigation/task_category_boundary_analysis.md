# 任务类别关系与边界深度分析报告

> 版本: v4.0 | 日期: 2026-04-06 | 范围: D/S/B/I/X/E 六类非对抗性任务

---

## 一、各类别定位总览

| 类别 | 定位 | 任务数 | 难度分布 | 执行环境 |
|:---:|:---|:---:|:---|:---|
| **D** | 数据获取、清洗、特征工程 | 11 | Easy×5, Med×4, Hard×2 | Python (pandas), 2个联网任务 |
| **S** | Alpha 信号研究与评估 | 6 | Easy×1, Med×2, Hard×3 | Python (pandas/numpy), 无 LEAN |
| **B** | 回测引擎架构与方法论 | 6 | Easy×1, Med×2, Hard×3 | Python 自建引擎, 无 LEAN |
| **I** | LEAN 平台策略实现 | 10 | Easy×1, Med×2, Hard×7 | LEAN C#, Docker 回测 |
| **X** | 代码调试与缺陷诊断 | 10 | Easy×2, Med×2, Hard×6 | Python×6 + LEAN C#×4 |
| **E** | 端到端完整工作流 | 5 | Med×3, Hard×2 | Python + LEAN 混合 |

---

## 二、设想流程 D→S→B→I→X→E 与量化研究员真实路径的对齐分析

### 2.1 理想化的量化研究员执行路径

```
真实工作流:
  数据准备 → Alpha研究 → 回测验证 → 生产实现 → 调试修复 → 端到端验收
     D           S           B           I           X           E
```

**总体判断：路径设计基本合理，但存在若干错位。**

### 2.2 逐环节对齐度评估

| 环节衔接 | 对齐度 | 分析 |
|:---|:---:|:---|
| **D→S** | ★★★★☆ | 良好。D 处理原始数据，S 在清洗后的数据上做 alpha 研究。但 D09（特征工程）与 S 系列的信号构建有概念重叠 |
| **S→B** | ★★★☆☆ | 中等偏弱。S 系列做 alpha 研究时已经在做简单回测（rough PnL），B 系列却从零构建回测引擎。这两者的"谁先谁后"存在歧义 |
| **B→I** | ★★☆☆☆ | **较弱。** B 系列用 Python 自建引擎，I 系列完全在 LEAN C# 上。B 的架构知识（三层分离、look-ahead 防护）在 I 中几乎不被复用——LEAN 已经内置了这些 |
| **I→X** | ★★★★☆ | 良好。X07-X10 直接在 LEAN 上调试，与 I 系列的技术栈一致。但 X01-X06 是 Python 调试，和 I 系列无关 |
| **X→E** | ★★★☆☆ | 中等。E 系列是综合任务，但它并不要求"先调试再集成"。E 更像是 S+B+I 的组合，X 并非 E 的前置条件 |

---

## 三、类别间边界问题详析

### 3.1 S 与 D 的边界模糊区

**问题：D09（Feature Engineering Pipeline）与 S 系列的信号构建高度重叠**

| D09 要求 | S02/S03 要求 |
|:---|:---|
| 从 OHLCV 构建 returns, volatility, technical indicators | 构建 trend/mean-reversion 信号（本质也是 technical indicators） |
| 诊断 multicollinearity | 评估信号 IC、quantile |
| 防止 look-ahead leakage | 讨论信号鲁棒性 |

D09 的"构建特征 → 验证无泄露 → 检查冗余"与 S 系列的"构建信号 → 评估质量 → 验证鲁棒性"实质上是**同一个认知过程的不同表述**。区别仅在于 D09 侧重"数据工程视角"而 S 侧重"alpha 研究视角"，但对 agent 来说所需的工具调用和代码输出几乎一致。

**建议：** 明确 D 系列止步于"数据可用性保障"（D01-D08, D10-D11），将 D09 的特征工程内容合并入 S 系列，或明确 D09 只做"通用特征工程"而非"交易信号构建"。

### 3.2 S 与 B 的边界模糊区

**问题：S 系列内含"rough PnL"回测，B 系列又从头建引擎**

- S01 要求"运行回测并产出 Sharpe/return/可视化" — 这已经是一个完整的回测流程
- S02-S06 每个都要求"rough PnL check" — 需要调用 `run_backtest` 或手动计算策略收益
- B01 却要求"解释 Sharpe ratio 是什么意思" — 认知层级反而比 S 低

**结果：** 如果按 D→S→B 顺序执行，学员在 S 阶段已经熟练使用回测工具并理解 Sharpe，到 B01 还要被教"什么是 Sharpe ratio"，出现**认知倒退**。

**建议：**
- 将 B01 移到 S 之前（作为回测基础），或
- 重新定义 S 系列为"纯信号研究"（不含回测），rough PnL 由 B 系列提供，或
- 将 S01（MA crossover 策略完整回测）归入 B 系列

### 3.3 B 与 I 的断层

**问题：B 系列的 Python 自建引擎与 I 系列的 LEAN C# 完全脱节**

B 系列教学员构建的能力：

- 自建数据回放模块（B02）
- 自建 PnL 计算（B02）
- Look-ahead 防护架构（B03）
- 多资产同步回放（B04）
- 执行模拟（slippage/fee/funding）（B05）
- Walk-forward 验证（B06）

I 系列所需的能力：

- LEAN C# 语法和 QCAlgorithm API
- `AddCryptoFuture`, `SMA()`, `RSI()` 等内置方法
- Algorithm Framework（AlphaModel, PCM, ExecutionModel）
- `SetWarmUp`, `Consolidate`, `GetParameter`

**这两个技能集几乎没有交集。** B 系列培养的是"回测引擎开发者"，I 系列需要的是"LEAN 平台用户"。在真实量化工作流中，大多数研究员不会自建引擎后再去 LEAN 实现——他们通常二选一。

**建议：**
- 明确 B 系列定位为"回测方法论与原理理解"，而非"为 I 系列做技术准备"
- 或增加 B→I 的桥梁任务（如：将 Python 原型迁移到 LEAN）
- E02/E05 实际上正是这个桥梁（Python→LEAN），但被放在了最后

### 3.4 X 系列的内部分裂

**问题：X01-X06（Python）与 X07-X10（LEAN C#）是两个截然不同的技术栈**

| 子集 | 技术栈 | 前置知识 | 自然前驱 |
|:---|:---|:---|:---|
| X01-X06 | Python/pandas | D/S/B 系列知识 | 应紧跟 S/B |
| X07-X10 | LEAN C# | I 系列知识 | 应紧跟 I |

将它们统一为"X 系列"并放在 I 之后，意味着 X01-X06 与其前置知识（Python 系列）之间被 I（LEAN）隔开了。

**建议：**
- 将 Python 调试任务（X01-X06）重新归类，放在 B 之后 / I 之前
- 或将 X 系列拆分为 X-Python 和 X-LEAN 两个子集，允许非线性执行

### 3.5 E 系列的定位问题

**问题：E 系列号称"端到端"，但各任务覆盖范围差异极大**

| 任务 | 实际覆盖的阶段 | 是否真正端到端？ |
|:---|:---|:---:|
| E01 | S + B（Python MA 系统） | 否，缺 D 和 I |
| E02 | S + I（Python→LEAN 迁移） | 部分，缺 D 和 B |
| E03 | S + B（信号验证 + IS/OOS） | 否，缺 D 和 I |
| E04 | X + I（LEAN 多 bug 调试） | 否，本质是高级 X 任务 |
| E05 | D + S + B + I（完整流程） | **是，唯一真正端到端** |

**E04 实际上是一个复合 debug 任务**（三个交互 bug），将其放在 E 系列而非 X 系列缺乏说服力。E01/E03 更像是 S 和 B 的组合变体，而非端到端流程。

**只有 E05 真正实现了 D→S→B→I 的完整链路。**

---

## 四、各类别内部一致性评估

| 类别 | 内部一致性 | 说明 |
|:---:|:---:|:---|
| **D** | ★★★★★ | 非常一致。11 个任务严格围绕数据生命周期，难度阶梯清晰 |
| **S** | ★★★★☆ | 较一致。S01 偏简单（更像 B 任务），S02-S06 在"alpha 研究"这一主线上递进良好 |
| **B** | ★★★★☆ | 较一致。从指标解读→引擎架构→高级验证，递进合理。但 B01 认知层级过低 |
| **I** | ★★★★★ | 非常一致。10 个任务全部在 LEAN 上，从单品种→多品种→Framework→优化，阶梯分明 |
| **X** | ★★★☆☆ | 内部分裂。Python 子集和 LEAN 子集技术栈完全不同，前置知识不同 |
| **E** | ★★☆☆☆ | **一致性最差。** 5 个任务的"端到端"程度参差不齐，E04 实质是 debug 任务 |

---

## 五、各类别任务清单速查

### D 系列（数据分析）— 11 个任务

| 任务 | 难度 | 核心内容 | 超时 |
|:---|:---:|:---|:---:|
| D01 Load & Inspect OHLCV | Easy | 加载 OHLCV，基础探索 | 10 min |
| D02 Missing Data | Easy | 缺失值检测与处理 | 12 min |
| D03 Type Conversion | Easy | 数据类型转换与验证 | 12 min |
| D04 Summary Statistics | Easy | 描述性统计与分布诊断 | 12 min |
| D05 Return Computation | Medium | 简单收益 vs 对数收益 | 15 min |
| D06 Tick Aggregation | Medium | Tick→OHLCV 聚合 | 15 min |
| D07 Broken Feed Diagnosis | Hard | 多种数据源故障诊断 | 20 min |
| D08 Alt Data Integration | Hard | 另类数据对齐与信号检验 | 20 min |
| D09 Feature Engineering | Medium | 特征构建 + 泄露/冗余检查 | 15 min |
| D10 Historical Data Fetch | Easy | API 获取历史数据 (联网) | 15 min |
| D11 Realtime Data Fetch | Medium | 实时数据流采集 (联网) | 15 min |

### S 系列（策略研究）— 6 个任务

| 任务 | 难度 | 核心内容 | 超时 |
|:---|:---:|:---|:---:|
| S01 MA Crossover | Easy | 均线策略完整回测 + 可视化 | 30 min |
| S02 Trend Following Research | Medium | BTC 趋势跟踪信号研究 | 40 min |
| S03 Mean Reversion Research | Medium | BTC 均值回归信号研究 | 40 min |
| S04 Volume & Microstructure | Hard | 非价格数据 alpha + 多时间框架 | 45 min |
| S05 Cross-Asset Alpha | Hard | BTC-ETH 跨资产信号 | 45 min |
| S06 Multi-Signal Combination | Hard | 多信号组合 + 分散化分析 | 25 min |

### B 系列（回测）— 6 个任务

| 任务 | 难度 | 核心内容 | 超时 |
|:---|:---:|:---|:---:|
| B01 Interpret Metrics | Easy | 解释 Sharpe/DD/Return 等指标 | 25 min |
| B02 Basic Sequential Engine | Medium | 三层架构回测引擎 | 25 min |
| B03 Lookahead Prevention | Medium | 防 look-ahead 架构 + 验证测试 | 25 min |
| B04 Multi-Asset Sync | Hard | 多资产同步回放 + 配对策略 | 35 min |
| B05 Execution Simulation | Hard | slippage/fee/funding 建模 | 35 min |
| B06 Walk-Forward Validation | Hard | 滚动窗口参数优化 + OOS 验证 | 35 min |

### I 系列（实现）— 10 个任务

| 任务 | 难度 | 核心内容 | 超时 |
|:---|:---:|:---|:---:|
| I01 SMA (Single Symbol) | Easy | 单品种 SMA 趋势策略 | 25 min |
| I02 Trend Following | Medium | ~100 品种 SMA 交叉 | 40 min |
| I03 Mean Reversion | Medium | ~100 品种 RSI + 止损 + 多空 | 30 min |
| I04 Multi-Timeframe | Hard | 4h+1h 双时间框架 (~20 品种) | 30 min |
| I05 Cross-Asset Pairs | Hard | 配对交易 z-score (~10 对) | 25 min |
| I06 Multi-Signal Sweep | Hard | 三信号组合 + 19 组参数扫描 | 45 min |
| I07 Alpha Model | Medium* | Framework 迁移 (AlphaModel) | 20 min |
| I08 Multi-Alpha | Hard | 三 AlphaModel + A/B 对比 | 35 min |
| I09 Risk Management | Hard | 三种风控配置对比 | 30 min |
| I10 Parameter Optimization | Hard | ~250 组 grid search | 60 min |

> *I07 标注为 medium，但任务定义中标记为 hard

### X 系列（调试）— 10 个任务

| 任务 | 难度 | 技术栈 | 核心 bug |
|:---|:---:|:---:|:---|
| X01 MA Off-by-One | Easy | Python | rolling(19) → rolling(20) |
| X02 Look-Ahead Bias | Easy | Python | 缺少 shift(1) |
| X03 Position Bug | Medium | Python | 空头信号写成 0 而非 -1 |
| X04 Returns Diff | Medium | Python | .diff() → .pct_change() |
| X05 Timezone Merge | Hard | Python | UTC/Eastern 时区错位 |
| X06 Overfit Diagnosis | Hard | Python | 概念诊断（无单行修复） |
| X07 Warmup Bug | Hard | LEAN C# | 缺少 SetWarmUp + IsWarmingUp |
| X08 Order Type Bug | Hard | LEAN C# | LimitOrder → MarketOrder |
| X09 Alpha Conflict | Hard | LEAN C# | 对冲性 Insight 导致零交易 |
| X10 Universe Stale | Hard | LEAN C# | 幸存者偏差 + 静态 universe |

### E 系列（端到端）— 5 个任务

| 任务 | 难度 | 覆盖阶段 | 核心内容 |
|:---|:---:|:---|:---|
| E01 Build MA System | Medium | S+B | Python 完整 MA 系统 |
| E02 Research→Implementation | Medium | S+I | Python 原型 → LEAN C# 迁移 |
| E03 Strategy Validation | Medium | S+B | 动量信号 IS/OOS 验证 |
| E04 Production Debugging | Hard | X+I | LEAN 三 bug 复合调试 |
| E05 Full Quant Workflow | Hard | D+S+B+I | **唯一真正端到端** |

---

## 六、边界问题汇总与改进建议

### 6.1 关键问题优先级排序

| # | 问题 | 严重度 | 影响 |
|:---:|:---|:---:|:---|
| 1 | **B→I 断层**：Python 自建引擎 vs LEAN，技能不迁移 | 高 | 学习路径不连贯 |
| 2 | **S 内含回测 vs B 从头教回测**：认知倒退 | 高 | D→S→B 顺序失效 |
| 3 | **E04 分类错误**：本质是 X 任务 | 中 | E 系列定义稀释 |
| 4 | **X 系列内部分裂**：Python/LEAN 混合 | 中 | 线性路径假设不成立 |
| 5 | **D09 与 S 系列重叠**：特征工程 ≈ 信号构建 | 低 | 边界模糊但影响有限 |

### 6.2 改进方案

#### 方案 A：调整执行顺序为双轨制

```
Python 轨:  D → S → B → X(01-06) → E(01,03)
LEAN 轨:                 I → X(07-10) → E(02,04,05)
                         ↑
               S 的 alpha 概念作为输入
```

#### 方案 B：重新归类边界任务

- **S01 → B 系列**（它的核心是"构建并回测一个 MA 策略"，更像 B 而非 alpha 研究）
- **D09 → S 系列**（特征工程本质上是 alpha 信号构建的前置步骤）
- **E04 → X 系列**（三 bug 复合调试，不具备端到端性质）
- **X01-X06** 标记为 `prereq: S/B`，**X07-X10** 标记为 `prereq: I`

#### 方案 C：为 B→I 添加桥梁

- 将 E02（Python→LEAN 迁移）提前到 B 和 I 之间作为过渡任务
- 或在 B 系列增加一个"在已有引擎概念上理解 LEAN 架构"的任务

---

## 七、结论

**设想的 D→S→B→I→X→E 路径在宏观层面合理**，确实遵循了量化研究员"获取数据→研究信号→验证回测→生产实现→调试修复→端到端验收"的逻辑。但在**微观衔接层面存在三个主要断裂**：

1. **S 已经在回测，B 又从头教**（认知倒退）
2. **B 的 Python 引擎知识到 I 的 LEAN 平台完全不迁移**（技能断层）
3. **X 和 E 的内部组成与线性路径假设冲突**（分类不纯）

这些问题不影响单任务的质量（每个任务本身设计良好），但如果要将 benchmark 呈现为一条连贯的学习/评估路径，需要在类别边界和执行顺序上做上述调整。
