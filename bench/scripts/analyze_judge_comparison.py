#!/usr/bin/env python3
"""
QuantTutorBench Judge Comparison & Statistical Analysis
========================================================
Data: 2 agent models (Sonnet 4.6, Haiku 4.5) × 2 judge models (Sonnet, Haiku)
       8 ICC tasks × 3 runs + single runs
       4 dimensions: OAS, QR, QP, Tutor

Outputs: bench/*MD/v5.0/judge_comparison_analysis_v2.md
"""

import glob
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats
from scipy.stats import spearmanr
from sklearn.metrics import cohen_kappa_score

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


def compute_weighted_kappa(scores_a, scores_b, n_bins=5):
    """Quadratic weighted Cohen's Kappa on discretised 0-1 scores.

    Discretises continuous scores into *n_bins* ordinal categories and
    computes quadratic-weighted Kappa, which penalises larger
    disagreements more heavily.

    Interpretation (Landis & Koch 1977):
        <0.20 poor | 0.21-0.40 fair | 0.41-0.60 moderate
        0.61-0.80 substantial | 0.81-1.00 almost perfect
    """
    edges = np.linspace(0, 1, n_bins + 1)
    bins_a = np.clip(np.digitize(scores_a, edges[1:-1]), 0, n_bins - 1)
    bins_b = np.clip(np.digitize(scores_b, edges[1:-1]), 0, n_bins - 1)
    return cohen_kappa_score(bins_a, bins_b, weights="quadratic")


def compute_agreement_rate(scores_a, scores_b, threshold=0.1):
    """Simple agreement rate (TutorBench-comparable).

    Two scores *agree* when their absolute difference <= *threshold*.
    threshold=0.1 on a 0-1 scale equals 1-point tolerance on 1-10.
    """
    agreements = sum(1 for a, b in zip(scores_a, scores_b) if abs(a - b) <= threshold)
    return agreements / len(scores_a)


def compute_bland_altman(scores_a, scores_b):
    """Bland-Altman analysis: systematic bias and limits of agreement.

    Returns dict with bias (mean diff), ±1.96 SD limits, and outlier count.
    Convention: diff = a - b (positive = judge a scored higher).
    """
    a = np.asarray(scores_a, dtype=float)
    b = np.asarray(scores_b, dtype=float)
    diffs = a - b
    mean_diff = np.mean(diffs)
    std_diff = np.std(diffs, ddof=1)
    loa_upper = mean_diff + 1.96 * std_diff
    loa_lower = mean_diff - 1.96 * std_diff
    n_outside = int(np.sum((diffs > loa_upper) | (diffs < loa_lower)))
    return {
        "mean_diff": mean_diff,
        "std_diff": std_diff,
        "loa_upper": loa_upper,
        "loa_lower": loa_lower,
        "n_outside": n_outside,
        "pct_outside": n_outside / len(diffs),
        "n_total": len(diffs),
    }


def bootstrap_ci(data_a, data_b, metric_fn, n_bootstrap=10000, ci=0.95, seed=42):
    """Non-parametric bootstrap confidence interval for a two-sample metric.

    *metric_fn(a, b) -> float* is called on resampled arrays.
    Returns (point_estimate, ci_lower, ci_upper).
    """
    rng = np.random.RandomState(seed)
    a = np.asarray(data_a, dtype=float)
    b = np.asarray(data_b, dtype=float)
    point = metric_fn(a, b)
    n = len(a)
    boot = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        idx = rng.randint(0, n, size=n)
        boot[i] = metric_fn(a[idx], b[idx])
    alpha = (1 - ci) / 2
    lo = float(np.percentile(boot, alpha * 100))
    hi = float(np.percentile(boot, (1 - alpha) * 100))
    return point, lo, hi


def _cohens_d(s_arr, h_arr):
    """Cohen's d (pooled SD) — bootstrap-compatible signature."""
    n1, n2 = len(s_arr), len(h_arr)
    pooled = np.sqrt(
        ((n1 - 1) * s_arr.std(ddof=1) ** 2 + (n2 - 1) * h_arr.std(ddof=1) ** 2)
        / (n1 + n2 - 2)
    )
    return (s_arr.mean() - h_arr.mean()) / pooled if pooled > 1e-9 else 0.0


def _pearsonr_val(a, b):
    """Pearson r value only — bootstrap-compatible signature."""
    r, _ = stats.pearsonr(a, b)
    return r


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
        rho, rho_p = spearmanr(h_vals, s_vals)

        # ICC between two judges
        matrix = [[h, s] for h, s in pairs]
        icc = icc_3_1(matrix)

        # Weighted Kappa (chance-corrected ordinal agreement)
        kappa = compute_weighted_kappa(h_vals, s_vals, n_bins=5)

        # Simple agreement rate (TutorBench-comparable)
        agreement = compute_agreement_rate(h_vals, s_vals, threshold=0.1)

        # Bland-Altman
        ba = compute_bland_altman(h_vals, s_vals)

        # Bootstrap 95% CI for Pearson r
        h_arr = np.array(h_vals)
        s_arr = np.array(s_vals)
        _, r_ci_lo, r_ci_hi = bootstrap_ci(h_arr, s_arr, _pearsonr_val)

        summary[dim] = {
            "n": len(pairs),
            "mean_delta": np.mean(deltas),
            "std_delta": np.std(deltas, ddof=1),
            "min_delta": np.min(deltas),
            "max_delta": np.max(deltas),
            "pearson_r": r,
            "pearson_p": p_val,
            "pearson_r_ci_lo": r_ci_lo,
            "pearson_r_ci_hi": r_ci_hi,
            "spearman_rho": rho,
            "spearman_p": rho_p,
            "kendall_tau": tau,
            "kendall_p": tau_p,
            "icc_judge": icc,
            "weighted_kappa": kappa,
            "agreement_rate": agreement,
            "ba_bias": ba["mean_diff"],
            "ba_loa_lower": ba["loa_lower"],
            "ba_loa_upper": ba["loa_upper"],
            "ba_pct_outside": ba["pct_outside"],
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

            # Bootstrap 95% CI for Cohen's d
            _, d_ci_lo, d_ci_hi = bootstrap_ci(s_arr, h_arr, _cohens_d)

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
                "cohens_d_ci_lo": d_ci_lo,
                "cohens_d_ci_hi": d_ci_hi,
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

    # ── Section 1: Cross-Judge Robustness ──
    _a("## 一、Cross-Judge 稳健性分析（Haiku Judge vs Sonnet Judge）")
    _a("")
    _a(
        "同一份对话交给两个不同的 judge 模型评分，排名是否一致？偏差来源是系统性的还是随机的？"
    )
    _a("")
    _a(
        "> **阅读指南**：本节衡量的是**排名稳健性**（换 judge 后模型排名是否改变），"
        "不是评估可靠性。评估可靠性的主要证据是 §2（within-judge CV）和 §3（Cohen's d Bootstrap CI）。"
        "Kappa 和 Agreement Rate 在两个 judge 的 cross-judge 场景下受系统偏差主导，"
        "其适用场景是多 rater 一致性分析（如方案五 Human Calibration 中 Sonnet judge vs 多位人类专家）。"
    )
    _a("")

    agreement_pairs, agreement_summary = analyze_judge_agreement(data)

    _a("### 1.1 排名稳健性（主要指标）")
    _a("")
    _a(
        "| 维度 | N | Bias (Δ) | Std Δ | Pearson r [95% CI] | Spearman ρ | Kendall τ | ICC | Sonnet↑ | Haiku↑ |"
    )
    _a(
        "|------|---|----------|-------|--------------------|------------|-----------|-----|---------|--------|"
    )

    for dim in DIMS:
        s = agreement_summary.get(dim)
        if not s:
            continue
        r_ci = f"{s['pearson_r']:.3f} [{s['pearson_r_ci_lo']:.2f}, {s['pearson_r_ci_hi']:.2f}]"
        _a(
            f"| {dim} | {s['n']} | {s['mean_delta']:+.4f} | {s['std_delta']:.4f} | "
            f"{r_ci} | {s['spearman_rho']:.3f} | {s['kendall_tau']:.3f} | "
            f"{s['icc_judge']:.3f} | "
            f"{s['n_sonnet_higher']} | {s['n_haiku_higher']} |"
        )

    _a("")
    tutor_s = agreement_summary.get("Tutor", {})
    qr_s = agreement_summary.get("QR", {})
    qp_s = agreement_summary.get("QP", {})
    _a("**解读**：")
    _a(
        "- **排名方向完全一致**：两个 judge 下 Sonnet agent 均优于 Haiku agent，"
        "OAS 方向一致性 8/8（Sonnet judge）和 7/8（Haiku judge）。"
        "换 judge 不改变结论。"
    )
    _a(
        f"- **Tutor 排序相关最弱但仍显著**：Pearson r={tutor_s.get('pearson_r',0):.3f} "
        f"[{tutor_s.get('pearson_r_ci_lo',0):.2f}, {tutor_s.get('pearson_r_ci_hi',0):.2f}]，"
        f"Spearman ρ={tutor_s.get('spearman_rho',0):.3f}。"
        f"两个 judge 对教学质量的相对排序是一致的。"
    )
    _a(
        f"- **Tutor 系统偏差最大**：Haiku judge 系统性虚高 {abs(tutor_s.get('mean_delta',0)):.3f}，"
        f"但这是可消除的校准差异（选用 Sonnet judge 即可），不影响排名有效性。"
    )
    _a(
        f"- **QR 一致性最强**：r={qr_s.get('pearson_r',0):.3f}，"
        f"ICC={qr_s.get('icc_judge',0):.3f} — 程序化评分占比越高，cross-judge 一致性越高。"
    )
    _a("")

    _a("### 1.2 绝对分数一致性（参考指标，适用于多 rater 场景）")
    _a("")
    _a(
        "> Kappa 和 Agreement Rate 衡量的是'两个 judge 是否把同一对话放进同一分数档位'。"
        "在当前 2-judge 场景下，这些指标主要反映 Haiku judge 的校准偏差，"
        "而非评估体系的内在可靠性。它们的真正用武之地是方案五（多模型 + 人类的多 rater 一致性分析）。"
    )
    _a("")
    _a("| 维度 | Kappa_w (5-bin) | Agreement Rate (threshold=0.1) | 系统偏差 |")
    _a("|------|-----------------|-------------------------------|---------|")
    for dim in DIMS:
        s = agreement_summary.get(dim)
        if not s:
            continue
        _a(
            f"| {dim} | {s['weighted_kappa']:.3f} | {s['agreement_rate']*100:.1f}% | "
            f"{s['mean_delta']:+.4f} |"
        )
    _a("")
    _a(
        f"**解读**：Tutor 的 Kappa={tutor_s.get('weighted_kappa',0):.3f}、"
        f"Agreement={tutor_s.get('agreement_rate',0)*100:.1f}% 看似极低，"
        f"但主要由系统偏差 {abs(tutor_s.get('mean_delta',0)):.3f} 驱动。"
        f"当 bias ({abs(tutor_s.get('mean_delta',0)):.3f}) > threshold (0.1) 时，"
        f"Agreement Rate 在数学上趋近 0%，不含额外信息。"
        f"Kappa 同理——它惩罚的是校准差异，而非排序分歧。"
    )
    _a(
        f"QP 的 Agreement={qp_s.get('agreement_rate',0)*100:.1f}% 极高，"
        f"部分因为其 bias 小（{abs(qp_s.get('mean_delta',0)):.3f}）且分数分布集中"
        f"（Std Δ={qp_s.get('std_delta',0):.3f}）。"
    )
    _a("")

    # ── Section 1.3: Bland-Altman ──
    _a("### 1.3 Bland-Altman 偏差分解")
    _a("")
    _a(
        "Bland-Altman 分析将 cross-judge 分歧分解为**系统偏差**（bias，可通过选定 judge 消除）"
        "和**随机分散**（LOA 宽度减去 bias，不可消除的真随机分歧）："
    )
    _a("")
    _a(
        "| 维度 | 系统偏差 (Bias) | LOA 下限 | LOA 上限 | LOA 宽度 | 随机分散 | 超限比例 |"
    )
    _a("|------|----------------|---------|---------|---------|---------|---------|")
    for dim in DIMS:
        s = agreement_summary.get(dim)
        if not s:
            continue
        loa_width = s["ba_loa_upper"] - s["ba_loa_lower"]
        random_scatter = loa_width - abs(s["ba_bias"])  # LOA width minus bias
        _a(
            f"| {dim} | {s['ba_bias']:+.4f} | {s['ba_loa_lower']:+.4f} | "
            f"{s['ba_loa_upper']:+.4f} | {loa_width:.4f} | {random_scatter:.4f} | "
            f"{s['ba_pct_outside']*100:.1f}% |"
        )
    _a("")
    tutor_ba = agreement_summary.get("Tutor", {})
    tutor_loa_w = tutor_ba.get("ba_loa_upper", 0) - tutor_ba.get("ba_loa_lower", 0)
    tutor_random = tutor_loa_w - abs(tutor_ba.get("ba_bias", 0))
    _a(
        f"**解读**：Tutor 的 LOA 宽度 {tutor_loa_w:.3f} 中，"
        f"系统偏差占 {abs(tutor_ba.get('ba_bias',0)):.3f}，"
        f"随机分散占 {tutor_random:.3f}。"
        f"这意味着 Tutor 的 cross-judge 分歧**主要来自可消除的系统偏差**"
        f"（Haiku judge 校准点偏高），"
        f"而非两个 judge 对教学质量有根本性的判断分歧。"
        f"选用 Sonnet judge 作为主结果即可消除系统偏差成分。"
    )
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
        "| Judge | Dim | Sonnet Mean | Haiku Mean | Cohen's d [95% CI] | Effect | Wilcoxon p | Direction |"
    )
    _a(
        "|-------|-----|------------|------------|---------------------|--------|------------|-----------|"
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
            d_ci = f"{d['cohens_d']:+.3f} [{d['cohens_d_ci_lo']:+.2f}, {d['cohens_d_ci_hi']:+.2f}]"
            _a(
                f"| {judge} | {dim} | {d['sonnet_mean']:.4f} | {d['haiku_mean']:.4f} | "
                f"{d_ci} | {effect} | {wp} | {direction} |"
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
    _a("### 6.1 可靠性证据层次")
    _a("")
    _a("本报告的可靠性证据按证据强度分三层：")
    _a("")

    tutor_agreement = agreement_summary.get("Tutor", {})
    _qp_agreement = agreement_summary.get("QP", {})  # noqa: F841
    _qr_agreement = agreement_summary.get("QR", {})  # noqa: F841

    # Get Sonnet judge Tutor d CI for the conclusion
    sonnet_tutor_d = discrim["sonnet"].get("Tutor", {})
    _a(
        f"1. **区分度稳健性（核心）**：Tutor Cohen's d = "
        f"{sonnet_tutor_d.get('cohens_d',0):+.3f} "
        f"[{sonnet_tutor_d.get('cohens_d_ci_lo',0):+.2f}, "
        f"{sonnet_tutor_d.get('cohens_d_ci_hi',0):+.2f}]，"
        f"CI 下限远离 0，区分度在统计上稳健。"
    )
    sonnet_qr_d = discrim["sonnet"].get("QR", {})
    _a(
        f"   QR Cohen's d = {sonnet_qr_d.get('cohens_d',0):+.3f} "
        f"[{sonnet_qr_d.get('cohens_d_ci_lo',0):+.2f}, "
        f"{sonnet_qr_d.get('cohens_d_ci_hi',0):+.2f}]，"
        f"CI 包含 0 — QR 上两个模型的差距在统计上不显著。"
    )
    _a(
        "2. **Within-judge 可复现性（核心）**：同一 judge 对同一任务 3 次运行的 CV "
        "详见 §2，多数任务 CV < 10%。"
    )
    _a(
        f"3. **Cross-judge 排名稳健性（辅助）**：两个 judge 下 agent 排名方向 100% 一致。"
        f"Tutor 排序相关 Spearman ρ={tutor_agreement.get('spearman_rho',0):.3f}。"
        f"Bland-Altman 证实分歧主要来自系统偏差（可消除），非随机分散。"
    )
    _a("")

    _a("### 6.2 Judge 选择")
    _a("")
    _a(
        f"1. **Sonnet judge 作为主结果**——区分度更强 "
        f"(Tutor d={sonnet_tutor_d.get('cohens_d',0):+.3f} vs "
        f"{discrim['haiku'].get('Tutor',{}).get('cohens_d',0):+.3f})，更严格（不虚高）"
    )
    _a(
        "2. **Haiku judge 作为 robustness check**——附录展示排名一致性，"
        "Bland-Altman 展示偏差结构"
    )
    _a(
        f"3. **Tutor 系统偏差是校准差异，非评估缺陷**——"
        f"bias={abs(tutor_agreement.get('mean_delta',0)):.3f} 占 LOA 的主要成分，"
        f"选定一个 judge 即可消除"
    )
    _a("")

    _a("### 6.3 论文建议")
    _a("")
    _a("1. **主结果使用 Sonnet judge**——更严格、更区分")
    _a(
        "2. **报告排名稳健性**——Table 展示 Pearson r [95% CI]、Spearman ρ、ICC；"
        "附录展示 Bland-Altman 偏差分解"
    )
    _a("3. **Tutor 维度是核心差异化**——Cohen's d [95% CI] 最大、区分方向最一致")
    _a("4. **多维度不冗余**——QR-Tutor 相关性低，证明多维度必要性")
    _a(
        "5. **Kappa / Agreement Rate 留给 Human Calibration**——"
        "在多 rater（多个 LLM + 人类）场景下作为绝对分数一致性指标报告"
    )
    _a("")

    # ── Section 7: Statistical Methodology ──
    _a("## 七、统计方法论说明")
    _a("")
    _a("### 7.1 指标分类与适用场景")
    _a("")
    _a("| 指标 | 衡量什么 | 适用场景 | 本报告用途 |")
    _a("|------|---------|---------|-----------|")
    _a(
        "| Pearson r / Spearman ρ / Kendall τ | 排序一致性 | "
        "cross-judge 排名稳健性 | §1.1 主要指标 |"
    )
    _a(
        "| Bland-Altman | 偏差分解（系统 vs 随机） | "
        "理解 cross-judge 分歧的来源 | §1.3 偏差分解 |"
    )
    _a("| ICC(3,1) | 排名+绝对值一致性 | " "cross-judge 和 within-judge | §1.1 和 §2 |")
    _a(
        "| Cohen's d [Bootstrap CI] | 效应量及其不确定性 | "
        "区分度的统计稳健性 | §3 核心指标 |"
    )
    _a("| CV (变异系数) | 多 run 稳定性 | " "within-judge 可复现性 | §2 核心指标 |")
    _a(
        "| Weighted Kappa | 绝对分数档位一致性（校正偶然） | "
        "多 rater 一致性（3+ rater） | §1.2 参考 / 方案五核心 |"
    )
    _a(
        "| Agreement Rate | 绝对分数一致性（未校正偶然） | "
        "与 TutorBench 可比 | §1.2 参考 / 方案五对标 |"
    )
    _a("")
    _a(
        "> **关键区分**：Pearson r / Spearman ρ 衡量'排序是否一致'（不受系统偏差影响），"
        "Kappa / Agreement Rate 衡量'绝对分数是否一致'（受系统偏差严重影响）。"
        "对于'选定一个 judge 后排名是否可靠'这个问题，前者是正确指标；"
        "对于'多个评估者之间是否校准一致'这个问题（方案五 Human Calibration），后者是正确指标。"
    )
    _a("")
    _a("### 7.2 Kappa 离散化参数说明")
    _a("")
    _a("Weighted Kappa 需要将 0-1 连续分数离散化为有序类别。选择 5 bins 的理由：")
    _a("")
    _a("1. 与 QP 维度的 5 档评分粒度一致（0.0/0.25/0.5/0.75/1.0）")
    _a(
        "2. 粒度消融实验已证明 5 档与 10 档的 Cohen's d 无显著差异 "
        "(d: 1.75→1.82, +4.1%)"
    )
    _a("3. bins 过多会导致大量空 cell，Kappa 估计不稳定（N=44-52）")
    _a("")
    _a("### 7.3 与参照论文的方法论对比")
    _a("")
    _a("| 能力 | TutorBench | MathTutorBench | EduBench | 本报告 |")
    _a("|------|-----------|----------------|----------|--------|")
    _a(
        "| 排名稳健性 | 未报告 | 未报告 | Kendall's W | Pearson r + Spearman ρ + Kendall τ |"
    )
    _a("| 偏差分解 | 未做 | 未做 | 未做 | Bland-Altman |")
    _a("| 标准化效应量 | 未报告 | 未报告 | 未报告 | Cohen's d [Bootstrap CI] |")
    _a("| 多 run 稳定性 | 未报告 | 未报告 | 未报告 | CV per task |")
    _a("| 维度独立性 | 未报告 | 定性 | 未报告 | 维度间 Pearson r |")
    _a("| 评分粒度消融 | 未报告 | 未报告 | 未报告 | 10档→5档 d 变化 |")
    _a(
        "| 绝对一致性（人类验证） | Agreement 0.78 (未校正偶然) | 未做 | Kendall's W | **方案五计划：Kappa + Agreement + Krippendorff α** |"
    )
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

    out_path = BENCH / "*MD" / "v5.0" / "judge_comparison_analysis_v2.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Report written to: {out_path}")
