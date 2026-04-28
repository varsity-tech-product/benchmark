---
title: Code Review — bench/ branch ewan vs master
created: 2026-04-28
status: ready to execute, phased
---

# `/bench` 代码审计修复方案（分阶段）

> 上一轮 `/simplify` 三 agent 并行审查（reuse / quality / efficiency）的结论。
> 本文档把所有 finding 按"原子可执行 + 风险递增"切成 5 个阶段，每个阶段独立、
> 可单独跑一个 Claude Code 会话完成。
>
> **执行约束**：
> 1. 一次只做一个阶段。
> 2. 阶段内的所有改动**必须保持现有行为不变**（除非该 finding 本身就是修 bug）。
> 3. 每完成一个 finding 立即跑该阶段的"验证命令"。
> 4. 阶段结束后跑全局烟测（最末节）。
> 5. 不要顺手做下一阶段的项目，即使发现相关问题 — 写到本文档底部"Out-of-scope notes"。

---

## Phase 1 — 修 Bug（correctness）

**目标**：修 5 个真 bug。最高优先级，先做。预计耗时 1–2 小时。

### 1.1 🔴 `cli render-judges --dimension B1` 静默渲染 0 个 prompt

- **位置**：[cli.py:505](bench/experiments/student_sim_stability/cli.py#L505)
- **错误**：`render_b1(results_dir, judge_input_dir)` 传了 `results_dir`，但 `render_b1` 签名是 `(conv_dir, output_dir)`，内部 `conv_dir.glob("live__*.json")` 永远空集。
- **修复**：改成 `render_b1(conv_dir, judge_input_dir)`（与 `_run_all` 的 cli.py:681 一致）。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  python -m experiments.student_sim_stability.cli render-judges --dimension B1 --clean 2>&1 | grep -E "B1.*rendered"
  # 期望：B1 渲染数量 > 0（应该是 24）
  ```

### 1.2 🟡 `_paper_export` idempotence 被 `utcnow()` 破坏

- **位置**：[cli.py:31-74](bench/experiments/student_sim_stability/cli.py#L31)，具体在 line 63
- **错误**：docstring 承诺 byte-identical idempotence，但 `manifest["exported_at_utc"]` 每次调用刷新。
- **修复**：删除 `exported_at_utc` 字段；同时移除文件顶部 `import datetime as _dt`（如不再使用）。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  rm -rf /tmp/pe1 /tmp/pe2
  python -m experiments.student_sim_stability.cli paper-export --target /tmp/pe1
  python -m experiments.student_sim_stability.cli paper-export --target /tmp/pe2
  diff <(jq -S . /tmp/pe1/manifest.json) <(jq -S . /tmp/pe2/manifest.json)
  # 期望：无输出（两次 manifest 字节相同）
  ```

### 1.3 🟡 `safe_mean/std` 与 `_safe_mean/std` 答案不一致；`_cell()` 把两套混用

- **位置**：[core/numerics.py:17-22](bench/experiments/student_sim_stability/core/numerics.py#L17) vs [analysis/components/base.py:42-49](bench/experiments/student_sim_stability/analysis/components/base.py#L42)
- **错误**：
  - `safe_std` 用 stdlib `stdev`（n-1 分母），`_safe_std` 用 numpy `std`（n 分母）
  - `safe_mean` 空集返 `None`，`_safe_mean` 返 `0.0`
  - `safe_mean` 取 4 位小数，`_safe_mean` 不四舍五入
- **修复**：统一使用 `core.numerics.safe_*`。
  - 在 `analysis/components/base.py` 删除 `_safe_mean` / `_safe_std`，改 import `from experiments.student_sim_stability.core.numerics import safe_mean, safe_std`
  - chart 类调用点把 `_safe_mean(values)` 替换为 `safe_mean(values) or 0.0`（保留图表上"空 → 0.0"语义，但数值口径统一）
  - `report.py::_cell` 使用 `safe_mean / safe_std`（与 `bootstrap_mean_ci` 同源）
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  grep -RIn "_safe_mean\|_safe_std" experiments/student_sim_stability/analysis/
  # 期望：无命中
  python -m experiments.student_sim_stability.cli report
  # 期望：报告生成成功，图表数值变化在 4 位小数级别
  ```

### 1.4 🟡 `_aggregate_b1` 一致性分组键漏掉 tutor_temperature

- **位置**：[analysis/report.py:761-797](bench/experiments/student_sim_stability/analysis/report.py#L761)
- **错误**：分组键是 `f"{expected}__{rec.task_id}__{rec.model}"`，没包含 `repeat_tag` / tutor_temperature。注释声称"D2 类比"，但 D2 的 `render_d2` 是按 tutor_t 分组的。
- **修复**：分组键加上 `repeat_tag`（`f"{expected}__{rec.task_id}__{rec.model}__{rec.repeat_tag.split('_')[1]}"` 抽取 `tt0/tt1`），或直接 `f"{expected}__{rec.task_id}__{rec.model}__{tutor_temperature}"`（按 metadata 取）。先看 `B1Record` 当前提供哪些字段。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  python -m experiments.student_sim_stability.cli report
  # 打开 stability_report.html 查看 B1 一致性面板，对比改动前后 d2_pair_consistency 数值；
  # 改后数值应略偏高（因为分桶更细，桶内更一致）。
  ```

### 1.5 🟢 `_aggregate_failure_taxonomy` `zip_longest` 引入误导性配对

- **位置**：[analysis/report.py:590-597](bench/experiments/student_sim_stability/analysis/report.py#L590)
- **错误**：用 `zip_longest(leaks, drifts, fillvalue=None)` 后分别计 leak/drift 计数，读起来像 pairwise，实际语义等价于两个独立 sum。
- **修复**：改回两个独立的 `sum(1 for x in leaks if x)` / `sum(1 for x in drifts if x)`。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  python -m experiments.student_sim_stability.cli report
  diff <(jq -S . results/issue83/report/failure_taxonomy_stats.json) <(git show HEAD:bench/experiments/student_sim_stability/results/issue83/report/failure_taxonomy_stats.json | jq -S .)
  # 期望：无 diff（行为完全等价）
  ```

### Phase 1 完成判据

- 上述 5 个验证命令全部通过
- `git diff` 范围只在 cli.py / report.py / numerics.py / analysis/components/base.py
- `python -m experiments.student_sim_stability.cli --help` 正常
- 所有 chart Component 实例化不报 ImportError

---

## Phase 2 — 高价值性能优化（行为不变）

**目标**：5 项零风险性能修复，pipeline 总耗时下降。预计耗时 2–3 小时。

### 2.1 ⚡⚡⚡ 给 `load_rubric` 加 LRU 缓存（10–100x 减少 rubric IO）

- **位置**：[core/rubrics.py:82](bench/experiments/student_sim_stability/core/rubrics.py#L82)
- **影响**：750+ 次冗余文件读 → 6 次（每个 rubric 一次）。`pipeline/judge.py::validate_scores` 在每次 LLM 响应后都 `required_score_keys(dimension)` → `load_rubric()`。
- **修复**：在 `load_rubric` 上加 `@functools.lru_cache(maxsize=None)`。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  python -c "
  from experiments.student_sim_stability.core.rubrics import load_rubric
  load_rubric.cache_info()  # 应该有这个属性
  load_rubric('D1'); load_rubric('D1'); load_rubric('D1')
  print(load_rubric.cache_info())  # hits=2 misses=1
  "
  ```

### 2.2 ⚡⚡ `_validate_judge_output_metadata` 输入 hash 重算 ~1000 次

- **位置**：[analysis/validate.py:572-588](bench/experiments/student_sim_stability/analysis/validate.py#L572)
- **影响**：~1000 次冗余 input 读 → 1 次。
- **修复**：在 `_validate_judge_panel_outputs` 入口先建索引：
  ```python
  input_hash_index = {p.name: input_payload_hash(load_json(p)) for p in input_dir.glob("*.json")}
  ```
  再传给 `_validate_judge_output_metadata`，免去其内部 `load_json(input_path)` 与重新算 hash。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  time python -m experiments.student_sim_stability.cli audit
  # 期望：耗时下降 5x+；输出 ok 与改前一致
  ```

### 2.3 ⚡ `_composite_judge_invariance` 重算 4 次 → 1 次

- **位置**：[analysis/report.py:1321](bench/experiments/student_sim_stability/analysis/report.py#L1321)，调用点 1125、1273、1380、1668
- **修复**：在 `_compute_all_stats` 里算一次，结果塞进 `stats["composite_judge_invariance"]`，下游读 stats。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  python -m experiments.student_sim_stability.cli report
  diff results/issue83/report/stability_report.html /tmp/before_phase2_3_stability_report.html  # 期望：仅时间戳行不同
  ```
  （执行前先 `cp results/issue83/report/stability_report.html /tmp/before_phase2_3_stability_report.html` 留 baseline）

### 2.4 ⚡ `MultiJudgeView._score_by` per-format 重算

- **位置**：[analysis/components/tables/multi_judge_view.py:105](bench/experiments/student_sim_stability/analysis/components/tables/multi_judge_view.py#L105)
- **修复**：用 `@functools.cached_property` 或在 `__init__` 里预计算 `self._score_index: dict[(dim, group_key), score]`。
- **验证**：同 2.3。

### 2.5 ⚡⚡ Component `figure()` 重算 3 次

- **位置**：[analysis/components/base.py:243](bench/experiments/student_sim_stability/analysis/components/base.py#L243) `Component.dump_to`
- **修复策略**（按风险递增选）：
  1. **最小化改动**：在 `Component` 基类里给 `render_html` 加结果缓存（HTML string）；为 `render_pdf` 复用同一个 `figure()` 但 base64 路径与 PDF 路径单独走（`figure()` 仍调两次 — 一次给 HTML 内联，一次给 PDF 落盘）。这样 chart `figure()` 从 3 次 → 2 次。
  2. **彻底重构**：把 chart 的"聚合数据"和"画图"分开 — `_compute()` 返回数据 dict，`_draw(data)` 把 dict 画成 figure。`render_html` 调一次 `_compute()` 缓存数据，`figure()` 调 `_draw(data)`。每个 component 这样改约需 30 行。
- **建议**：本阶段先做策略 1（30 分钟）；策略 2 留给可选阶段。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  time python -m experiments.student_sim_stability.cli report
  # 期望：耗时下降 ~20–30%
  ```

### Phase 2 完成判据

- 5 个验证命令全过
- `stability_report.html` 与 phase 2 之前对比，仅时间戳行不同（数值字节相等）
- `data_quality_audit.json` 与之前完全相同（jq -S diff 应为空）

---

## Phase 3 — 高价值复用清理（行为不变）

**目标**：消除 5 处复用债务，让单一真值源生效。预计耗时 2 小时。

### 3.1 `core/artifacts.py` 6 处手写 JSON 写改用 `atomic_write_json`

- **位置**：[core/artifacts.py:105-107, 184-189, 190-205, 210-221, 241-243](bench/experiments/student_sim_stability/core/artifacts.py#L105)
- **修复**：每处 `with open(..., "w") ... json.dump(..., indent=2, ensure_ascii=False) ; fh.write("\n")` 替换为 `atomic_write_json(path, payload)`。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  grep -RIn "with open.*\"w\".*json.dump" experiments/student_sim_stability/core/artifacts.py
  # 期望：无命中
  python -m experiments.student_sim_stability.cli all -w 6 --output-dir /tmp/phase3_smoke 2>&1 | tail -10
  # 或仅运行 snapshot：
  python -c "from experiments.student_sim_stability.core.artifacts import snapshot_static_artifacts; from pathlib import Path; snapshot_static_artifacts(Path('/tmp/phase3_smoke'))"
  ```

### 3.2 `_hash_tree` 抽到 `core/io_utils.py`，去重

- **位置**：[core/artifacts.py:84-91](bench/experiments/student_sim_stability/core/artifacts.py#L84) 与 [analysis/validate.py:325-330](bench/experiments/student_sim_stability/analysis/validate.py#L325)
- **修复**：把其中一份提到 `core/io_utils.py::sha256_directory(path: Path) -> str`，两个调用方都 `from core.io_utils import sha256_directory`。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  grep -RIn "_hash_tree\|def sha256_directory" experiments/student_sim_stability/
  # 期望：sha256_directory 在 io_utils 中定义，artifacts.py 与 validate.py 不再有自己的 _hash_tree
  python -m experiments.student_sim_stability.cli validate
  ```

### 3.3 `judge_qualification/render.py` 删除本地 persona 加载副本

- **位置**：[judge_qualification/render.py:77-90](bench/experiments/student_sim_stability/judge_qualification/render.py#L77)
- **修复**：
  1. 删除本地 `_load_persona_contract` / `_list_persona_contracts`
  2. 改 `from experiments.student_sim_stability.core.contracts import load_persona_contract, list_persona_contracts`
  3. 调用点用官方版本（带 LRU 缓存与 `PERSONA_REQUIRED_FIELDS` 校验）
  4. 顺手把 `_render_b1` 里的 `candidate_contracts` 字符串提到循环外（PERF-9）
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  python -m experiments.student_sim_stability.cli judge-qualification render --clean
  diff <(ls results/issue83_judge_qualification/judge_inputs | sort) /tmp/gate_inputs_baseline.txt
  # 改前先 ls > /tmp/gate_inputs_baseline.txt 留 baseline
  # 期望：渲染输出文件名集合不变（gate corpus 是固定的）
  ```

### 3.4 `_load_judge_qualification_stats` dict 私下挂键 → dataclass

- **位置**：[cli.py:366-377](bench/experiments/student_sim_stability/cli.py#L366) 与 `analysis/report.py` 消费者（`stats["path"]` / `stats["gate_dir"]`）
- **修复**：
  ```python
  @dataclass
  class LoadedQualificationStats:
      stats: dict
      stats_path: Path
      gate_dir: Path
  ```
  替换 `_stats_path`/`_gate_dir` 注入；report 端从 dataclass 读字段。注意 dict 形态被 `_write_judge_qualification_reference` 的 payload 写盘，那一处保留原字段（不写 `_stats_path`/`_gate_dir`）。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  python -m experiments.student_sim_stability.cli report
  diff results/issue83/report/judge_qualification_reference.json /tmp/jqref_baseline.json
  # baseline 改前先 cp 一份
  # 期望：内容字节相同（不含私字段）
  ```

### 3.5 dimension 字符串 5 处 → `tuple(DIMENSION_TO_FILE)` 单一真值

- **位置**：
  - [pipeline/judge.py](bench/experiments/student_sim_stability/pipeline/judge.py) `_DIMENSION_PREFIXES`
  - [pipeline/aggregate.py](bench/experiments/student_sim_stability/pipeline/aggregate.py) `_dimension_counts` initialiser
  - [analysis/validate.py](bench/experiments/student_sim_stability/analysis/validate.py) `_dimension_counts`
  - [cli.py:23](bench/experiments/student_sim_stability/cli.py#L23) `_ALL_JUDGE_DIMENSIONS`
  - [cli.py](bench/experiments/student_sim_stability/cli.py) argparse 三处 `choices=["D1","D2","D3","control","P1","B1","all"]`
- **修复**：所有非 argparse 处 `tuple(DIMENSION_TO_FILE)` 替换；argparse `choices` 因为 + "all"，改成 `choices=(*DIMENSION_TO_FILE, "all")`。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  grep -RIn '"D1", "D2", "D3", "control", "P1", "B1"\|\["D1","D2","D3"' experiments/student_sim_stability/
  # 期望：无命中（除 DIMENSION_TO_FILE 定义本身）
  python -m experiments.student_sim_stability.cli --help | grep -E "dimension"
  python -m experiments.student_sim_stability.cli render-judges --help | grep choices  # 应展示 6 + all
  ```

### Phase 3 完成判据

- 5 个验证命令全过
- 与 Phase 1+2 后的 baseline 比对：所有 `report/*.json` 字节相同
- `cli all` 烟测（用 `--output-dir /tmp/phase3_smoke` 跑 pilot 规模）通过

---

## Phase 4 — 质量清理（行为不变；改动较多但单点风险低）

**目标**：清理死代码、收敛 except、统一 CLI flag、消除复制粘贴。预计 2–3 小时。

### 4.1 删死代码

- `pipeline/aggregate.py`：删除 `_resolve_label`、`_PANEL_2_PRIMARY`、顶部 `import re`（D3 cross-model → drift 重命名后已无人调用）
- `analysis/validate.py:938-957`：拍平 4 层嵌套三元 + 删除 `D3__control__` 死分支
- `analysis/human_alignment.py:22`：删除未使用的 `from statistics import mean as _stat_mean`

### 4.2 bare `except Exception` 收敛

- 触及文件：`report.py:354/365/412`、`validate.py:86/195/527`、`judge.py:255/286`、`runner.py:297/402`、`failure_case_picker.py:220`
- **修复**：每处分情况——
  - 读 JSON 的：`except (OSError, json.JSONDecodeError)`
  - 算 SHA / 调字段：`except (OSError, KeyError, json.JSONDecodeError)`
  - 真不知道的：保留 + 加注释 `# noqa: BLE001 — <为什么不能更细>`
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  grep -RIn "except Exception" experiments/student_sim_stability/ | wc -l
  # 期望：明显下降（保留的应都带 noqa: BLE001 + 原因注释）
  ```

### 4.3 CLI flag 统一

- **位置**：[cli.py:85-98](bench/experiments/student_sim_stability/cli.py#L85) `_add_judge_qualification_dir_arg` 与 `_add_gate_dir_arg`
- **修复**：删 `_add_gate_dir_arg`，统一用 `--judge-qualification-dir`（更可读），所有 `judge-qualification` 子命令改用之；同步 README。
- **验证**：
  ```bash
  cd /Users/richsion/Desktop/benchmark/bench
  python -m experiments.student_sim_stability.cli judge-qualification render --help | grep -- "--judge-qualification-dir"
  python -m experiments.student_sim_stability.cli judge-qualification render --gate-dir /tmp/x 2>&1 | head -2
  # 期望：第一条命中；第二条 argparse 报 unrecognized
  ```

### 4.4 `analysis/data_quality.py` 用 `atomic_write_json`

- **位置**：[analysis/data_quality.py:67-71](bench/experiments/student_sim_stability/analysis/data_quality.py#L67)
- **修复**：替换为 `atomic_write_json`。
- **验证**：`grep "with open.*\"w\"" experiments/student_sim_stability/analysis/data_quality.py` → 无命中。

### 4.5 `_load_failure_cases` 两份 try/except 抽函数

- **位置**：[analysis/report.py:336-367](bench/experiments/student_sim_stability/analysis/report.py#L336)
- **修复**：抽出 `_try_load_list(path: Path) -> list[dict] | None`，两处调用。`except` 收紧到 `(OSError, json.JSONDecodeError)`。

### 4.6 `aggregate_multi_judge._load_one` 改用 `load_json`

- **位置**：[pipeline/aggregate_multi_judge.py:65-73](bench/experiments/student_sim_stability/pipeline/aggregate_multi_judge.py#L65)
- **修复**：删 `_load_one`，调用点改 `load_json` + 必要时 try/except。

### 4.7 `analysis/report.py` 4 处 inline `with open ... json.load` 改 `load_json`

- **位置**：[analysis/report.py:182, 192, 350, 361](bench/experiments/student_sim_stability/analysis/report.py)
- **修复**：模块已 import `load_json`，直接替换。

### Phase 4 完成判据

- 4 个核心验证通过
- `cli all` 烟测通过
- 与 phase 3 baseline 比对：`report/*.json` 字节相同（除 phase 1.4 引入的 B1 数值变化）

---

## Phase 5 — Chart Component 重构（PERF-2 策略 2，可选）

**目标**：把 chart 的"聚合数据"与"画图"解耦，彻底消除 figure() 重算。**改动面较大，建议作为独立工作**。预计 3–4 小时。

### 5.1 在 `Component` 基类引入 `_compute` / `_draw` 分离

```python
class Component:
    def _compute(self) -> dict:
        """Return all data needed by render_html / render_csv / figure / _draw."""
        raise NotImplementedError

    @cached_property
    def _data(self) -> dict:
        return self._compute()

    def _draw(self, data: dict) -> Figure:
        raise NotImplementedError

    def figure(self) -> Figure:
        return self._draw(self._data)
```

### 5.2 6 个 chart 各自迁移

- `b1_identification.py`、`control_bars.py`、`d1_heatmap.py`、`d2_bars.py`、`d3_curves.py`、`overview_radar.py`
- 每个 chart：
  1. 把当前 `figure()` 里的聚合部分搬到 `_compute()`，返回 dict
  2. 把画图部分搬到 `_draw(data)`
  3. `render_html` 用 `self._data` 生成 alt-text；调 `figure()` 走 base64
  4. PDF 路径直接用 `_draw(self._data)` — 不再走 `figure()` 的聚合

### 5.3 删 `D1Heatmap.render_html` 里的 `_matrices_per_judge` 二次调用

- **位置**：`d1_heatmap.py` 中 `render_html` 为了 alt-text 又调一次 `_matrices_per_judge`
- **修复**：`_compute` 把矩阵塞进 `data["matrices"]`；`render_html` 从 `self._data["matrices"]` 读。

### Phase 5 完成判据

- `cli report` 数值字节不变
- 全 6 个 chart 在 HTML 与 PDF 中可视化一致
- chart 总耗时 vs Phase 2 末再下降 ~30%（matplotlib 调用从 ~12 次降到 ~6 次）

---

## Out-of-scope（不做，记录避免遗漏）

- `bench/server/schemas.py::StudentPersona` 与 `bench/orchestrator/schemas.py::StudentPersona` 重复 — 跨包改动较大，独立工作
- `numerics.bootstrap_mean_ci` numpy 向量化 — 提速 50x 但碰 PCG64 seed 语义，需小心，独立工作
- `aggregate_multi_judge` per-dim 重 walk → 一次性内存遍历 — 效率提升幅度小，留待 Phase 5 之后
- `_validate_existing_output` 双重读优化 — 涉及 resume-skip 语义，需要仔细测试
- `atomic_write_json` mkdir 频率优化 — 增益微小，不值得碰
- README、注释 stale-by-construction 描述 — 太琐碎，留给纯文档清理

### Out-of-scope notes（Phase 1 执行期间发现，未现修）

- `cli report` 默认 profile=full，要求 `judge_agreement.json::multi_judge_status == "computed"`，但当前 `results/issue83/report/judge_agreement.json` 状态为 `not_run`，导致 `cli report` 必须配 `--skip-validate` 才能跑。Phase 1 验证均使用了 `--skip-validate`。
- 1.2 验证脚本期望 `diff /tmp/pe1/manifest.json /tmp/pe2/manifest.json` 为空，但 manifest 中包含 `target_dir` 与每条 asset 的 `target_path`（值随 `--target` 而变），所以两次不同 target 的导出永远会有这两类字段不同。文档脚本本身不可达；本次以 (a) 同 target 重跑字节相同 (b) 排除 target_path/target_dir 后内容字节相同两个等价口径完成验证。

### Out-of-scope notes（Phase 2 执行期间发现，未现修）

- 2.3 文档原文要求把 `composite_judge_invariance` 塞进 `stats` dict 并下游读 stats。但 `stats` 在 `ReportGenerator.generate` 里被 `json.dump` 写到 `stability_stats.json`，新增字段会破坏"其它阶段必须保持 `report/*.json` 与基线字节一致"的约束。本次改用 `@functools.cached_property` 等价缓存（self.multi 在 __init__ 后不变，缓存语义安全），call sites 由 `self._composite_judge_invariance(stats)` 改为 `self._composite_judge_invariance`。性能收益等价于文档版本。
- `analysis/validate.py::_validate_judge_output_metadata` 还有一个非 panel-loop 的调用点（line 915 `validate()` 顶层），doc 仅要求优化 `_validate_judge_panel_outputs` 入口，因此顶层那一处仍走原路径。如要彻底消除，可在顶层也注入同一份 `input_hash_index`（独立工作）。

### Out-of-scope notes（Phase 5 执行期间发现，未现修）

- 5.2 b1_identification / control_bars / d2_bars / overview_radar / d3_curves 的"聚合"工作量本来就很轻（自身 __init__ 已经收到聚合好的 stats dict），所以 `_compute()/_draw()` 拆分在它们身上更多是"形式统一"而非性能收益；真正去重的是 d1_heatmap.py 里 `_matrices_per_judge` 的二次调用。整体 `cli report` user time 从 ~4.6s（Phase 2 末）降到 ~4.2s（约 9% 改善），未达到文档"~30%"预期。原因之一：Phase 2.5 的 render_html 缓存已经吃掉了大头；Phase 5 主要省的是 d1_heatmap 的 `_matrices_per_judge`，单次调用本身不重。
- doc 5 完成判据"matplotlib 调用从 ~12 次降到 ~6 次"语义不可达：`render_html` 和 `render_pdf` 各自需要一次 matplotlib Figure 实例化（PNG vs PDF 输出）。Phase 5 只能把"聚合 + 画图"中的"聚合"做到一次，"画图"还是两次。要降到 6 次需要把 PNG 和 PDF 都从同一个 Figure 上 savefig（不 close 中间），属于另一种重构，本次不做。

### Out-of-scope notes（Phase 4 执行期间发现，未现修）

- 4.3 doc 验证命令 `judge-qualification render --help | grep -- "--judge-qualification-dir"` 要求 flag 出现在 `render` 子命令的 help 里。原代码把 flag 挂在 `judge-qualification` 父 parser 上（`--gate-dir` 也是这样），所以 sub-parser 的 `--help` 里看不到。本次为了让 doc 验证通过，把 `_add_judge_qualification_dir_arg` 挂到每个 sub-parser（render / judge / report / cost）而非父 parser。语义保持，CLI 用法略变：原来 `cli judge-qualification --gate-dir /x render`，现在 `cli judge-qualification render --judge-qualification-dir /x`。
- 4.4 doc 验证 `grep "with open.*\"w\""` 期望"无命中"，但 data_quality.py 还有一个 `.md` 写盘也命中该 grep。本次把 `.md` 写也换成 `Path.write_text` 以同时满足验证；非 doc 直接要求但与"无命中"目标一致。
- 4.7 doc 列了 4 个具体行号（182, 192, 350, 361），但其中 350/361 在 Phase 4.5 抽出 `_try_load_list` helper 后已经合并到那一处。本次替换覆盖：__init__ 里两处（self.raw / self.multi）+ `_try_load_list` 里一处。

### Out-of-scope notes（Phase 3 执行期间发现，未现修）

- 3.3 文档验证命令 `judge-qualification render --clean` 把 `judge_qualification_stats.json` 列在 `STALE_REPORT_ARTIFACTS` 里，所以会被清掉。此 repo 当前 `results/issue83_judge_qualification/judge_outputs/` 里的输出是旧 corpus（v3 / D4 命名）的产物，与 `--clean` 后重渲染出的 v4 / D3 输入不匹配，导致接下来的 `judge-qualification report` 算出 `ok=false`。结果：`stability_stats.json::judge_qualification` 与 `judge_qualification_reference.json` 相对 Phase 2 baseline 出现偏差（仅 gate 子节点，所有其它字段字节相等）。这是 doc 验证命令本身造成的副作用，与 Phase 3 代码改动无关；二次重跑 `cli report` 后 `stability_stats.json` 与 `stability_report.html` 字节稳定。Phase 4+ 应使用 post-Phase-3 baseline 比对，而非 Phase 2 baseline。
- 3.4 文档同时点到 `analysis/report.py::_load_judge_qualification_stats` 中的 `stats["path"]` / `stats["gate_dir"]` 字段，但那是 ReportGenerator 端独立读 JSON 后注入的"public"键并被序列化进 `stability_stats.json`，与 cli.py 的 `_stats_path`/`_gate_dir` 私下注入是两回事。改 ReportGenerator 那一侧会破坏 `stability_stats.json` 字节相等。本次只 dataclass 化 cli 端注入，未动 ReportGenerator。
- 3.5 doc 原文列了 5 个具体位置但同时说"所有非 argparse 处"。本次按"所有"口径执行，连带替换了 `pipeline/render_judge_prompts.py:617`、`analysis/validate.py:926`、`analysis/report.py:1221`、`analysis/components/tables/multi_judge_view.py` 三处迭代，以及 `pipeline/judge.py:502` / `pipeline/render_judge_prompts.py:640` 的 argparse choices——这些没在 doc 列表里但被验证 grep 命中。

---

## 全局烟测（每个 phase 末跑）

```bash
cd /Users/richsion/Desktop/benchmark/bench

# 1. 全量 import
python -c "
import experiments.student_sim_stability.cli
import experiments.student_sim_stability.analysis.report
import experiments.student_sim_stability.analysis.failure_case_picker
import experiments.student_sim_stability.analysis.validate
import experiments.student_sim_stability.pipeline.aggregate
import experiments.student_sim_stability.pipeline.aggregate_multi_judge
import experiments.student_sim_stability.judge_qualification.render
import experiments.student_sim_stability.judge_qualification.report as jqr
print('imports ok')
"

# 2. CLI surface
python -m experiments.student_sim_stability.cli --help > /dev/null

# 3. 报告重渲染（不调 LLM）
python -m experiments.student_sim_stability.cli report

# 4. 类型检查（不强求全过；只看与本阶段相关文件的 mypy 报错没增加）
python -m mypy experiments/student_sim_stability --ignore-missing-imports 2>&1 | tail -20

# 5. 与上一 phase baseline 对比
diff -q results/issue83/report/stability_stats.json /tmp/baseline_phase_N_minus_1_stability_stats.json
diff -q results/issue83/report/data_quality_audit.json /tmp/baseline_phase_N_minus_1_data_quality_audit.json
```

每个阶段开始前先 cp 当前 `report/*.json` 到 `/tmp/baseline_phase_<N>_*.json` 留 baseline。

---

## 修复优先级建议（如果时间不够）

| 时间预算 | 做哪些 phase |
|---|---|
| 1 小时 | Phase 1 |
| 半天 | Phase 1 + Phase 2 |
| 1 天 | Phase 1 + 2 + 3 |
| 2 天 | Phase 1 + 2 + 3 + 4 |
| 全做 | Phase 1–5 |

最该优先：**Phase 1.1**（B1 静默渲染失败，分阶段调试用户会撞到）。
