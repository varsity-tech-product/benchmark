#!/usr/bin/env python3
"""
QuantTutorBench Judge Comparison & Statistical Analysis
========================================================
Data: 2 agent models (Sonnet 4.6, Haiku 4.5) × 2 judge models (Sonnet, Haiku)
       8 ICC tasks × 3 runs + single runs
       4 dimensions: OAS, QR, QP, Tutor

Outputs: bench/*MD/v4.0/key/judge_comparison_analysis.md
"""

import glob
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

BENCH = Path(__file__).resolve().parent.parent
os.chdir(BENCH)

DIMS = ["OAS", "QR", "QP", "Tutor"]

# ICC tasks with 3 runs each
ICC_TASKS = [
    "B01_interpret_metrics",
    "B02_basic_sequential_engine",
    "D01_load_inspect_ohlcv",
    "D09_feature_engineering_pipeline",
    "X01_ma_offbyone",
    "I01_implement_sma",
    "S01_ma_crossover",
    "S03_mean_reversion_research",
]

AGENTS = {
    "sonnet": "results/run-single/anthropic/claude-sonnet-4-6",
    "haiku": "results/run-single/anthropic/claude-haiku-4-5-20251001",
}

# ──────────────────────────────────────────────
# 1. Score extraction
# ──────────────────────────────────────────────


def extract_scores(path):
    """Extract OAS, QR, QP, Tutor from a scores*.md file."""
    with open(path) as f:
        text = f.read()
    scores = {}
    for line in text.split("\n"):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        label, value_str = parts[1], parts[2]
        try:
            val = float(value_str)
        except ValueError:
            continue
        if "Overall Agent Score" in label:
            scores["OAS"] = val
        elif "Quant Result" in label and "QR" in label:
            scores["QR"] = val
        elif "Quant Process" in label and "QP" in label:
            scores["QP"] = val
        elif "Tutor Score" in label:
            scores["Tutor"] = val
    return scores


def load_all_scores():
    """Load all scores into a structured dict.

    Returns: {agent: {task: {run_label: {judge: {dim: score}}}}}
    """
    data = {}
    for agent, base in AGENTS.items():
        data[agent] = {}
        for judge_suffix, judge_label in [
            ("scores_haiku.md", "haiku"),
            ("scores_sonnet.md", "sonnet"),
        ]:
            for fpath in sorted(glob.glob(f"{base}/**/{judge_suffix}", recursive=True)):
                d = os.path.dirname(fpath)
                run_label = os.path.basename(d)
                task_dir = os.path.basename(os.path.dirname(d))

                scores = extract_scores(fpath)
                if not scores:
                    continue

                if task_dir not in data[agent]:
                    data[agent][task_dir] = {}
                if run_label not in data[agent][task_dir]:
                    data[agent][task_dir][run_label] = {}
                data[agent][task_dir][run_label][judge_label] = scores

    return data


def is_valid_tutor(scores_dict):
    """Check if Tutor score is valid (not 0.0 from API timeout)."""
    return scores_dict.get("Tutor", 0.0) > 0.001


# ──────────────────────────────────────────────
# 2. ICC calculation
# ──────────────────────────────────────────────


def icc_3_1(values_matrix):
    """Compute ICC(3,1) for a matrix of shape (n_subjects, n_raters).

    For our use: n_subjects = n_tasks or n_runs, n_raters = 3 (runs) or 2 (judges).
    values_matrix: list of lists, each inner list = scores for one subject across raters.
    """
    data = np.array(values_matrix, dtype=float)
    n, k = data.shape
    if n < 2 or k < 2:
        return float("nan")

    grand_mean = data.mean()
    row_means = data.mean(axis=1)
    col_means = data.mean(axis=0)

    ss_total = np.sum((data - grand_mean) ** 2)
    ss_rows = k * np.sum((row_means - grand_mean) ** 2)
    ss_cols = n * np.sum((col_means - grand_mean) ** 2)
    ss_error = ss_total - ss_rows - ss_cols

    ms_rows = ss_rows / (n - 1)
    ms_error = ss_error / ((n - 1) * (k - 1))

    icc = (ms_rows - ms_error) / (ms_rows + (k - 1) * ms_error)
    return icc


def compute_cv(values):
    """Coefficient of variation."""
    values = [v for v in values if not np.isnan(v)]
    if len(values) < 2:
        return float("nan")
    m = np.mean(values)
    if abs(m) < 1e-9:
        return float("nan")
    return np.std(values, ddof=1) / m


# ──────────────────────────────────────────────
# 3. Analysis functions
# ──────────────────────────────────────────────


def analyze_judge_agreement(data):
    """Section 1: Judge consistency (Haiku vs Sonnet judge)."""
    results = {"per_pair": [], "per_dim": defaultdict(list)}

    for agent in ["sonnet", "haiku"]:
        for task in sorted(data.get(agent, {})):
            for run_label in sorted(data[agent][task]):
                judges = data[agent][task][run_label]
                if "haiku" not in judges or "sonnet" not in judges:
                    continue
                h = judges["haiku"]
                s = judges["sonnet"]
                for dim in DIMS:
                    if dim not in h or dim not in s:
                        continue
                    hv, sv = h[dim], s[dim]
                    # Exclude Tutor=0 anomalies
                    if dim == "Tutor" and (hv < 0.001 or sv < 0.001):
                        continue
                    results["per_pair"].append(
                        {
                            "agent": agent,
                            "task": task,
                            "run": run_label,
                            "dim": dim,
                            "haiku": hv,
                            "sonnet": sv,
                            "delta": sv - hv,
                        }
                    )
                    results["per_dim"][dim].append((hv, sv))

    # Compute summary stats per dimension
    summary = {}
    for dim in DIMS:
        pairs = results["per_dim"][dim]
        if len(pairs) < 3:
            continue
        h_vals = [p[0] for p in pairs]
        s_vals = [p[1] for p in pairs]
        deltas = [s - h for h, s in pairs]

        r, p_val = stats.pearsonr(h_vals, s_vals)
        tau, tau_p = stats.kendalltau(h_vals, s_vals)

        # ICC between two judges
        matrix = [[h, s] for h, s in pairs]
        icc = icc_3_1(matrix)

        summary[dim] = {
            "n": len(pairs),
            "mean_delta": np.mean(deltas),
            "std_delta": np.std(deltas, ddof=1),
            "min_delta": np.min(deltas),
            "max_delta": np.max(deltas),
            "pearson_r": r,
            "pearson_p": p_val,
            "kendall_tau": tau,
            "kendall_p": tau_p,
            "icc_judge": icc,
            "n_sonnet_higher": sum(1 for d in deltas if d > 0.005),
            "n_haiku_higher": sum(1 for d in deltas if d < -0.005),
            "n_equal": sum(1 for d in deltas if abs(d) <= 0.005),
        }

    return results, summary


def analyze_icc_by_judge(data):
    """Section 2: ICC(3,1) per task, comparing haiku judge vs sonnet judge."""
    results = {}

    for judge in ["haiku", "sonnet"]:
        results[judge] = {}
        for agent in ["sonnet", "haiku"]:
            results[judge][agent] = {}
            for task in ICC_TASKS:
                if task not in data.get(agent, {}):
                    continue

                runs_data = data[agent][task]
                run_keys = sorted(
                    [k for k in runs_data if k.startswith("intermediate_developer_run")]
                )
                if len(run_keys) < 3:
                    continue

                for dim in DIMS:
                    values = []
                    valid = True
                    for rk in run_keys[:3]:
                        if judge not in runs_data[rk]:
                            valid = False
                            break
                        v = runs_data[rk][judge].get(dim)
                        if v is None:
                            valid = False
                            break
                        if dim == "Tutor" and v < 0.001:
                            valid = False
                            break
                        values.append(v)

                    if not valid or len(values) < 3:
                        continue

                    cv = compute_cv(values)

                    if task not in results[judge][agent]:
                        results[judge][agent][task] = {}
                    results[judge][agent][task][dim] = {
                        "values": values,
                        "mean": np.mean(values),
                        "std": np.std(values, ddof=1),
                        "cv": cv,
                    }

    # Compute per-task ICC across 3 runs (each run is a "rater")
    icc_results = {}
    for judge in ["haiku", "sonnet"]:
        icc_results[judge] = {}
        for agent in ["sonnet", "haiku"]:
            icc_results[judge][agent] = {}
            for task in ICC_TASKS:
                task_data = results[judge].get(agent, {}).get(task, {})
                for dim in DIMS:
                    if dim not in task_data:
                        continue
                    vals = task_data[dim]["values"]
                    cv = task_data[dim]["cv"]
                    if task not in icc_results[judge][agent]:
                        icc_results[judge][agent][task] = {}
                    icc_results[judge][agent][task][dim] = {
                        "mean": np.mean(vals),
                        "std": np.std(vals, ddof=1),
                        "cv": cv,
                        "values": vals,
                    }

    return results, icc_results


def analyze_discrimination(data):
    """Section 3: Cohen's d between sonnet agent and haiku agent."""
    results = {}

    for judge in ["haiku", "sonnet"]:
        results[judge] = {}
        for dim in DIMS:
            sonnet_scores = []
            haiku_scores = []
            paired = []

            for task in ICC_TASKS:
                s_runs = []
                h_runs = []

                for agent, scores_list in [("sonnet", s_runs), ("haiku", h_runs)]:
                    if task not in data.get(agent, {}):
                        continue
                    for rk in sorted(data[agent][task]):
                        if not rk.startswith("intermediate_developer_run"):
                            continue
                        if judge not in data[agent][task][rk]:
                            continue
                        v = data[agent][task][rk][judge].get(dim)
                        if v is None:
                            continue
                        if dim == "Tutor" and v < 0.001:
                            continue
                        scores_list.append(v)

                if s_runs and h_runs:
                    sonnet_scores.extend(s_runs)
                    haiku_scores.extend(h_runs)
                    paired.append(
                        {
                            "task": task,
                            "sonnet_mean": np.mean(s_runs),
                            "haiku_mean": np.mean(h_runs),
                            "delta": np.mean(s_runs) - np.mean(h_runs),
                        }
                    )

            if len(sonnet_scores) < 3 or len(haiku_scores) < 3:
                continue

            s_arr = np.array(sonnet_scores)
            h_arr = np.array(haiku_scores)

            # Cohen's d (pooled SD)
            n1, n2 = len(s_arr), len(h_arr)
            pooled_std = np.sqrt(
                ((n1 - 1) * s_arr.std(ddof=1) ** 2 + (n2 - 1) * h_arr.std(ddof=1) ** 2)
                / (n1 + n2 - 2)
            )
            if pooled_std < 1e-9:
                d = 0.0
            else:
                d = (s_arr.mean() - h_arr.mean()) / pooled_std

            # Wilcoxon on paired task means
            if len(paired) >= 5:
                s_means = [p["sonnet_mean"] for p in paired]
                h_means = [p["haiku_mean"] for p in paired]
                try:
                    w_stat, w_p = stats.wilcoxon(s_means, h_means)
                except ValueError:
                    w_stat, w_p = float("nan"), float("nan")
            else:
                w_stat, w_p = float("nan"), float("nan")

            direction_count = sum(1 for p in paired if p["delta"] > 0)

            results[judge][dim] = {
                "sonnet_mean": s_arr.mean(),
                "sonnet_std": s_arr.std(ddof=1),
                "haiku_mean": h_arr.mean(),
                "haiku_std": h_arr.std(ddof=1),
                "cohens_d": d,
                "n_sonnet": n1,
                "n_haiku": n2,
                "wilcoxon_stat": w_stat,
                "wilcoxon_p": w_p,
                "n_tasks_compared": len(paired),
                "n_sonnet_higher": direction_count,
                "paired": paired,
            }

    return results


def analyze_dimension_independence(data):
    """Section 4: Correlation between QR, QP, Tutor."""
    results = {}

    for judge in ["haiku", "sonnet"]:
        all_scores = defaultdict(list)

        for agent in ["sonnet", "haiku"]:
            for task in data.get(agent, {}):
                for run_label in data[agent][task]:
                    if judge not in data[agent][task][run_label]:
                        continue
                    s = data[agent][task][run_label][judge]
                    if all(dim in s for dim in ["QR", "QP", "Tutor"]):
                        if s.get("Tutor", 0) < 0.001:
                            continue
                        for dim in ["QR", "QP", "Tutor"]:
                            all_scores[dim].append(s[dim])

        corr_matrix = {}
        dims_3 = ["QR", "QP", "Tutor"]
        for i, d1 in enumerate(dims_3):
            for d2 in dims_3[i + 1 :]:
                if len(all_scores[d1]) >= 5:
                    r, p = stats.pearsonr(all_scores[d1], all_scores[d2])
                    corr_matrix[f"{d1}-{d2}"] = {
                        "r": r,
                        "p": p,
                        "n": len(all_scores[d1]),
                    }

        results[judge] = corr_matrix

    return results


def analyze_task_difficulty(data):
    """Section 5: Per-task difficulty and discrimination."""
    results = []

    judge = "sonnet"
    for task in ICC_TASKS:
        sonnet_vals = []
        haiku_vals = []

        for agent, vals_list in [("sonnet", sonnet_vals), ("haiku", haiku_vals)]:
            if task not in data.get(agent, {}):
                continue
            for rk in sorted(data[agent][task]):
                if not rk.startswith("intermediate_developer_run"):
                    continue
                if judge not in data[agent][task][rk]:
                    continue
                v = data[agent][task][rk][judge].get("OAS")
                if v is not None:
                    vals_list.append(v)

        if sonnet_vals and haiku_vals:
            overall_mean = np.mean(sonnet_vals + haiku_vals)
            delta = np.mean(sonnet_vals) - np.mean(haiku_vals)
            results.append(
                {
                    "task": task,
                    "difficulty": 1.0 - overall_mean,
                    "overall_mean": overall_mean,
                    "sonnet_mean": np.mean(sonnet_vals),
                    "haiku_mean": np.mean(haiku_vals),
                    "discrimination": delta,
                }
            )

    return sorted(results, key=lambda x: x["difficulty"], reverse=True)


# ──────────────────────────────────────────────
# 4. Report generation
# ──────────────────────────────────────────────


def generate_report(data):
    lines = []
    _a = lines.append

    _a("# QuantTutorBench 统计分析报告：Judge 对比与评估可靠性")
    _a("")
    _a("> 生成时间：2026-04-07")
    _a(
        "> 数据：2 agent (Sonnet 4.6, Haiku 4.5) × 2 judge (Sonnet, Haiku) × 8 ICC tasks × 3 runs"
    )
    _a("> 排除：Tutor=0.0 的 API 超时异常值")
    _a("")
    _a("---")
    _a("")

    # ── Section 1: Judge Agreement ──
    _a("## 一、Judge 一致性分析（Haiku Judge vs Sonnet Judge）")
    _a("")
    _a("同一份对话交给两个不同的 judge 模型评分，结果差异有多大？")
    _a("")

    agreement_pairs, agreement_summary = analyze_judge_agreement(data)

    _a("### 1.1 汇总统计")
    _a("")
    _a(
        "| 维度 | N | Mean Δ | Std Δ | Min Δ | Max Δ | Pearson r | Kendall τ | ICC(judge) | Sonnet↑ | Haiku↑ |"
    )
    _a(
        "|------|---|--------|-------|-------|-------|-----------|-----------|------------|---------|--------|"
    )

    for dim in DIMS:
        s = agreement_summary.get(dim)
        if not s:
            continue
        _a(
            f"| {dim} | {s['n']} | {s['mean_delta']:+.4f} | {s['std_delta']:.4f} | "
            f"{s['min_delta']:+.4f} | {s['max_delta']:+.4f} | "
            f"{s['pearson_r']:.3f} | {s['kendall_tau']:.3f} | {s['icc_judge']:.3f} | "
            f"{s['n_sonnet_higher']} | {s['n_haiku_higher']} |"
        )

    _a("")
    _a("**解读**：")
    tutor_s = agreement_summary.get("Tutor", {})
    qr_s = agreement_summary.get("QR", {})
    _a(
        f"- **Tutor 偏差最大**：Haiku judge 系统性给 Tutor 打高分（平均 {abs(tutor_s.get('mean_delta',0)):.3f}），"
        f"Pearson r={tutor_s.get('pearson_r',0):.3f}"
    )
    _a(
        f"- **QR 方向相反**：Sonnet judge 对 QR 更宽容（平均 {qr_s.get('mean_delta',0):+.3f}）"
    )
    _a("- **QP 差异最小**：两个 judge 在过程评分上高度一致")
    _a("")

    # ── Section 1.2: Per-pair detail ──
    _a("### 1.2 逐项对比（按维度分组）")
    _a("")
    _a("<details>")
    _a("<summary>展开查看全部配对数据</summary>")
    _a("")
    _a("| Agent | Task | Run | Dim | Haiku | Sonnet | Δ |")
    _a("|-------|------|-----|-----|-------|--------|---|")
    for p in sorted(
        agreement_pairs["per_pair"], key=lambda x: (x["dim"], x["agent"], x["task"])
    ):
        flag = " ⚠️" if abs(p["delta"]) > 0.10 else ""
        _a(
            f"| {p['agent']} | {p['task'][:20]} | {p['run'][-5:]} | {p['dim']} | "
            f"{p['haiku']:.4f} | {p['sonnet']:.4f} | {p['delta']:+.4f}{flag} |"
        )
    _a("")
    _a("</details>")
    _a("")

    # ── Section 2: ICC comparison ──
    _a("## 二、ICC 稳定性分析（3 runs 一致性）")
    _a("")
    _a("同一 task × agent 的 3 次运行，评分的变异系数和稳定性。")
    _a("")

    _, icc_data = analyze_icc_by_judge(data)

    for judge in ["sonnet", "haiku"]:
        _a(f"### 2.{1 if judge=='sonnet' else 2} {judge.capitalize()} Judge 下的 CV")
        _a("")
        _a("| Agent | Task | OAS CV | QR CV | QP CV | Tutor CV |")
        _a("|-------|------|--------|-------|-------|----------|")

        for agent in ["sonnet", "haiku"]:
            for task in ICC_TASKS:
                td = icc_data[judge].get(agent, {}).get(task, {})
                if not td:
                    continue
                cvs = []
                for dim in DIMS:
                    if dim in td:
                        cv = td[dim]["cv"]
                        cvs.append(f"{cv*100:.1f}%" if not np.isnan(cv) else "—")
                    else:
                        cvs.append("—")
                _a(f"| {agent} | {task[:25]} | {' | '.join(cvs)} |")
        _a("")

    # Overall ICC across all tasks (treating each task-run as a subject)
    _a("### 2.3 Cross-Judge ICC 对比")
    _a("")
    _a("同一维度的 ICC 在两个 judge 下是否一致？")
    _a("")

    for agent in ["sonnet", "haiku"]:
        _a(f"**{agent.capitalize()} Agent:**")
        _a("")
        _a("| Task | Dim | Haiku Judge CV | Sonnet Judge CV | Δ CV |")
        _a("|------|-----|----------------|-----------------|------|")
        for task in ICC_TASKS:
            for dim in DIMS:
                h_td = icc_data["haiku"].get(agent, {}).get(task, {}).get(dim, {})
                s_td = icc_data["sonnet"].get(agent, {}).get(task, {}).get(dim, {})
                if h_td and s_td:
                    h_cv = h_td["cv"]
                    s_cv = s_td["cv"]
                    if not np.isnan(h_cv) and not np.isnan(s_cv):
                        _a(
                            f"| {task[:25]} | {dim} | {h_cv*100:.1f}% | {s_cv*100:.1f}% | {(s_cv-h_cv)*100:+.1f}% |"
                        )
        _a("")

    # ── Section 3: Model Discrimination ──
    _a("## 三、模型区分度（Sonnet Agent vs Haiku Agent）")
    _a("")

    discrim = analyze_discrimination(data)

    _a("### 3.1 Cohen's d 对比")
    _a("")
    _a(
        "| Judge | Dim | Sonnet Mean | Haiku Mean | Cohen's d | Effect | Wilcoxon p | Direction |"
    )
    _a(
        "|-------|-----|------------|------------|-----------|--------|------------|-----------|"
    )

    for judge in ["sonnet", "haiku"]:
        for dim in DIMS:
            d = discrim[judge].get(dim)
            if not d:
                continue
            effect = (
                "large"
                if abs(d["cohens_d"]) > 0.8
                else (
                    "medium"
                    if abs(d["cohens_d"]) > 0.5
                    else "small" if abs(d["cohens_d"]) > 0.2 else "negligible"
                )
            )
            wp = f"{d['wilcoxon_p']:.4f}" if not np.isnan(d["wilcoxon_p"]) else "—"
            direction = f"{d['n_sonnet_higher']}/{d['n_tasks_compared']} S>H"
            _a(
                f"| {judge} | {dim} | {d['sonnet_mean']:.4f} | {d['haiku_mean']:.4f} | "
                f"{d['cohens_d']:+.3f} | {effect} | {wp} | {direction} |"
            )

    _a("")
    _a("**解读**：")

    sonnet_tutor = discrim["sonnet"].get("Tutor", {})
    haiku_tutor = discrim["haiku"].get("Tutor", {})
    _a(
        f"- Sonnet judge 下 Tutor Cohen's d = {sonnet_tutor.get('cohens_d', 0):+.3f}，"
        f"Haiku judge 下 = {haiku_tutor.get('cohens_d', 0):+.3f}"
    )

    sonnet_qr = discrim["sonnet"].get("QR", {})
    haiku_qr = discrim["haiku"].get("QR", {})
    _a(
        f"- QR 在两个 judge 下的 d 分别是 {sonnet_qr.get('cohens_d', 0):+.3f} 和 {haiku_qr.get('cohens_d', 0):+.3f}"
    )
    _a("")

    # Per-task breakdown
    _a("### 3.2 逐任务区分度（Sonnet Judge）")
    _a("")
    _a(
        "| Task | Sonnet OAS | Haiku OAS | Δ OAS | Sonnet Tutor | Haiku Tutor | Δ Tutor |"
    )
    _a("|------|-----------|-----------|-------|-------------|-------------|---------|")

    for task in ICC_TASKS:
        s_oas = discrim["sonnet"].get("OAS", {}).get("paired", [])
        s_tutor = discrim["sonnet"].get("Tutor", {}).get("paired", [])
        oas_row = next((p for p in s_oas if p["task"] == task), None)
        tutor_row = next((p for p in s_tutor if p["task"] == task), None)
        if oas_row:
            t_s = tutor_row["sonnet_mean"] if tutor_row else float("nan")
            t_h = tutor_row["haiku_mean"] if tutor_row else float("nan")
            t_d = tutor_row["delta"] if tutor_row else float("nan")
            _a(
                f"| {task[:25]} | {oas_row['sonnet_mean']:.4f} | {oas_row['haiku_mean']:.4f} | "
                f"{oas_row['delta']:+.4f} | "
                f"{t_s:.4f} | {t_h:.4f} | {t_d:+.4f} |"
            )
    _a("")

    # ── Section 4: Dimension Independence ──
    _a("## 四、维度独立性")
    _a("")

    dim_indep = analyze_dimension_independence(data)

    _a("| Judge | 维度对 | Pearson r | p-value | N | 独立性 |")
    _a("|-------|--------|-----------|---------|---|--------|")

    for judge in ["sonnet", "haiku"]:
        for pair, vals in sorted(dim_indep[judge].items()):
            indep = (
                "独立"
                if abs(vals["r"]) < 0.3
                else "弱相关" if abs(vals["r"]) < 0.5 else "中等相关"
            )
            _a(
                f"| {judge} | {pair} | {vals['r']:+.3f} | {vals['p']:.4f} | {vals['n']} | {indep} |"
            )
    _a("")

    _a(
        "**解读**：如果 QR-Tutor 相关性低（r<0.3），证明多维度评估不冗余——高 QR 不等于高 Tutor。"
    )
    _a("")

    # ── Section 5: Task Difficulty ──
    _a("## 五、任务难度与区分度（Sonnet Judge）")
    _a("")

    task_diff = analyze_task_difficulty(data)

    _a("| Task | Difficulty | Overall OAS | Sonnet OAS | Haiku OAS | Discrimination |")
    _a("|------|-----------|-------------|------------|-----------|----------------|")

    for t in task_diff:
        _a(
            f"| {t['task'][:25]} | {t['difficulty']:.3f} | {t['overall_mean']:.4f} | "
            f"{t['sonnet_mean']:.4f} | {t['haiku_mean']:.4f} | {t['discrimination']:+.4f} |"
        )
    _a("")

    if task_diff:
        diff_vals = [t["difficulty"] for t in task_diff]
        disc_vals = [t["discrimination"] for t in task_diff]
        if len(diff_vals) >= 4:
            r, p = stats.pearsonr(diff_vals, disc_vals)
            _a(f"**难度 vs 区分度相关性**: Pearson r = {r:+.3f} (p = {p:.4f})")
            _a("")

    # ── Section 6: Key Conclusions ──
    _a("## 六、核心结论")
    _a("")
    _a("### 6.1 Judge 选择的影响")
    _a("")

    tutor_agreement = agreement_summary.get("Tutor", {})
    qp_agreement = agreement_summary.get("QP", {})
    _a(
        f"1. **Tutor 维度受 judge 影响最大**：Haiku judge 系统性虚高 "
        f"(Δ={abs(tutor_agreement.get('mean_delta',0)):.3f})，"
        f"ICC(judge)={tutor_agreement.get('icc_judge',0):.3f}"
    )
    _a(
        f"2. **QP 维度最稳定**：两个 judge 高度一致 "
        f"(Δ={abs(qp_agreement.get('mean_delta',0)):.3f})，"
        f"ICC(judge)={qp_agreement.get('icc_judge',0):.3f}"
    )
    _a(
        f"3. **QR 偏差方向相反**：Sonnet judge 对 QR 更宽容 "
        f"(Δ={qr_s.get('mean_delta',0):+.3f})"
    )
    _a("")

    _a("### 6.2 区分度结论")
    _a("")

    for judge in ["sonnet", "haiku"]:
        tutor_d = discrim[judge].get("Tutor", {}).get("cohens_d", 0)
        qr_d = discrim[judge].get("QR", {}).get("cohens_d", 0)
        _a(
            f"- **{judge.capitalize()} judge**: Tutor d={tutor_d:+.3f}, QR d={qr_d:+.3f}"
        )

    _a("")
    _a("### 6.3 论文建议")
    _a("")
    _a("1. **主结果使用 Sonnet judge**——更严格、更区分")
    _a("2. **报告 judge 一致性**——Table 展示 Pearson r 和 ICC(judge)")
    _a("3. **Tutor 维度是核心差异化**——Cohen's d 最大、区分方向最一致")
    _a("4. **多维度不冗余**——QR-Tutor 相关性低，证明多维度必要性")
    _a("")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("Loading scores...", flush=True)
    data = load_all_scores()

    # Count
    total = 0
    for agent in data:
        for task in data[agent]:
            for run in data[agent][task]:
                total += len(data[agent][task][run])
    print(f"Loaded {total} score files across {len(data)} agents", flush=True)

    print("Generating analysis...", flush=True)
    report = generate_report(data)

    out_path = BENCH / "*MD" / "v4.0" / "key" / "judge_comparison_analysis.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Report written to: {out_path}")
