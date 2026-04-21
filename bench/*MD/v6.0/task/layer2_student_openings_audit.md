# Layer2 `student_openings` 乱码/截断审计报告

> Date: 2026-04-21
> Scope: `bench/tasks/layer2/**/*.json`
> Trigger: B02 中发现学生开场白出现 `architecture: a properly.` / `with proper architecture: a. I know what...` 一类拼接错误
> Goal: 全面审计 layer2 任务描述中的 `student_openings`，识别类似模板拼接、截断、残留语法错误，供 Claude 二次审核
> Status: 待 Claude 复核；本报告只记录调查结果，未修改任务 JSON

---

## 1. 结论摘要

只看 `bench/tasks/layer2`，确实存在与 B02 同源的学生开场白生成错误，而且不是孤例。

本次审计覆盖：

| 项目 | 数量 |
|------|------|
| layer2 JSON 任务文件 | 65 |
| 含 `student_openings` 的 layer2 文件 | 65 |
| `student_openings` 总条数 | 194 |
| 高置信应修开场白 | 75 |
| 轻微/建议复核开场白 | 11 |
| 涉及文件数 | 34 |

问题主要集中在这些目录：

| 目录 | 结论 |
|------|------|
| `backtest/` | B02-B06 多处截断，B02 最明显 |
| `data_analysis/` | D02-D04、D07-D11 多处模板残句 |
| `implementation/` | I02-I09 的 fullstack/developer 两类 persona 大面积截断 |
| `end_to_end/` | E01-E05 的 fullstack/developer 两类 persona 大面积语法错误 |
| `strategy/` | 目前只发现 S04 有明确截断 |
| `debug/` | X04、X09 高置信；另有 X03/X05/X07/X10 轻微截断味 |
| `adversarial/` | 未发现同类乱码/截断问题 |

核心判断：这些不是自然措辞问题，而是任务描述摘要被截断后，又拼接 persona 模板后缀造成的系统性生成残留。

---

## 2. 审计方法

本次审计分三步：

1. 先用关键词扫描 B02 同类片段：
   - `proper architecture: a`
   - `properly.`
   - `with.`
   - `from.`
   - `using.`
   - `LEAN's.`
   - `OnData +.`
   - `summary statistics,.`
2. 再解析所有 layer2 JSON，只检查 `student_openings` 字段。
3. 最后人工复核，剔除误报，例如正常的 `Should I trade it?`、`I can write it.` 等。

本报告没有审计 layer1。layer1 中也能看到类似 `I need to code I...` 的痕迹，但用户明确要求“只管 layer2 的任务”，因此此处不纳入统计。

---

## 3. 高置信应修问题

下表中的条目均属于明显语法错误、截断、或模板拼接残留，应优先修复。

### 3.1 Backtest

| 文件 | 行号 | Persona | 问题片段 |
|------|------|---------|----------|
| `bench/tasks/layer2/backtest/B02_basic_sequential_engine.json` | L15 | `fullstack_practitioner` | `proper architecture: a properly.` |
| `bench/tasks/layer2/backtest/B02_basic_sequential_engine.json` | L16 | `finance_veteran` | `proper architecture: a.` |
| `bench/tasks/layer2/backtest/B02_basic_sequential_engine.json` | L17 | `developer_crossover` | `proper architecture: a.` |
| `bench/tasks/layer2/backtest/B02_basic_sequential_engine.json` | L18 | `double_novice` | `proper architecture: a.` |
| `bench/tasks/layer2/backtest/B03_lookahead_prevention.json` | L15 | `fullstack_practitioner` | `architecturally prevents properly.` |
| `bench/tasks/layer2/backtest/B03_lookahead_prevention.json` | L16 | `finance_veteran` | `architecturally prevents.` |
| `bench/tasks/layer2/backtest/B03_lookahead_prevention.json` | L17 | `developer_crossover` | `architecturally prevents.` |
| `bench/tasks/layer2/backtest/B03_lookahead_prevention.json` | L18 | `double_novice` | `architecturally prevents.` |
| `bench/tasks/layer2/backtest/B04_multi_asset_sync.json` | L13 | `fullstack_practitioner` | `support synchronized properly.` |
| `bench/tasks/layer2/backtest/B04_multi_asset_sync.json` | L14 | `finance_veteran` | `support synchronized.` |
| `bench/tasks/layer2/backtest/B05_execution_simulation.json` | L13 | `fullstack_practitioner` | `realistic execution properly.` |
| `bench/tasks/layer2/backtest/B06_walkforward_validation.json` | L13 | `fullstack_practitioner` | `on top of a properly.` |
| `bench/tasks/layer2/backtest/B06_walkforward_validation.json` | L14 | `finance_veteran` | `on top of a.` |

Notes:
- B01 未发现同类问题。
- B05/B06 只有部分 persona 存在问题，说明不是整文件损坏，而是生成模板按 persona 分支出错。

### 3.2 Data Analysis

| 文件 | 行号 | Persona | 问题片段 |
|------|------|---------|----------|
| `bench/tasks/layer2/data_analysis/D02_missing_data_detection_handling.json` | L15 | `fullstack_practitioner` | `while rigorously.` |
| `bench/tasks/layer2/data_analysis/D02_missing_data_detection_handling.json` | L16 | `finance_veteran` | `while in Python.` |
| `bench/tasks/layer2/data_analysis/D02_missing_data_detection_handling.json` | L17 | `developer_crossover` | `while.` |
| `bench/tasks/layer2/data_analysis/D02_missing_data_detection_handling.json` | L18 | `double_novice` | `while.` |
| `bench/tasks/layer2/data_analysis/D03_data_type_conversion_validation.json` | L15 | `fullstack_practitioner` | `handle rigorously.` |
| `bench/tasks/layer2/data_analysis/D03_data_type_conversion_validation.json` | L16 | `finance_veteran` | `handle in Python.` |
| `bench/tasks/layer2/data_analysis/D03_data_type_conversion_validation.json` | L17 | `developer_crossover` | `handle.` |
| `bench/tasks/layer2/data_analysis/D03_data_type_conversion_validation.json` | L18 | `double_novice` | `handle.` |
| `bench/tasks/layer2/data_analysis/D04_ohlcv_summary_statistics.json` | L17 | `developer_crossover` | `summary statistics,.` |
| `bench/tasks/layer2/data_analysis/D04_ohlcv_summary_statistics.json` | L18 | `double_novice` | `summary statistics,.` |
| `bench/tasks/layer2/data_analysis/D07_broken_data_feed_diagnosis.json` | L15 | `fullstack_practitioner` | `I want to diagnosing...` |
| `bench/tasks/layer2/data_analysis/D07_broken_data_feed_diagnosis.json` | L16 | `finance_veteran` | `I need to diagnosing...` |
| `bench/tasks/layer2/data_analysis/D07_broken_data_feed_diagnosis.json` | L17 | `developer_crossover` | `I need to diagnosing...` |
| `bench/tasks/layer2/data_analysis/D07_broken_data_feed_diagnosis.json` | L18 | `double_novice` | `asked us to diagnosing...` |
| `bench/tasks/layer2/data_analysis/D08_alternative_data_integration.json` | L15 | `fullstack_practitioner` | `normalize rigorously.` |
| `bench/tasks/layer2/data_analysis/D08_alternative_data_integration.json` | L16 | `finance_veteran` | `normalize in Python.` |
| `bench/tasks/layer2/data_analysis/D08_alternative_data_integration.json` | L17 | `developer_crossover` | `normalize.` |
| `bench/tasks/layer2/data_analysis/D08_alternative_data_integration.json` | L18 | `double_novice` | `normalize.` |
| `bench/tasks/layer2/data_analysis/D09_feature_engineering_pipeline.json` | L15 | `fullstack_practitioner` | `then rigorously.` |
| `bench/tasks/layer2/data_analysis/D09_feature_engineering_pipeline.json` | L16 | `finance_veteran` | `then in Python.` |
| `bench/tasks/layer2/data_analysis/D09_feature_engineering_pipeline.json` | L17 | `developer_crossover` | `then.` |
| `bench/tasks/layer2/data_analysis/D09_feature_engineering_pipeline.json` | L18 | `double_novice` | `then.` |
| `bench/tasks/layer2/data_analysis/D10_historical_data_fetch.json` | L15 | `fullstack_practitioner` | `data from rigorously.` |
| `bench/tasks/layer2/data_analysis/D10_historical_data_fetch.json` | L16 | `finance_veteran` | `data from in Python.` |
| `bench/tasks/layer2/data_analysis/D10_historical_data_fetch.json` | L17 | `developer_crossover` | `data from.` |
| `bench/tasks/layer2/data_analysis/D10_historical_data_fetch.json` | L18 | `double_novice` | `data from.` |
| `bench/tasks/layer2/data_analysis/D11_realtime_data_fetch.json` | L15 | `fullstack_practitioner` | `streaming or polling rigorously.` |
| `bench/tasks/layer2/data_analysis/D11_realtime_data_fetch.json` | L16 | `finance_veteran` | `streaming or polling in Python.` |
| `bench/tasks/layer2/data_analysis/D11_realtime_data_fetch.json` | L17 | `developer_crossover` | `streaming or polling.` |
| `bench/tasks/layer2/data_analysis/D11_realtime_data_fetch.json` | L18 | `double_novice` | `streaming or polling.` |

Notes:
- D01、D05 未发现高置信问题。
- D06 只有轻微问题，见 §4。

### 3.3 Debug

| 文件 | 行号 | Persona | 问题片段 |
|------|------|---------|----------|
| `bench/tasks/layer2/debug/X04_returns_diff.json` | L13 | `fullstack_practitioner` | `returns calculation bug where.` |
| `bench/tasks/layer2/debug/X04_returns_diff.json` | L14 | `developer_crossover` | `returns calculation bug where.` |
| `bench/tasks/layer2/debug/X09_alpha_conflict.json` | L13 | `fullstack_practitioner` | `in a LEAN.` |
| `bench/tasks/layer2/debug/X09_alpha_conflict.json` | L14 | `developer_crossover` | `in a LEAN.` |

Notes:
- X01、X02、X06、X08 未发现高置信问题。
- X03、X05、X07、X10 有轻微截断味，见 §4。

### 3.4 End-to-End

| 文件 | 行号 | Persona | 问题片段 |
|------|------|---------|----------|
| `bench/tasks/layer2/end_to_end/E01_build_ma_system.json` | L13 | `fullstack_practitioner` | `I need to building...` |
| `bench/tasks/layer2/end_to_end/E01_build_ma_system.json` | L14 | `developer_crossover` | `I need to building...` |
| `bench/tasks/layer2/end_to_end/E02_research_to_implementation.json` | L13 | `fullstack_practitioner` | `I need to researching...` |
| `bench/tasks/layer2/end_to_end/E02_research_to_implementation.json` | L14 | `developer_crossover` | `I need to researching...` |
| `bench/tasks/layer2/end_to_end/E03_strategy_validation.json` | L13 | `fullstack_practitioner` | `I need to validating... with.` |
| `bench/tasks/layer2/end_to_end/E03_strategy_validation.json` | L14 | `developer_crossover` | `I need to validating... with.` |
| `bench/tasks/layer2/end_to_end/E04_production_debugging.json` | L13 | `fullstack_practitioner` | `I need to systematically debugging...` |
| `bench/tasks/layer2/end_to_end/E04_production_debugging.json` | L14 | `developer_crossover` | `I need to systematically debugging...` |
| `bench/tasks/layer2/end_to_end/E05_full_quant_workflow.json` | L13 | `fullstack_practitioner` | `I need to a complete... from.` |
| `bench/tasks/layer2/end_to_end/E05_full_quant_workflow.json` | L14 | `developer_crossover` | `I need to a complete... from.` |

Notes:
- E-series 的错误模式更像模板语法错误：`I need to + gerund` 或 `I need to + noun phrase`。
- 修复时应同时保证开场白仍然自然地区分 fullstack 与 developer persona。

### 3.5 Implementation

| 文件 | 行号 | Persona | 问题片段 |
|------|------|---------|----------|
| `bench/tasks/layer2/implementation/I02_trend_following.json` | L13 | `fullstack_practitioner` | `strategy as a.` |
| `bench/tasks/layer2/implementation/I02_trend_following.json` | L14 | `developer_crossover` | `strategy as a.` |
| `bench/tasks/layer2/implementation/I03_mean_reversion.json` | L13 | `fullstack_practitioner` | `with asymmetric.` |
| `bench/tasks/layer2/implementation/I03_mean_reversion.json` | L14 | `developer_crossover` | `with asymmetric.` |
| `bench/tasks/layer2/implementation/I04_multi_timeframe.json` | L13 | `fullstack_practitioner` | `uses 4h.` |
| `bench/tasks/layer2/implementation/I04_multi_timeframe.json` | L14 | `developer_crossover` | `uses 4h.` |
| `bench/tasks/layer2/implementation/I05_cross_asset.json` | L13 | `fullstack_practitioner` | `uses a.` |
| `bench/tasks/layer2/implementation/I05_cross_asset.json` | L14 | `developer_crossover` | `uses a.` |
| `bench/tasks/layer2/implementation/I06_multi_signal_sweep.json` | L13 | `fullstack_practitioner` | `across the full.` |
| `bench/tasks/layer2/implementation/I06_multi_signal_sweep.json` | L14 | `developer_crossover` | `across the full.` |
| `bench/tasks/layer2/implementation/I07_alpha_model.json` | L13 | `fullstack_practitioner` | `manual OnData +.` |
| `bench/tasks/layer2/implementation/I07_alpha_model.json` | L14 | `developer_crossover` | `manual OnData +.` |
| `bench/tasks/layer2/implementation/I08_multi_alpha.json` | L13 | `fullstack_practitioner` | `system using.` |
| `bench/tasks/layer2/implementation/I08_multi_alpha.json` | L14 | `developer_crossover` | `system using.` |
| `bench/tasks/layer2/implementation/I09_risk_management.json` | L13 | `fullstack_practitioner` | `models in LEAN's.` |
| `bench/tasks/layer2/implementation/I09_risk_management.json` | L14 | `developer_crossover` | `models in LEAN's.` |

Notes:
- I01 未发现问题。
- I10 未发现高置信问题。
- I02-I09 呈现高度规律：每个文件通常是 fullstack/developer 两条受影响，说明该 persona 模板批量生成时拿到的任务摘要被截断。

### 3.6 Strategy

| 文件 | 行号 | Persona | 问题片段 |
|------|------|---------|----------|
| `bench/tasks/layer2/strategy/S04_volume_microstructure_alpha.json` | L13 | `fullstack_practitioner` | `non-price data in.` |
| `bench/tasks/layer2/strategy/S04_volume_microstructure_alpha.json` | L14 | `finance_veteran` | `non-price data in.` |

Notes:
- S01、S02、S03、S05、S06 未发现同类高置信问题。

---

## 4. 轻微/建议复核问题

这些条目不如 §3 明显，有些只是表达不自然，但仍像模板压缩造成的半截任务名。建议 Claude 复核后决定是否顺手修。

| 文件 | 行号 | Persona | 问题片段 | 备注 |
|------|------|---------|----------|------|
| `bench/tasks/layer2/data_analysis/D04_ohlcv_summary_statistics.json` | L15 | `fullstack_practitioner` | `summary statistics, rigorously.` | 语法可懂但不自然 |
| `bench/tasks/layer2/data_analysis/D04_ohlcv_summary_statistics.json` | L16 | `finance_veteran` | `summary statistics, in Python.` | 语法可懂但逗号残留 |
| `bench/tasks/layer2/data_analysis/D06_tick_data_aggregation.json` | L15 | `fullstack_practitioner` | `save the result rigorously.` | 语义奇怪，应为“aggregate rigorously”或重写 |
| `bench/tasks/layer2/debug/X03_position_bug.json` | L13 | `fullstack_practitioner` | `in a Bollinger Band.` | 缺少 strategy / mean-reversion strategy |
| `bench/tasks/layer2/debug/X03_position_bug.json` | L14 | `developer_crossover` | `in a Bollinger Band.` | 同上 |
| `bench/tasks/layer2/debug/X05_timezone_merge.json` | L13 | `fullstack_practitioner` | `in a crypto-stock.` | 缺少 merge / dataset / pair |
| `bench/tasks/layer2/debug/X05_timezone_merge.json` | L14 | `developer_crossover` | `in a crypto-stock.` | 同上 |
| `bench/tasks/layer2/debug/X07_warmup_bug.json` | L13 | `fullstack_practitioner` | `in a LEAN EMA.` | 缺少 strategy / algorithm |
| `bench/tasks/layer2/debug/X07_warmup_bug.json` | L14 | `developer_crossover` | `in a LEAN EMA.` | 同上 |
| `bench/tasks/layer2/debug/X10_universe_stale.json` | L13 | `fullstack_practitioner` | `stale universe.` | 可懂，但像截断任务标题 |
| `bench/tasks/layer2/debug/X10_universe_stale.json` | L14 | `developer_crossover` | `stale universe.` | 同上 |

---

## 5. 未发现同类问题的区域

本次 layer2 范围内，以下区域没有发现 B02 同类的乱码/截断问题：

- `bench/tasks/layer2/adversarial/*.json`
- `bench/tasks/layer2/backtest/B01_interpret_metrics.json`
- `bench/tasks/layer2/data_analysis/D01_load_inspect_ohlcv.json`
- `bench/tasks/layer2/data_analysis/D05_return_computation.json`
- `bench/tasks/layer2/debug/X01_ma_offbyone.json`
- `bench/tasks/layer2/debug/X02_lookahead.json`
- `bench/tasks/layer2/debug/X06_overfit_single.json`
- `bench/tasks/layer2/debug/X08_order_type_bug.json`
- `bench/tasks/layer2/implementation/I01_implement_sma.json`
- `bench/tasks/layer2/implementation/I10_parameter_optimization.json`
- `bench/tasks/layer2/strategy/S01_ma_crossover.json`
- `bench/tasks/layer2/strategy/S02_trend_following_research.json`
- `bench/tasks/layer2/strategy/S03_mean_reversion_research.json`
- `bench/tasks/layer2/strategy/S05_cross_asset_alpha.json`
- `bench/tasks/layer2/strategy/S06_multi_signal_combination.json`

注意：这里的“未发现”只代表没有发现 B02 这类明显的开场白乱码/截断，不代表任务整体质量已审查通过。

---

## 6. 推测根因

从错误形态看，根因很可能是自动生成 `student_openings` 时：

1. 从 `description` 或任务标题中抽取一段任务摘要；
2. 对摘要做了不安全截断；
3. 再拼接 persona 固定后缀；
4. 截断点落在介词、冠词、并列结构、括号表达式或形容词后，导致残句。

典型证据：

| 错误 | 可能来自原描述 |
|------|----------------|
| `proper architecture: a.` | `proper architecture: a data replay module...` |
| `architecturally prevents.` | `architecturally prevents look-ahead bias...` |
| `support synchronized.` | `support synchronized multi-asset replay...` |
| `data from.` | `data from public APIs...` |
| `non-price data in.` | `non-price data in BTCUSDT futures...` |
| `manual OnData +.` | `manual OnData + Portfolio construction...` |
| `models in LEAN's.` | `models in LEAN's Algorithm Framework...` |

这说明修复不应只做正则替换。更稳妥的做法是回到每个任务的 `description`，重写自然开场白。

---

## 7. 修复建议

建议按优先级处理：

### P0：修复 §3 高置信问题

目标：
- 消除所有明显截断；
- 保留 persona 区分；
- 不改变任务目标、工具要求、评分逻辑。

修复原则：

| Persona | 应保持的口吻 |
|---------|--------------|
| `double_novice` | 新手，不懂 Python/finance，需要从基础讲起 |
| `finance_veteran` | 懂金融/交易逻辑，不熟代码/API |
| `developer_crossover` | 会写代码，但不懂金融意义 |
| `fullstack_practitioner` | 会工程实现，关心架构、边界、方法论 |

### P1：修复 §4 轻微问题

这些问题不一定会破坏任务执行，但会降低 benchmark 观感和学生模拟真实性。建议在 P0 后顺手清理。

### P2：加一个开场白 lint

建议新增一个轻量检查脚本，至少阻止以下模式再次进入任务库：

```python
BAD_OPENING_PATTERNS = [
    r":\s*(a|an|the)\.",
    r"\bproperly\.",
    r"\b(with|from|in|on|as|using|while|then)\.",
    r"\bI need to (building|researching|validating|diagnosing)\b",
    r",\.",
    r"\+\.",
    r"LEAN's\.",
]
```

同时要允许正常句子，例如：
- `Should I trade it?`
- `Can you write it?`
- `What should it do?`

因此 lint 不应只靠“句子以 it 结尾”这类宽泛规则，否则会误报 adversarial 任务。

---

## 8. 可复现扫描脚本

下面脚本可复现本报告的主体统计。它只扫描 layer2 的 `student_openings`。

```python
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

root = Path("bench/tasks/layer2")

definite = [
    r"proper architecture: a",
    r"architecturally prevents(?: properly)?\.",
    r"support synchronized(?: properly)?\.",
    r"realistic execution properly\.",
    r"walk-forward validation framework on top of a(?: properly)?\.",
    r"strategy as a\.",
    r"with asymmetric\.",
    r"uses 4h\.",
    r"uses a\.",
    r"across the full\.",
    r"OnData \+\.",
    r"system using\.",
    r"LEAN's\.",
    r"I need to building\b",
    r"I need to researching\b",
    r"I need to validating\b",
    r"I need to systematically debugging\b",
    r"I need to a complete\b",
    r"non-price data in\.",
    r"while(?: rigorously| in Python|\.)",
    r"handle(?: rigorously| in Python|\.)",
    r"statistics,\.",
    r"to diagnosing\b",
    r"normalize(?: rigorously| in Python|\.)",
    r"then(?: rigorously| in Python|\.)",
    r"data from(?: rigorously| in Python|\.)",
    r"streaming or polling(?: rigorously| in Python|\.)",
    r"bug where\.",
    r"in a LEAN\.",
]

mild = [
    r"statistics, rigorously\.",
    r"statistics, in Python\.",
    r"save the result rigorously\.",
    r"Bollinger Band\.",
    r"crypto-stock\.",
    r"LEAN EMA\.",
    r"stale universe\.",
]

rx_def = re.compile("|".join(definite), re.I)
rx_mild = re.compile("|".join(mild), re.I)

results = []
files_with_openings = 0
openings = 0

for path in sorted(root.rglob("*.json")):
    data = json.loads(path.read_text())
    student_openings = data.get("student_openings") or {}
    if student_openings:
        files_with_openings += 1

    lines = path.read_text().splitlines()
    for persona, opening in student_openings.items():
        openings += 1
        severity = None
        if rx_def.search(opening):
            severity = "definite"
        elif rx_mild.search(opening):
            severity = "mild"
        if not severity:
            continue

        line_no = "?"
        needle = f'"{persona}":'
        for i, line in enumerate(lines, 1):
            if needle in line:
                line_no = i
                break

        results.append((severity, path, line_no, persona, opening))

print("layer2_files_with_student_openings", files_with_openings)
print("layer2_openings", openings)
print("flagged", len(results), Counter(r[0] for r in results))
print("flagged_files", len(set(r[1] for r in results)))

by_severity = defaultdict(list)
for result in results:
    by_severity[result[0]].append(result)

for severity in ["definite", "mild"]:
    print()
    print("##", severity)
    for _, path, line_no, persona, opening in by_severity[severity]:
        first_sentence = opening.split(".")[0] + "."
        print(f"{path}:{line_no} {persona}: {first_sentence}")
```

Expected summary from this audit run:

```text
layer2_files_with_student_openings 65
layer2_openings 194
flagged 86 Counter({'definite': 75, 'mild': 11})
flagged_files 34
```

---

## 9. Claude 复核清单

请 Claude 重点检查：

1. 是否同意 §3 的 75 条都属于高置信应修问题。
2. 是否认为 §4 的 11 条应纳入同一轮修复。
3. 是否有本报告漏掉的 layer2 `student_openings` 问题。
4. 修复时是否应从 `description` 还原完整任务语义，而不是仅删掉残词。
5. 是否需要为任务库新增 lint，阻止 future generation 再次产生这类截断。

建议 Claude 不要只看正则结果，应抽样打开原 JSON，确认每条开场白是否仍然自然、符合 persona，并且不会泄露任务的全部 checklist。
