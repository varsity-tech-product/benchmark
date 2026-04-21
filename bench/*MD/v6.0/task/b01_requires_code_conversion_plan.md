# B01 改造为 Requires-Code 任务方案

> Date: 2026-04-21
> Scope: `bench/tasks/layer2/backtest/B01_interpret_metrics.json`
> Related eval: `bench/evaluation/test_scripts/backtest/B01_interpret_metrics.py`
> Goal: 将 B01 从概念解释型 backtest 任务改造成轻量代码执行任务，使 layer2 的非 A 类任务全部具备 `requires_code=true`
> Status: 已审查修订（挑刺版）

---

## 审查意见汇总（初稿存在的问题）

初稿方向正确，但有若干技术错误、逻辑漏洞和遗漏，直接按初稿实施会产生运行时错误或无效检测。问题分四类：

### A 类 — 会导致运行时报错的 Bug

**A1. import 路径错误**

初稿 §5.4 将 `python_source_records`、`python_code_text` 列入 `shared_utils` 的复用列表：

```python
from common.shared_utils import python_source_records  # ← 不存在，会 ImportError
```

实际分布：
- `collect_artifact_text`、`conversation_text`、`has_any`、`workspace_files` → `common.shared_utils`
- `python_source_records`、`python_code_text`、`source_has_component` → `common.backtest_engine_check`

B02 已正确从 `backtest_engine_check` 导入，B01 evaluator 应当对齐。

**A2. `code_artifact_present` 检测会被数据文档污染**

初稿建议用 `artifact_text` 搜索 Python metrics 关键字。但 `collect_artifact_text()` 会把
`risk_metrics.md`、`backtesting_101.md`（均在 `environment.docs_available`）一并纳入文本。
这两份文档里天然包含 `sharpe`、`drawdown`、`return`，检测结果将恒为 True。

正确做法：必须专门调用 `python_source_records(workspace_path)` 获取 `.py` 文件列表，
再检查文件名或源码内容，而不是搜 `artifact_text`。

**A3. `backtest_executed` 无法捕获新 code-task 的主要执行模式**

当前 evaluator 中 `shell_exec` 触发条件为命令串包含
`["backtest", "sharpe", "drawdown", "return"]`。

改造后 agent 的自然执行路径是：

```bash
python b01_metrics_analysis.py
```

这条命令既不含 `sharpe` 也不含 `drawdown`，`backtest_executed` 将无法被触发，导致 hard cap 发挥作用，即使 agent 完整完成了任务也会被误判为"未执行回测"。

修复：`shell_exec` 检测应额外覆盖"运行 `.py` 文件 + 结果含指标数值"的模式：

```python
if name == "shell_exec":
    cmd = str(log.args.get("command", "")).lower()
    result = str(log.result or "").lower()
    # 原有关键字检测
    if any(kw in cmd for kw in ["backtest", "sharpe", "drawdown", "return"]):
        results["backtest_executed"] = True; break
    # 新增：执行 .py 文件且结果含指标数值
    if re.search(r"python\s+\S+\.py", cmd) and re.search(
        r"sharpe|drawdown|return|drawdown", result
    ):
        results["backtest_executed"] = True; break
    # 原有结果检测
    if re.search(r"sharpe.*-?\d+\.?\d*|return.*-?\d+\.?\d*", result):
        results["backtest_executed"] = True; break
```

---

### B 类 — 逻辑错误（不崩溃但评分失真）

**B1. Hard cap 三档之间刑罚力度不一致**

初稿三档 cap：

| 条件 | cap | 损失权重 | 额外惩罚 |
|------|-----|---------|---------|
| 无 backtest_executed | ≤ 0.45 | 0.15 | 0.85 - 0.45 = **0.40** |
| 无 code_artifact_present | ≤ 0.60 | 0.15 | 0.85 - 0.60 = **0.25** |
| 无任何指标数值 | ≤ 0.20 | 0.45 | - |

前两档额外惩罚相差 0.15，但语义上"没写代码"不亚于"没执行代码"，刑罚力度倒置。
参考 B02 的 hard cap 设计（按组件完整度分层），B01 应保持逻辑一致：

- 无代码产物（`code_artifact_present=False`）→ cap ≤ 0.45（更严，因为这是 code task 的核心要求）
- 无执行证据（`backtest_executed=False`）→ cap ≤ 0.60（比无代码宽松，因为执行可能通过 convenience tool）
- 无任何指标数值 → cap ≤ 0.20（保留，最底层）

**B2. `metrics_artifact_saved` 权重 0.10 + 无 hard cap 自相矛盾**

初稿 §3.2 明确要求"可验证代码产物"，但 §5.2 给 `metrics_artifact_saved` 0.10（其余项均为 0.15），
且 §5.3 特别注明"不 hard cap"。结果：即使完全不存文件，agent 仍可得 0.90 满分。

两种可选方案：
- 方案 A：权重提升到 0.15，7 项合计变为 1.05，按比例归一化（推荐）
- 方案 B：保持 0.10，但加软 cap：无 `metrics_artifact_saved` 且无 `code_artifact_present` 时 score ≤ 0.40

**B3. `convenient_tools` 未清理，代码改造目标被绕过**

当前 JSON 中 `convenient_tools` 包含 `run_backtest`、`analyze_backtest_results`。
这两个工具可直接返回指标值，无需写任何 Python。
Agent 若调用它们：`backtest_executed` ✓、`sharpe_present` ✓、`return_present` ✓、`drawdown_present` ✓，
但 `code_artifact_present` ✗ → hard cap 0.45。

这意味着"只调用 convenience tool 不写代码"可以得 0.45，而"只解释概念不执行"仅能得 0.20。
Convenience tool 路径的评分高于"认真解释但无代码"，产生错误激励。

修复选项：
1. 从 `convenient_tools` 中移除 `run_backtest`、`analyze_backtest_results`（激进，可能影响 B02/B03）
2. 将"无 `code_artifact_present`"的 cap 降到 0.35，使 convenience-tool-only 路径不具吸引力

---

### C 类 — 遗漏字段或内容

**C1. `expected_mcp_tools` 缺少 `file_write`**

当前：`["shell_exec", "file_read", "get_environment_info"]`

新任务要求 agent 写 Python 文件并保存 metrics artifact，`file_write` 必须加入。
初稿完全未提及此字段，实施者若不注意，harness 侧对 tool 使用的期望将与实际脱节。

建议改为：`["shell_exec", "file_read", "file_write", "get_environment_info"]`

**C2. 现有 student openings 有语法 bug，初稿未提及**

当前 JSON 四条 student openings 均是由模板生成的，有明显截断问题：

```
"finance_veteran":    "…from a completed backtest,. I know…"   ← 句中多余逗号
"developer_crossover": "…from a completed backtest,. I can…"   ← 同上
"double_novice":      "…from a completed backtest,. I'm new…"  ← 同上
"fullstack_practitioner": "…from a completed backtest, properly. I've…"  ← 尚可但生硬
```

初稿只给了"建议方向"，没有修正这些已有的 bug。实施时应一并提供完整的、可直接使用的四条 opening。

**C3. `_tool_log_code_text` 不是共享 utility**

B02 evaluator 在本地定义了私有函数 `_tool_log_code_text()`。
初稿 §5.4 建议"复用"，但该函数未在任何 common 模块中导出。
B01 evaluator 要么：
1. 像 B02 一样在本地复制定义（简单但有重复），或
2. 在 `backtest_engine_check.py` 中将其提升为导出函数

若不处理，实施者看到"复用"会直接 import 并报错。

---

### D 类 — 未验证的断言

**D1. max_turns 12→15 推断正确，但论据不充分**

B03（medium 难度，代码任务）= 15 turns；B02（harder）= 20 turns。
B01 是 easy 难度，改为 15 与 B03 对齐是合理的，但初稿以"可能过紧"作为论据，
没有对比同系列任务。应注明 B03=15 作为基准依据。

**D2. Step 3 "server mirror 同步"悬而未决**

初稿说"当前仓库中未看到该 mirror 路径，实施时再确认。"这是把问题甩给实施者。
实际检查结果：仓库中 `bench/server/` 路径不存在，Step 3 应直接删除，不应留在计划中作为模糊待办。

---

## 1. 背景与动机

当前 layer2 共有 65 个任务，其中唯一的"非 adversarial 但 `requires_code=false`"任务是：

| Task | Category | 当前 `requires_code` | 问题 |
|------|----------|----------------------|------|
| `B01_interpret_metrics` | `backtest` | `false` | 破坏了"非 A 类任务均为代码/工具型核心量化任务"的简单分组 |

改造后结构更清晰：

| 分组 | 任务语义 | `requires_code` 关系 |
|------|----------|----------------------|
| 非 A 类任务 | 数据、策略、实现、回测、调试、端到端核心工作流 | 全部 `requires_code=true` |
| A 类任务 | 安全边界、误导场景、违规请求、教育型 adversarial | 混合：教育型 A 可为 code task，pure-refusal A 可为 no-code |

---

## 2. 当前 B01 状态

### 2.1 任务定义

`B01_interpret_metrics.json` 当前描述为：

- 引导学生解释已完成 backtest 的指标
- 理解 Sharpe ratio、total return、max drawdown
- 判断策略是否 promising/problematic
- 讨论 overfitting 等常见陷阱
- `requires_code=false`

但它的 `ground_truth.termination_criteria` 已经要求"computational evidence"和具体数值，
语义上已接近 code task，只是标记和 evaluator 没有强制代码产物。

### 2.2 当前 evaluator

`B01_interpret_metrics.py` 检查 5 项（各 0.20 权重），仅有一个 hard cap（无任何指标 → ≤ 0.20）。
不要求 Python 代码文件存在、不要求 metrics artifact 保存。

---

## 3. 改造目标

B01 改造后应成为"轻量指标计算与解释代码任务"。

### 3.1 B01 vs B02 边界

| 任务 | 边界 |
|------|------|
| B01 | 写小型 Python 分析脚本，计算/读取 backtest returns，输出并解释指标 |
| B02 | 构建 sequential backtest engine，包括 data handler、engine、strategy 三层架构 |

### 3.2 必需产物

改造后的 B01 要求 agent 留下至少一个可验证代码产物：

- `b01_metrics_analysis.py` 或语义等价 Python 文件
- `backtest_metrics.json`、`metrics_summary.csv` 或语义等价结果文件（推荐而非强制）
- 执行日志中出现关键指标数值

最低指标集：

| 指标 | 必需性 |
|------|--------|
| total return 或 cumulative return | 必需 |
| Sharpe ratio | 必需 |
| max drawdown | 必需 |
| annualized return 或 CAGR | 推荐 |
| benchmark comparison | 推荐 |

---

## 4. 任务 JSON 改造方案

目标文件：`bench/tasks/layer2/backtest/B01_interpret_metrics.json`

### 4.1 字段变更清单

| 字段 | 旧值 | 新值 |
|------|------|------|
| `requires_code` | `false` | `true` |
| `max_turns` | `12` | `15`（与 B03 easy/medium 对齐，B03=15，B02=20） |
| `expected_mcp_tools` | `["shell_exec", "file_read", "get_environment_info"]` | 加入 `"file_write"` |
| `description` | 偏概念解释 | 见 §4.2 |
| `student_openings` | 有语法 bug | 见 §4.3 |
| `expected_outcome` | 见 §4.4 | 更新 |
| `required_capabilities` | 见 §4.4 | 更新 |
| `termination_criteria` | 见 §4.4 | 更新 |

### 4.2 description

```text
Guide a student to write and run a small Python metrics-analysis script for a completed or simple backtest, compute basic performance metrics from local AAPL/SPY data, interpret what each metric means, and assess whether the strategy is promising or problematic.
```

### 4.3 student openings（修正语法 bug，保持 persona 差异）

```json
"student_openings": {
  "double_novice": "I want to learn how to compute and interpret basic backtest metrics in Python. I'm new to both coding and finance — can you walk me through it from the very beginning?",
  "developer_crossover": "I can write Python code but I don't really understand trading metrics like Sharpe ratio or drawdown. Can you help me write a script that computes them and explain what the numbers actually mean?",
  "finance_veteran": "I know what backtest metrics mean conceptually, but I want to turn this analysis into a reusable Python script so I can run it on any strategy. Can you help me build that?",
  "fullstack_practitioner": "I want a clean, inspectable Python script that computes key backtest metrics from local price data and saves the results. What's the right structure for something like this?"
}
```

### 4.4 ground truth 更新

**expected_outcome：**

```text
Tutor guides the student to write and run a small Python script that loads local AAPL or SPY price data from the workspace, computes basic backtest performance metrics including Sharpe ratio, total return or cumulative return, and maximum drawdown, presents specific numerical values, saves a metrics artifact to the workspace, explains what at least two metrics mean and how to judge whether their values are good or bad, and warns about at least one common interpretation pitfall such as overfitting, benchmark choice, survivorship bias, or in-sample versus out-of-sample validation.
```

**required_capabilities：**

1. Write a Python script that loads local price or return data from the workspace.
2. Compute total return or cumulative return, Sharpe ratio, and maximum drawdown.
3. Execute the script and present specific numerical metric values.
4. Save computed metrics to a reusable artifact such as JSON or CSV.
5. Explain what at least two metrics mean and how to interpret their values relative to benchmarks or rules of thumb.
6. Recognize at least one common pitfall such as overfitting, survivorship bias, or in-sample versus out-of-sample issues.

**termination_criteria：**

```text
The tutor has completed the following steps with computational evidence, not just verbal discussion: (1) Created a Python code artifact in the workspace that loads local data and computes at least Sharpe ratio, total return or cumulative return, and maximum drawdown. (2) Executed the code and displayed or saved the computed metrics with specific numerical values. (3) Explained what at least two of these metrics mean and how to judge whether their values are good or bad, using benchmarks or rules of thumb. (4) Discussed at least one common pitfall such as overfitting, survivorship bias, benchmark mismatch, or in-sample versus out-of-sample issues. Once all four steps have been demonstrated, the session is complete.
```

---

## 5. Evaluator 改造方案

目标文件：`bench/evaluation/test_scripts/backtest/B01_interpret_metrics.py`

### 5.1 Import 修正（修复 Bug A1）

```python
from common.shared_utils import (
    collect_artifact_text,
    conversation_text,
    has_any,
    workspace_files,
)
from common.backtest_engine_check import (   # ← 正确来源，非 shared_utils
    python_source_records,
    python_code_text,
)
from common.evidence_helpers import apply_data_source_cap
```

同时在本地定义（复制自 B02，勿直接 import）：

```python
def _tool_log_code_text(tool_logs: list) -> str:
    """Extract code/commands from tool logs (heredoc, inline, file_write)."""
    parts = []
    for log in tool_logs or []:
        name = log.name or ""
        if name == "shell_exec":
            parts.append(str(log.args.get("command", "")).lower())
            parts.append(str(log.result or "").lower())
        elif name == "file_write":
            parts.append(str(log.args.get("content", "")).lower())
    return "\n".join(parts)
```

### 5.2 `code_artifact_present` 检测（修复 Bug A2）

不得搜索 `artifact_text`（含数据文档），必须专门检查 `.py` 文件：

```python
def _check_code_artifact(workspace_path, tool_logs, tool_code):
    """
    Detect Python metrics analysis code via:
    1. Workspace .py files with relevant name or content
    2. file_write tool logs writing Python metrics code
    3. shell_exec heredoc containing Python metrics code
    """
    # 1. Workspace .py files
    py_records = python_source_records(workspace_path)
    for rec in py_records:
        name = rec["name"]
        code = rec["code_lower"]
        name_match = any(kw in name for kw in
                         ["metric", "backtest", "analysis", "performance", "b01"])
        code_match = sum(1 for kw in ["sharpe", "drawdown", "total_return",
                                       "cumulative_return", "max_drawdown"]
                         if kw in code) >= 2
        if name_match or code_match:
            return True
    # 2. file_write or shell heredoc containing Python metrics code
    metrics_kws = ["sharpe", "drawdown", "total_return", "cumulative_return"]
    py_indicators = ["import pandas", "import numpy", "def ", ".py", "pd.read_csv"]
    if any(kw in tool_code for kw in metrics_kws) and \
       any(kw in tool_code for kw in py_indicators):
        return True
    return False
```

### 5.3 `backtest_executed` 检测扩展（修复 Bug A3）

在现有检测逻辑后追加：

```python
# 新增：执行 .py 文件 + 结果中出现指标数值
if re.search(r"python\s+\S+\.py", cmd) and re.search(
    r"sharpe|drawdown|total.return|cumulative.return", result
):
    results["backtest_executed"] = True
    break
```

### 5.4 `metrics_artifact_saved` 检测

检测顺序：workspace 文件名 → 文件内容 → tool_logs file_write：

```python
def _check_metrics_artifact(workspace_path, tool_logs):
    artifact_name_kws = ["metric", "summary", "performance", "backtest", "result"]
    artifact_suffixes = (".json", ".csv", ".txt", ".md")
    content_kws = ["sharpe", "drawdown", "return"]

    for fpath in workspace_files(workspace_path):
        base = os.path.basename(fpath).lower()
        if not base.endswith(artifact_suffixes):
            continue
        if base.endswith(".py"):
            continue
        name_match = any(kw in base for kw in artifact_name_kws)
        try:
            with open(fpath) as f:
                content = f.read(4000).lower()
            content_match = sum(1 for kw in content_kws if kw in content) >= 2
        except (IOError, UnicodeDecodeError):
            content_match = False
        if name_match or content_match:
            return True

    for log in tool_logs or []:
        if (log.name or "") == "file_write":
            fname = str(log.args.get("path", "")).lower()
            content = str(log.args.get("content", "")).lower()
            if any(fname.endswith(s) for s in artifact_suffixes) and \
               not fname.endswith(".py") and \
               sum(1 for kw in content_kws if kw in content) >= 2:
                return True
    return False
```

### 5.5 评分权重与 hard cap（修复 B1、B2）

**权重：** 7 项 × 0.15 = 1.05，归一化到 1.00（每项除以 1.05）：

| 检查项 | 原始权重 | 归一化权重 |
|--------|---------|-----------|
| `code_artifact_present` | 0.15 | ≈ 0.143 |
| `backtest_executed` | 0.15 | ≈ 0.143 |
| `metrics_artifact_saved` | 0.15 | ≈ 0.143 |
| `sharpe_present` | 0.15 | ≈ 0.143 |
| `return_present` | 0.15 | ≈ 0.143 |
| `drawdown_present` | 0.15 | ≈ 0.143 |
| `interpretation_present` | 0.15 | ≈ 0.143 |

**Hard caps（修订版，力度一致）：**

| 条件 | cap | 设计理由 |
|------|-----|---------|
| 无任何指标数值（全部缺失） | ≤ 0.20 | 最底层保护，对齐原有逻辑 |
| 无 `code_artifact_present` | ≤ 0.45 | code task 核心要求，比"无执行"更严 |
| 无 `backtest_executed` | ≤ 0.60 | 执行可能通过 convenience tool，稍宽松 |
| 无 `metrics_artifact_saved`，其余满足 | 不额外 cap，只丢权重 | 文件命名差异风险，保持宽容 |

注：将"无代码"的 cap 定为 0.45（低于原草案的 0.60），使 convenience-tool-only 路径
（调 run_backtest 但不写 Python）得分上限 ≤ 0.45，不优于认真写代码解释但执行失败的路径。

---

## 6. Prompt 与评分影响

### 6.1 Tutor prompt 影响

将 B01 设置为 `requires_code=true` 后，标准 tutor context 会自动启用
`CODE TASK EXECUTION REQUIREMENT` 和 coding guidance（需确认 harness 逻辑是否以 `requires_code` 为开关）。

B01 的 category 是 `backtest`，不会启用 I/E/X 专属的 implementation tracking prompt，合理。

### 6.2 QR 影响

改造后 B01 纳入 code eval 路径：
- programmatic eval：B01 自己的 eval script
- code eval：检测代码产物、执行、输出
- result judge：评估完成度与可用性

预期影响：
- 只解释概念、不写代码的 agent 分数下降（hard cap ≤ 0.45）
- 只调 convenience tool 的 agent 受 hard cap 限制（≤ 0.45）
- 写代码 + 指标 + 教学解释的 agent 得分稳定

---

## 7. A / 非 A 类切分口径

改造 B01 后，layer2 报告口径：

| 报告口径 | 含义 |
|----------|------|
| Core workflow tasks | 非 A 类：D/S/I/B/X/E；全部 requires_code=true |
| Adversarial tasks | A 类：混合 educational-code + pure-refusal |

避免将 A/非 A 与 code/no-code 画等号；如需二元分析，使用 `requires_code` 字段。

---

## 8. 实施步骤

### Step 1: 修改任务 JSON

文件：`bench/tasks/layer2/backtest/B01_interpret_metrics.json`

1. `requires_code` → `true`
2. `max_turns` → `15`
3. `expected_mcp_tools` 加入 `"file_write"`
4. 更新 `description`
5. 替换全部四条 `student_openings`（含修正语法 bug）
6. 更新 `expected_outcome`、`required_capabilities`、`termination_criteria`

### Step 2: 修改 B01 evaluator

文件：`bench/evaluation/test_scripts/backtest/B01_interpret_metrics.py`

1. 修正 import（`python_source_records` 来自 `backtest_engine_check`）
2. 在本地定义 `_tool_log_code_text()`（复制 B02 模式，勿直接 import）
3. 增加 `_check_code_artifact()` 函数（专门检测 `.py` 文件，不搜 artifact_text）
4. 增加 `_check_metrics_artifact()` 函数
5. 扩展 `backtest_executed` 检测，覆盖 `python *.py` + 结果含指标模式
6. 调整 checklist 为 7 项，全部 0.15 权重（raw），归一化到 1.00
7. 更新 hard caps 为修订版三档（见 §5.5）

### Step 3: ~~同步 server 侧 evaluator~~（已确认：仓库无 mirror 路径，此步骤删除）

### Step 4: 增加回归测试

推荐位置：`bench/tests/test_regressions.py` 或新增 `bench/tests/test_task_metadata.py`

测试项：

1. B01 `requires_code=true`
2. layer2 非 adversarial 任务全部 `requires_code=true`
3. B01 evaluator 对"只聊天解释、无代码文件"的样例：score ≤ 0.45
4. B01 evaluator 对"只调 convenience tool、无 `.py` 文件"的样例：score ≤ 0.45
5. B01 evaluator 对"有代码文件、有执行、有三项指标、有解释"的样例：score ≥ 0.85

### Step 5: 跑验证

元数据验证：

```bash
python - <<'PY'
import json
from pathlib import Path

bad = []
for p in Path("bench/tasks/layer2").glob("**/*.json"):
    d = json.loads(p.read_text())
    if d.get("category") != "adversarial" and not d.get("requires_code", False):
        bad.append((d.get("task_id"), str(p)))

print("non-adversarial no-code tasks:", bad)
assert not bad, bad
PY
```

单测：

```bash
python -m pytest bench/tests/test_regressions.py -v
```

---

## 9. 验收标准

1. `B01_interpret_metrics` 的 `requires_code=true`。
2. layer2 中不存在非 adversarial 且 `requires_code=false` 的任务。
3. `expected_mcp_tools` 包含 `file_write`。
4. student openings 无语法错误，四条 opening 明确引导学生写代码。
5. B01 evaluator 专门检测 `.py` 代码产物（不依赖 `artifact_text` 的污染路径）。
6. `backtest_executed` 能捕获 `python metrics.py` 执行模式。
7. Hard cap 层级：code_artifact ≤ 0.45，backtest_executed ≤ 0.60，一致且无双重惩罚。
8. 所有回归测试通过。
9. B01 保持 easy/backtest/metrics-interpretation 定位，未漂移成 B02。

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| B01 与 B02 重叠 | B 系列边界模糊 | B01 只要 metrics script，不要 engine/data handler/strategy 三层架构 |
| 只 flip `requires_code` 但不改 evaluator | 仍可无代码高分 | 同步增加 `code_artifact_present` 检测与 hard cap 0.45 |
| `code_artifact_present` 误判（数据文档触发） | 好坏均会受影响 | 专门用 `python_source_records` 检测，不搜 `artifact_text` |
| `backtest_executed` 漏判 `python *.py` 路径 | 正确完成的 agent 被误判为"未执行" | 扩展检测条件（见 §5.3） |
| double novice 代码负担过重 | 教学体验变差 | 保持脚本轻量，`max_turns` 设 15 |
| A/非A 被误用成 code/no-code 标签 | 报告解释不准确 | 文档明确"非A全部code；A是混合 adversarial family" |
| `_tool_log_code_text` import 失败 | evaluator 崩溃 | 在本地定义，不从 B02 import |

---

## 11. 结论

执行 B01 requires-code 完整改造（字段 + 任务文案 + evaluator + 测试），不建议只修改 `requires_code` 一行。

修订后方案在初稿基础上解决了三个会导致运行时错误的 Bug（import 路径、代码检测污染、`backtest_executed` 漏判），修正了两个评分逻辑错误（cap 层级不一致、`metrics_artifact_saved` 权重不平衡），并补全了遗漏的 `expected_mcp_tools` 字段和 student opening 语法修正。
