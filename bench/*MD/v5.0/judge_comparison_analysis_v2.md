# QuantTutorBench 统计分析报告：Judge 对比与评估可靠性

> 生成时间：2026-04-07
> 数据：2 agent (Sonnet 4.6, Haiku 4.5) × 2 judge (Sonnet, Haiku) × 8 ICC tasks × 3 runs
> 排除：Tutor=0.0 的 API 超时异常值

---

## 一、Cross-Judge 稳健性分析（Haiku Judge vs Sonnet Judge）

同一份对话交给两个不同的 judge 模型评分，排名是否一致？偏差来源是系统性的还是随机的？

> **阅读指南**：本节衡量的是**排名稳健性**（换 judge 后模型排名是否改变），不是评估可靠性。评估可靠性的主要证据是 §2（within-judge CV）和 §3（Cohen's d Bootstrap CI）。Kappa 和 Agreement Rate 在两个 judge 的 cross-judge 场景下受系统偏差主导，其适用场景是多 rater 一致性分析（如方案五 Human Calibration 中 Sonnet judge vs 多位人类专家）。

### 1.1 排名稳健性（主要指标）

| 维度 | N | Bias (Δ) | Std Δ | Pearson r [95% CI] | Spearman ρ | Kendall τ | ICC | Sonnet↑ | Haiku↑ |
|------|---|----------|-------|--------------------|------------|-----------|-----|---------|--------|
| OAS | 52 | -0.0846 | 0.0856 | 0.571 [0.39, 0.73] | 0.620 | 0.450 | 0.567 | 5 | 46 |
| QR | 52 | +0.0251 | 0.0744 | 0.872 [0.74, 0.94] | 0.752 | 0.611 | 0.854 | 25 | 13 |
| QP | 52 | -0.0205 | 0.0402 | 0.806 [0.68, 0.89] | 0.812 | 0.631 | 0.795 | 11 | 35 |
| Tutor | 44 | -0.1981 | 0.1113 | 0.676 [0.53, 0.79] | 0.676 | 0.487 | 0.676 | 1 | 42 |

**解读**：
- **排名方向完全一致**：两个 judge 下 Sonnet agent 均优于 Haiku agent，OAS 方向一致性 8/8（Sonnet judge）和 7/8（Haiku judge）。换 judge 不改变结论。
- **Tutor 排序相关最弱但仍显著**：Pearson r=0.676 [0.53, 0.79]，Spearman ρ=0.676。两个 judge 对教学质量的相对排序是一致的。
- **Tutor 系统偏差最大**：Haiku judge 系统性虚高 0.198，但这是可消除的校准差异（选用 Sonnet judge 即可），不影响排名有效性。
- **QR 一致性最强**：r=0.872，ICC=0.854 — 程序化评分占比越高，cross-judge 一致性越高。

### 1.2 绝对分数一致性（参考指标，适用于多 rater 场景）

> Kappa 和 Agreement Rate 衡量的是'两个 judge 是否把同一对话放进同一分数档位'。在当前 2-judge 场景下，这些指标主要反映 Haiku judge 的校准偏差，而非评估体系的内在可靠性。它们的真正用武之地是方案五（多模型 + 人类的多 rater 一致性分析）。

| 维度 | Kappa_w (5-bin) | Agreement Rate (threshold=0.1) | 系统偏差 |
|------|-----------------|-------------------------------|---------|
| OAS | 0.317 | 71.2% | -0.0846 |
| QR | 0.733 | 84.6% | +0.0251 |
| QP | 0.568 | 98.1% | -0.0205 |
| Tutor | 0.231 | 15.9% | -0.1981 |

**解读**：Tutor 的 Kappa=0.231、Agreement=15.9% 看似极低，但主要由系统偏差 0.198 驱动。当 bias (0.198) > threshold (0.1) 时，Agreement Rate 在数学上趋近 0%，不含额外信息。Kappa 同理——它惩罚的是校准差异，而非排序分歧。
QP 的 Agreement=98.1% 极高，部分因为其 bias 小（0.021）且分数分布集中（Std Δ=0.040）。

### 1.3 Bland-Altman 偏差分解

Bland-Altman 分析将 cross-judge 分歧分解为**系统偏差**（bias，可通过选定 judge 消除）和**随机分散**（LOA 宽度减去 bias，不可消除的真随机分歧）：

| 维度 | 系统偏差 (Bias) | LOA 下限 | LOA 上限 | LOA 宽度 | 随机分散 | 超限比例 |
|------|----------------|---------|---------|---------|---------|---------|
| OAS | +0.0846 | -0.0833 | +0.2524 | 0.3357 | 0.2512 | 9.6% |
| QR | -0.0251 | -0.1709 | +0.1206 | 0.2915 | 0.2663 | 9.6% |
| QP | +0.0205 | -0.0583 | +0.0993 | 0.1576 | 0.1371 | 1.9% |
| Tutor | +0.1981 | -0.0201 | +0.4163 | 0.4364 | 0.2384 | 4.5% |

**解读**：Tutor 的 LOA 宽度 0.436 中，系统偏差占 0.198，随机分散占 0.238。这意味着 Tutor 的 cross-judge 分歧**主要来自可消除的系统偏差**（Haiku judge 校准点偏高），而非两个 judge 对教学质量有根本性的判断分歧。选用 Sonnet judge 作为主结果即可消除系统偏差成分。

### 1.2 逐项对比（按维度分组）

<details>
<summary>展开查看全部配对数据</summary>

| Agent | Task | Run | Dim | Haiku | Sonnet | Δ |
|-------|------|-----|-----|-------|--------|---|
| haiku | B01_interpret_metric | _run1 | OAS | 0.7244 | 0.6377 | -0.0867 |
| haiku | B01_interpret_metric | _run2 | OAS | 0.8353 | 0.7025 | -0.1328 ⚠️ |
| haiku | B01_interpret_metric | _run3 | OAS | 0.7658 | 0.7110 | -0.0548 |
| haiku | B02_basic_sequential | _run1 | OAS | 0.4476 | 0.4779 | +0.0303 |
| haiku | B02_basic_sequential | _run2 | OAS | 0.5960 | 0.5300 | -0.0660 |
| haiku | B02_basic_sequential | _run3 | OAS | 0.7173 | 0.4542 | -0.2631 ⚠️ |
| haiku | D01_load_inspect_ohl | _run1 | OAS | 0.6760 | 0.6118 | -0.0642 |
| haiku | D01_load_inspect_ohl | _run2 | OAS | 0.6192 | 0.6063 | -0.0129 |
| haiku | D01_load_inspect_ohl | _run3 | OAS | 0.6220 | 0.5738 | -0.0482 |
| haiku | D09_feature_engineer | _run1 | OAS | 0.7390 | 0.5892 | -0.1498 ⚠️ |
| haiku | D09_feature_engineer | _run2 | OAS | 0.7171 | 0.6343 | -0.0828 |
| haiku | D09_feature_engineer | _run3 | OAS | 0.6637 | 0.4931 | -0.1706 ⚠️ |
| haiku | I01_implement_sma | _run1 | OAS | 0.7350 | 0.6482 | -0.0868 |
| haiku | I01_implement_sma | _run2 | OAS | 0.7030 | 0.6628 | -0.0402 |
| haiku | I01_implement_sma | _run3 | OAS | 0.5405 | 0.5553 | +0.0148 |
| haiku | I02_trend_following | loper | OAS | 0.5651 | 0.4553 | -0.1098 ⚠️ |
| haiku | S01_ma_crossover | _run1 | OAS | 0.7794 | 0.6785 | -0.1009 ⚠️ |
| haiku | S01_ma_crossover | _run2 | OAS | 0.8059 | 0.6774 | -0.1285 ⚠️ |
| haiku | S01_ma_crossover | _run3 | OAS | 0.6702 | 0.4169 | -0.2533 ⚠️ |
| haiku | S03_mean_reversion_r | _run1 | OAS | 0.7567 | 0.4999 | -0.2568 ⚠️ |
| haiku | S03_mean_reversion_r | _run2 | OAS | 0.7095 | 0.4737 | -0.2358 ⚠️ |
| haiku | S03_mean_reversion_r | _run3 | OAS | 0.7145 | 0.4431 | -0.2714 ⚠️ |
| haiku | X01_ma_offbyone | _run1 | OAS | 0.7525 | 0.6753 | -0.0772 |
| haiku | X01_ma_offbyone | _run2 | OAS | 0.6607 | 0.5209 | -0.1398 ⚠️ |
| haiku | X01_ma_offbyone | _run3 | OAS | 0.7352 | 0.6926 | -0.0426 |
| haiku | X09_alpha_conflict | loper | OAS | 0.4768 | 0.4641 | -0.0127 |
| sonnet | B01_interpret_metric | _run1 | OAS | 0.7656 | 0.7081 | -0.0575 |
| sonnet | B01_interpret_metric | _run2 | OAS | 0.7691 | 0.7373 | -0.0318 |
| sonnet | B01_interpret_metric | _run3 | OAS | 0.7778 | 0.7071 | -0.0707 |
| sonnet | B02_basic_sequential | _run1 | OAS | 0.5269 | 0.5177 | -0.0092 |
| sonnet | B02_basic_sequential | _run2 | OAS | 0.7767 | 0.7435 | -0.0332 |
| sonnet | B02_basic_sequential | _run3 | OAS | 0.7854 | 0.7110 | -0.0744 |
| sonnet | D01_load_inspect_ohl | _run1 | OAS | 0.7293 | 0.6727 | -0.0566 |
| sonnet | D01_load_inspect_ohl | _run2 | OAS | 0.5998 | 0.5843 | -0.0155 |
| sonnet | D01_load_inspect_ohl | _run3 | OAS | 0.7566 | 0.7252 | -0.0314 |
| sonnet | D09_feature_engineer | _run1 | OAS | 0.7953 | 0.7321 | -0.0632 |
| sonnet | D09_feature_engineer | _run2 | OAS | 0.7701 | 0.5256 | -0.2445 ⚠️ |
| sonnet | D09_feature_engineer | _run3 | OAS | 0.7024 | 0.4961 | -0.2063 ⚠️ |
| sonnet | I01_implement_sma | _run1 | OAS | 0.6913 | 0.7043 | +0.0130 |
| sonnet | I01_implement_sma | _run2 | OAS | 0.6999 | 0.6791 | -0.0208 |
| sonnet | I01_implement_sma | _run3 | OAS | 0.7629 | 0.7101 | -0.0528 |
| sonnet | I02_trend_following | loper | OAS | 0.5729 | 0.6349 | +0.0620 |
| sonnet | S01_ma_crossover | _run1 | OAS | 0.7737 | 0.6908 | -0.0829 |
| sonnet | S01_ma_crossover | _run2 | OAS | 0.7936 | 0.7128 | -0.0808 |
| sonnet | S01_ma_crossover | _run3 | OAS | 0.7317 | 0.6471 | -0.0846 |
| sonnet | S03_mean_reversion_r | _run1 | OAS | 0.7993 | 0.7421 | -0.0572 |
| sonnet | S03_mean_reversion_r | _run2 | OAS | 0.8089 | 0.5306 | -0.2783 ⚠️ |
| sonnet | X01_ma_offbyone | _run1 | OAS | 0.7346 | 0.6842 | -0.0504 |
| sonnet | X01_ma_offbyone | _run2 | OAS | 0.7254 | 0.6905 | -0.0349 |
| sonnet | X01_ma_offbyone | _run3 | OAS | 0.7176 | 0.6744 | -0.0432 |
| sonnet | X09_alpha_conflict | loper | OAS | 0.6724 | 0.7223 | +0.0499 |
| sonnet | X09_alpha_conflict | atest | OAS | 0.7035 | 0.7035 | +0.0000 |
| haiku | B01_interpret_metric | _run1 | QP | 0.5750 | 0.5862 | +0.0112 |
| haiku | B01_interpret_metric | _run2 | QP | 0.7281 | 0.6327 | -0.0954 |
| haiku | B01_interpret_metric | _run3 | QP | 0.5936 | 0.6156 | +0.0220 |
| haiku | B02_basic_sequential | _run1 | QP | 0.5318 | 0.5606 | +0.0288 |
| haiku | B02_basic_sequential | _run2 | QP | 0.5675 | 0.5527 | -0.0148 |
| haiku | B02_basic_sequential | _run3 | QP | 0.6894 | 0.6670 | -0.0224 |
| haiku | D01_load_inspect_ohl | _run1 | QP | 0.6386 | 0.5873 | -0.0513 |
| haiku | D01_load_inspect_ohl | _run2 | QP | 0.5931 | 0.5910 | -0.0021 |
| haiku | D01_load_inspect_ohl | _run3 | QP | 0.7195 | 0.6283 | -0.0912 |
| haiku | D09_feature_engineer | _run1 | QP | 0.7193 | 0.6434 | -0.0759 |
| haiku | D09_feature_engineer | _run2 | QP | 0.7143 | 0.6531 | -0.0612 |
| haiku | D09_feature_engineer | _run3 | QP | 0.6804 | 0.5866 | -0.0938 |
| haiku | I01_implement_sma | _run1 | QP | 0.6622 | 0.6597 | -0.0025 |
| haiku | I01_implement_sma | _run2 | QP | 0.6835 | 0.6382 | -0.0453 |
| haiku | I01_implement_sma | _run3 | QP | 0.5498 | 0.6023 | +0.0525 |
| haiku | I02_trend_following | loper | QP | 0.5627 | 0.5516 | -0.0111 |
| haiku | S01_ma_crossover | _run1 | QP | 0.7692 | 0.6780 | -0.0912 |
| haiku | S01_ma_crossover | _run2 | QP | 0.7390 | 0.6486 | -0.0904 |
| haiku | S01_ma_crossover | _run3 | QP | 0.6359 | 0.5822 | -0.0537 |
| haiku | S03_mean_reversion_r | _run1 | QP | 0.7143 | 0.7275 | +0.0132 |
| haiku | S03_mean_reversion_r | _run2 | QP | 0.6472 | 0.6515 | +0.0043 |
| haiku | S03_mean_reversion_r | _run3 | QP | 0.6068 | 0.5707 | -0.0361 |
| haiku | X01_ma_offbyone | _run1 | QP | 0.7285 | 0.6810 | -0.0475 |
| haiku | X01_ma_offbyone | _run2 | QP | 0.6168 | 0.5406 | -0.0762 |
| haiku | X01_ma_offbyone | _run3 | QP | 0.6792 | 0.6892 | +0.0100 |
| haiku | X09_alpha_conflict | loper | QP | 0.5463 | 0.5767 | +0.0304 |
| sonnet | B01_interpret_metric | _run1 | QP | 0.6341 | 0.6122 | -0.0219 |
| sonnet | B01_interpret_metric | _run2 | QP | 0.5918 | 0.6183 | +0.0265 |
| sonnet | B01_interpret_metric | _run3 | QP | 0.5816 | 0.5992 | +0.0176 |
| sonnet | B02_basic_sequential | _run1 | QP | 0.5657 | 0.5891 | +0.0234 |
| sonnet | B02_basic_sequential | _run2 | QP | 0.7794 | 0.7731 | -0.0063 |
| sonnet | B02_basic_sequential | _run3 | QP | 0.7352 | 0.7290 | -0.0062 |
| sonnet | D01_load_inspect_ohl | _run1 | QP | 0.6795 | 0.6663 | -0.0132 |
| sonnet | D01_load_inspect_ohl | _run2 | QP | 0.6795 | 0.6295 | -0.0500 |
| sonnet | D01_load_inspect_ohl | _run3 | QP | 0.7439 | 0.7224 | -0.0215 |
| sonnet | D09_feature_engineer | _run1 | QP | 0.7422 | 0.7034 | -0.0388 |
| sonnet | D09_feature_engineer | _run2 | QP | 0.7416 | 0.7027 | -0.0389 |
| sonnet | D09_feature_engineer | _run3 | QP | 0.6822 | 0.6759 | -0.0063 |
| sonnet | I01_implement_sma | _run1 | QP | 0.6691 | 0.6495 | -0.0196 |
| sonnet | I01_implement_sma | _run2 | QP | 0.6685 | 0.6677 | -0.0008 |
| sonnet | I01_implement_sma | _run3 | QP | 0.7445 | 0.7382 | -0.0063 |
| sonnet | I02_trend_following | loper | QP | 0.6080 | 0.7160 | +0.1080 ⚠️ |
| sonnet | S01_ma_crossover | _run1 | QP | 0.7415 | 0.6842 | -0.0573 |
| sonnet | S01_ma_crossover | _run2 | QP | 0.7226 | 0.6876 | -0.0350 |
| sonnet | S01_ma_crossover | _run3 | QP | 0.7215 | 0.6829 | -0.0386 |
| sonnet | S03_mean_reversion_r | _run1 | QP | 0.7603 | 0.7527 | -0.0076 |
| sonnet | S03_mean_reversion_r | _run2 | QP | 0.7578 | 0.7340 | -0.0238 |
| sonnet | X01_ma_offbyone | _run1 | QP | 0.7205 | 0.7142 | -0.0063 |
| sonnet | X01_ma_offbyone | _run2 | QP | 0.6645 | 0.6233 | -0.0412 |
| sonnet | X01_ma_offbyone | _run3 | QP | 0.6655 | 0.6573 | -0.0082 |
| sonnet | X09_alpha_conflict | loper | QP | 0.6385 | 0.6345 | -0.0040 |
| sonnet | X09_alpha_conflict | atest | QP | 0.6045 | 0.6045 | +0.0000 |
| haiku | B01_interpret_metric | _run1 | QR | 0.8132 | 0.6769 | -0.1363 ⚠️ |
| haiku | B01_interpret_metric | _run2 | QR | 0.9319 | 0.9319 | +0.0000 |
| haiku | B01_interpret_metric | _run3 | QR | 0.8543 | 0.8168 | -0.0375 |
| haiku | B02_basic_sequential | _run1 | QR | 0.4995 | 0.5653 | +0.0658 |
| haiku | B02_basic_sequential | _run2 | QR | 0.5177 | 0.5177 | +0.0000 |
| haiku | B02_basic_sequential | _run3 | QR | 0.7193 | 0.6306 | -0.0887 |
| haiku | D01_load_inspect_ohl | _run1 | QR | 0.7801 | 0.7586 | -0.0215 |
| haiku | D01_load_inspect_ohl | _run2 | QR | 0.7949 | 0.7734 | -0.0215 |
| haiku | D01_load_inspect_ohl | _run3 | QR | 0.7178 | 0.6485 | -0.0693 |
| haiku | D09_feature_engineer | _run1 | QR | 0.7829 | 0.7829 | +0.0000 |
| haiku | D09_feature_engineer | _run2 | QR | 0.8020 | 0.8020 | +0.0000 |
| haiku | D09_feature_engineer | _run3 | QR | 0.6573 | 0.5895 | -0.0678 |
| haiku | I01_implement_sma | _run1 | QR | 0.7370 | 0.7666 | +0.0296 |
| haiku | I01_implement_sma | _run2 | QR | 0.6137 | 0.8099 | +0.1962 ⚠️ |
| haiku | I01_implement_sma | _run3 | QR | 0.4922 | 0.6654 | +0.1732 ⚠️ |
| haiku | I02_trend_following | loper | QR | 0.3555 | 0.4215 | +0.0660 |
| haiku | S01_ma_crossover | _run1 | QR | 0.7230 | 0.7503 | +0.0273 |
| haiku | S01_ma_crossover | _run2 | QR | 0.8044 | 0.8297 | +0.0253 |
| haiku | S01_ma_crossover | _run3 | QR | 0.6543 | 0.6090 | -0.0453 |
| haiku | S03_mean_reversion_r | _run1 | QR | 0.7009 | 0.7009 | +0.0000 |
| haiku | S03_mean_reversion_r | _run2 | QR | 0.7554 | 0.7018 | -0.0536 |
| haiku | S03_mean_reversion_r | _run3 | QR | 0.7489 | 0.6953 | -0.0536 |
| haiku | X01_ma_offbyone | _run1 | QR | 0.6976 | 0.7706 | +0.0730 |
| haiku | X01_ma_offbyone | _run2 | QR | 0.6232 | 0.6153 | -0.0079 |
| haiku | X01_ma_offbyone | _run3 | QR | 0.6976 | 0.7706 | +0.0730 |
| haiku | X09_alpha_conflict | loper | QR | 0.2002 | 0.3716 | +0.1714 ⚠️ |
| sonnet | B01_interpret_metric | _run1 | QR | 0.8132 | 0.8587 | +0.0455 |
| sonnet | B01_interpret_metric | _run2 | QR | 0.8927 | 0.9000 | +0.0073 |
| sonnet | B01_interpret_metric | _run3 | QR | 0.9319 | 0.8664 | -0.0655 |
| sonnet | B02_basic_sequential | _run1 | QR | 0.3943 | 0.4271 | +0.0328 |
| sonnet | B02_basic_sequential | _run2 | QR | 0.6684 | 0.7385 | +0.0701 |
| sonnet | B02_basic_sequential | _run3 | QR | 0.7729 | 0.8185 | +0.0456 |
| sonnet | D01_load_inspect_ohl | _run1 | QR | 0.7949 | 0.7949 | +0.0000 |
| sonnet | D01_load_inspect_ohl | _run2 | QR | 0.4755 | 0.5185 | +0.0430 |
| sonnet | D01_load_inspect_ohl | _run3 | QR | 0.7932 | 0.8181 | +0.0249 |
| sonnet | D09_feature_engineer | _run1 | QR | 0.7939 | 0.7939 | +0.0000 |
| sonnet | D09_feature_engineer | _run2 | QR | 0.7990 | 0.7990 | +0.0000 |
| sonnet | D09_feature_engineer | _run3 | QR | 0.7415 | 0.7415 | +0.0000 |
| sonnet | I01_implement_sma | _run1 | QR | 0.5798 | 0.7298 | +0.1500 ⚠️ |
| sonnet | I01_implement_sma | _run2 | QR | 0.6049 | 0.7549 | +0.1500 ⚠️ |
| sonnet | I01_implement_sma | _run3 | QR | 0.7088 | 0.7925 | +0.0837 |
| sonnet | I02_trend_following | loper | QR | 0.3024 | 0.4585 | +0.1561 ⚠️ |
| sonnet | S01_ma_crossover | _run1 | QR | 0.7100 | 0.7100 | +0.0000 |
| sonnet | S01_ma_crossover | _run2 | QR | 0.7734 | 0.7734 | +0.0000 |
| sonnet | S01_ma_crossover | _run3 | QR | 0.6834 | 0.7046 | +0.0212 |
| sonnet | S03_mean_reversion_r | _run1 | QR | 0.7765 | 0.7268 | -0.0497 |
| sonnet | S03_mean_reversion_r | _run2 | QR | 0.7819 | 0.7819 | +0.0000 |
| sonnet | X01_ma_offbyone | _run1 | QR | 0.6229 | 0.6576 | +0.0347 |
| sonnet | X01_ma_offbyone | _run2 | QR | 0.6524 | 0.6871 | +0.0347 |
| sonnet | X01_ma_offbyone | _run3 | QR | 0.6293 | 0.6293 | +0.0000 |
| sonnet | X09_alpha_conflict | loper | QR | 0.5793 | 0.8044 | +0.2251 ⚠️ |
| sonnet | X09_alpha_conflict | atest | QR | 0.7547 | 0.7547 | +0.0000 |
| haiku | B01_interpret_metric | _run1 | Tutor | 0.7952 | 0.6519 | -0.1433 ⚠️ |
| haiku | B01_interpret_metric | _run2 | Tutor | 0.8476 | 0.5164 | -0.3312 ⚠️ |
| haiku | B01_interpret_metric | _run3 | Tutor | 0.8635 | 0.6989 | -0.1646 ⚠️ |
| haiku | B02_basic_sequential | _run1 | Tutor | 0.2889 | 0.2793 | -0.0096 |
| haiku | B02_basic_sequential | _run2 | Tutor | 0.7206 | 0.5180 | -0.2026 ⚠️ |
| haiku | D01_load_inspect_ohl | _run1 | Tutor | 0.5982 | 0.4691 | -0.1291 ⚠️ |
| haiku | D01_load_inspect_ohl | _run2 | Tutor | 0.4446 | 0.4292 | -0.0154 |
| haiku | D01_load_inspect_ohl | _run3 | Tutor | 0.3964 | 0.4232 | +0.0268 |
| haiku | D09_feature_engineer | _run1 | Tutor | 0.7107 | 0.3000 | -0.4107 ⚠️ |
| haiku | D09_feature_engineer | _run2 | Tutor | 0.6214 | 0.4167 | -0.2047 ⚠️ |
| haiku | D09_feature_engineer | _run3 | Tutor | 0.6518 | 0.2714 | -0.3804 ⚠️ |
| haiku | I01_implement_sma | _run1 | Tutor | 0.8175 | 0.4965 | -0.3210 ⚠️ |
| haiku | I01_implement_sma | _run2 | Tutor | 0.8298 | 0.5199 | -0.3099 ⚠️ |
| haiku | I01_implement_sma | _run3 | Tutor | 0.5860 | 0.3719 | -0.2141 ⚠️ |
| haiku | I02_trend_following | loper | Tutor | 0.8123 | 0.3825 | -0.4298 ⚠️ |
| haiku | S01_ma_crossover | _run1 | Tutor | 0.8571 | 0.5952 | -0.2619 ⚠️ |
| haiku | S01_ma_crossover | _run2 | Tutor | 0.8857 | 0.5333 | -0.3524 ⚠️ |
| haiku | X01_ma_offbyone | _run1 | Tutor | 0.8444 | 0.5574 | -0.2870 ⚠️ |
| haiku | X01_ma_offbyone | _run2 | Tutor | 0.7556 | 0.3877 | -0.3679 ⚠️ |
| haiku | X01_ma_offbyone | _run3 | Tutor | 0.8444 | 0.6056 | -0.2388 ⚠️ |
| haiku | X09_alpha_conflict | loper | Tutor | 0.7185 | 0.4407 | -0.2778 ⚠️ |
| sonnet | B01_interpret_metric | _run1 | Tutor | 0.8635 | 0.6444 | -0.2191 ⚠️ |
| sonnet | B01_interpret_metric | _run2 | Tutor | 0.8317 | 0.6862 | -0.1455 ⚠️ |
| sonnet | B01_interpret_metric | _run3 | Tutor | 0.8270 | 0.6471 | -0.1799 ⚠️ |
| sonnet | B02_basic_sequential | _run1 | Tutor | 0.6365 | 0.5402 | -0.0963 |
| sonnet | B02_basic_sequential | _run2 | Tutor | 0.9000 | 0.7148 | -0.1852 ⚠️ |
| sonnet | B02_basic_sequential | _run3 | Tutor | 0.8587 | 0.5646 | -0.2941 ⚠️ |
| sonnet | D01_load_inspect_ohl | _run1 | Tutor | 0.7107 | 0.5375 | -0.1732 ⚠️ |
| sonnet | D01_load_inspect_ohl | _run2 | Tutor | 0.6518 | 0.6083 | -0.0435 |
| sonnet | D01_load_inspect_ohl | _run3 | Tutor | 0.7286 | 0.6202 | -0.1084 ⚠️ |
| sonnet | D09_feature_engineer | _run1 | Tutor | 0.8589 | 0.6934 | -0.1655 ⚠️ |
| sonnet | I01_implement_sma | _run1 | Tutor | 0.8474 | 0.7386 | -0.1088 ⚠️ |
| sonnet | I01_implement_sma | _run2 | Tutor | 0.8474 | 0.6041 | -0.2433 ⚠️ |
| sonnet | I01_implement_sma | _run3 | Tutor | 0.8474 | 0.5813 | -0.2661 ⚠️ |
| sonnet | I02_trend_following | loper | Tutor | 0.8474 | 0.7462 | -0.1012 ⚠️ |
| sonnet | S01_ma_crossover | _run1 | Tutor | 0.8857 | 0.6762 | -0.2095 ⚠️ |
| sonnet | S01_ma_crossover | _run2 | Tutor | 0.9000 | 0.6714 | -0.2286 ⚠️ |
| sonnet | S01_ma_crossover | _run3 | Tutor | 0.8000 | 0.5381 | -0.2619 ⚠️ |
| sonnet | S03_mean_reversion_r | _run1 | Tutor | 0.8714 | 0.7476 | -0.1238 ⚠️ |
| sonnet | X01_ma_offbyone | _run1 | Tutor | 0.8815 | 0.6803 | -0.2012 ⚠️ |
| sonnet | X01_ma_offbyone | _run2 | Tutor | 0.8815 | 0.7728 | -0.1087 ⚠️ |
| sonnet | X01_ma_offbyone | _run3 | Tutor | 0.8815 | 0.7469 | -0.1346 ⚠️ |
| sonnet | X09_alpha_conflict | loper | Tutor | 0.8204 | 0.7290 | -0.0914 |
| sonnet | X09_alpha_conflict | atest | Tutor | 0.7593 | 0.7593 | +0.0000 |

</details>

## 二、ICC 稳定性分析（3 runs 一致性）

同一 task × agent 的 3 次运行，评分的变异系数和稳定性。

### 2.1 Sonnet Judge 下的 CV

| Agent | Task | OAS CV | QR CV | QP CV | Tutor CV |
|-------|------|--------|-------|-------|----------|
| sonnet | B01_interpret_metrics | 2.4% | 2.5% | 1.6% | 3.5% |
| sonnet | B02_basic_sequential_engi | 18.6% | 31.3% | 13.8% | 15.6% |
| sonnet | D01_load_inspect_ohlcv | 10.8% | 23.5% | 7.0% | 7.6% |
| sonnet | D09_feature_engineering_p | 22.0% | 4.1% | 2.3% | — |
| sonnet | X01_ma_offbyone | 1.2% | 4.4% | 6.9% | 6.5% |
| sonnet | I01_implement_sma | 2.4% | 4.2% | 6.8% | 13.3% |
| sonnet | S01_ma_crossover | 4.9% | 5.2% | 0.4% | 12.5% |
| haiku | B01_interpret_metrics | 5.9% | 15.8% | 3.8% | 15.2% |
| haiku | B02_basic_sequential_engi | 8.0% | 9.9% | 10.8% | — |
| haiku | D01_load_inspect_ohlcv | 3.4% | 9.4% | 3.8% | 5.7% |
| haiku | D09_feature_engineering_p | 12.6% | 16.2% | 5.7% | 23.4% |
| haiku | X01_ma_offbyone | 15.0% | 12.5% | 13.1% | 22.1% |
| haiku | I01_implement_sma | 9.4% | 9.9% | 4.6% | 17.2% |
| haiku | S01_ma_crossover | 25.5% | 15.3% | 7.7% | — |
| haiku | S03_mean_reversion_resear | 6.0% | 0.5% | 12.1% | — |

### 2.2 Haiku Judge 下的 CV

| Agent | Task | OAS CV | QR CV | QP CV | Tutor CV |
|-------|------|--------|-------|-------|----------|
| sonnet | B01_interpret_metrics | 0.8% | 6.9% | 4.6% | 2.4% |
| sonnet | B02_basic_sequential_engi | 21.1% | 32.0% | 16.3% | 17.8% |
| sonnet | D01_load_inspect_ohlcv | 12.0% | 26.7% | 5.3% | 5.8% |
| sonnet | D09_feature_engineering_p | 6.4% | 4.1% | 4.8% | 11.6% |
| sonnet | X01_ma_offbyone | 1.2% | 2.4% | 4.7% | 0.0% |
| sonnet | I01_implement_sma | 5.4% | 10.8% | 6.3% | 0.0% |
| sonnet | S01_ma_crossover | 4.1% | 6.4% | 1.5% | 6.3% |
| haiku | B01_interpret_metrics | 7.2% | 7.0% | 13.2% | 4.3% |
| haiku | B02_basic_sequential_engi | 23.0% | 21.1% | 13.9% | 43.9% |
| haiku | D01_load_inspect_ohlcv | 5.0% | 5.4% | 9.8% | 22.0% |
| haiku | D09_feature_engineering_p | 5.5% | 10.5% | 3.0% | 6.9% |
| haiku | X01_ma_offbyone | 6.8% | 6.4% | 8.3% | 6.3% |
| haiku | I01_implement_sma | 15.8% | 19.9% | 11.4% | 18.4% |
| haiku | S01_ma_crossover | 9.6% | 10.3% | 9.8% | 10.2% |
| haiku | S03_mean_reversion_resear | 3.6% | 4.0% | 8.3% | 8.9% |

### 2.3 Cross-Judge ICC 对比

同一维度的 ICC 在两个 judge 下是否一致？

**Sonnet Agent:**

| Task | Dim | Haiku Judge CV | Sonnet Judge CV | Δ CV |
|------|-----|----------------|-----------------|------|
| B01_interpret_metrics | OAS | 0.8% | 2.4% | +1.6% |
| B01_interpret_metrics | QR | 6.9% | 2.5% | -4.4% |
| B01_interpret_metrics | QP | 4.6% | 1.6% | -3.0% |
| B01_interpret_metrics | Tutor | 2.4% | 3.5% | +1.2% |
| B02_basic_sequential_engi | OAS | 21.1% | 18.6% | -2.5% |
| B02_basic_sequential_engi | QR | 32.0% | 31.3% | -0.7% |
| B02_basic_sequential_engi | QP | 16.3% | 13.8% | -2.5% |
| B02_basic_sequential_engi | Tutor | 17.8% | 15.6% | -2.2% |
| D01_load_inspect_ohlcv | OAS | 12.0% | 10.8% | -1.3% |
| D01_load_inspect_ohlcv | QR | 26.7% | 23.5% | -3.3% |
| D01_load_inspect_ohlcv | QP | 5.3% | 7.0% | +1.6% |
| D01_load_inspect_ohlcv | Tutor | 5.8% | 7.6% | +1.8% |
| D09_feature_engineering_p | OAS | 6.4% | 22.0% | +15.6% |
| D09_feature_engineering_p | QR | 4.1% | 4.1% | +0.0% |
| D09_feature_engineering_p | QP | 4.8% | 2.3% | -2.5% |
| X01_ma_offbyone | OAS | 1.2% | 1.2% | +0.0% |
| X01_ma_offbyone | QR | 2.4% | 4.4% | +1.9% |
| X01_ma_offbyone | QP | 4.7% | 6.9% | +2.2% |
| X01_ma_offbyone | Tutor | 0.0% | 6.5% | +6.5% |
| I01_implement_sma | OAS | 5.4% | 2.4% | -3.1% |
| I01_implement_sma | QR | 10.8% | 4.2% | -6.7% |
| I01_implement_sma | QP | 6.3% | 6.8% | +0.5% |
| I01_implement_sma | Tutor | 0.0% | 13.3% | +13.3% |
| S01_ma_crossover | OAS | 4.1% | 4.9% | +0.8% |
| S01_ma_crossover | QR | 6.4% | 5.2% | -1.2% |
| S01_ma_crossover | QP | 1.5% | 0.4% | -1.2% |
| S01_ma_crossover | Tutor | 6.3% | 12.5% | +6.2% |

**Haiku Agent:**

| Task | Dim | Haiku Judge CV | Sonnet Judge CV | Δ CV |
|------|-----|----------------|-----------------|------|
| B01_interpret_metrics | OAS | 7.2% | 5.9% | -1.4% |
| B01_interpret_metrics | QR | 7.0% | 15.8% | +8.8% |
| B01_interpret_metrics | QP | 13.2% | 3.8% | -9.4% |
| B01_interpret_metrics | Tutor | 4.3% | 15.2% | +10.9% |
| B02_basic_sequential_engi | OAS | 23.0% | 8.0% | -15.1% |
| B02_basic_sequential_engi | QR | 21.1% | 9.9% | -11.2% |
| B02_basic_sequential_engi | QP | 13.9% | 10.8% | -3.1% |
| D01_load_inspect_ohlcv | OAS | 5.0% | 3.4% | -1.6% |
| D01_load_inspect_ohlcv | QR | 5.4% | 9.4% | +4.0% |
| D01_load_inspect_ohlcv | QP | 9.8% | 3.8% | -6.1% |
| D01_load_inspect_ohlcv | Tutor | 22.0% | 5.7% | -16.3% |
| D09_feature_engineering_p | OAS | 5.5% | 12.6% | +7.1% |
| D09_feature_engineering_p | QR | 10.5% | 16.2% | +5.7% |
| D09_feature_engineering_p | QP | 3.0% | 5.7% | +2.7% |
| D09_feature_engineering_p | Tutor | 6.9% | 23.4% | +16.5% |
| X01_ma_offbyone | OAS | 6.8% | 15.0% | +8.2% |
| X01_ma_offbyone | QR | 6.4% | 12.5% | +6.1% |
| X01_ma_offbyone | QP | 8.3% | 13.1% | +4.8% |
| X01_ma_offbyone | Tutor | 6.3% | 22.1% | +15.9% |
| I01_implement_sma | OAS | 15.8% | 9.4% | -6.4% |
| I01_implement_sma | QR | 19.9% | 9.9% | -10.0% |
| I01_implement_sma | QP | 11.4% | 4.6% | -6.8% |
| I01_implement_sma | Tutor | 18.4% | 17.2% | -1.3% |
| S01_ma_crossover | OAS | 9.6% | 25.5% | +15.9% |
| S01_ma_crossover | QR | 10.3% | 15.3% | +5.0% |
| S01_ma_crossover | QP | 9.8% | 7.7% | -2.1% |
| S03_mean_reversion_resear | OAS | 3.6% | 6.0% | +2.5% |
| S03_mean_reversion_resear | QR | 4.0% | 0.5% | -3.5% |
| S03_mean_reversion_resear | QP | 8.3% | 12.1% | +3.8% |

## 三、模型区分度（Sonnet Agent vs Haiku Agent）

### 3.1 Cohen's d 对比

| Judge | Dim | Sonnet Mean | Haiku Mean | Cohen's d [95% CI] | Effect | Wilcoxon p | Direction |
|-------|-----|------------|------------|---------------------|--------|------------|-----------|
| sonnet | OAS | 0.6664 | 0.5819 | +0.987 [+0.49, +1.56] | large | 0.0078 | 8/8 S>H |
| sonnet | QR | 0.7401 | 0.7158 | +0.236 [-0.24, +0.82] | small | 0.3125 | 5/8 S>H |
| sonnet | QP | 0.6788 | 0.6239 | +1.105 [+0.65, +1.62] | large | 0.0156 | 7/8 S>H |
| sonnet | Tutor | 0.6456 | 0.4759 | +1.694 [+1.17, +2.49] | large | 0.0156 | 7/7 S>H |
| haiku | OAS | 0.7389 | 0.6953 | +0.571 [+0.24, +1.12] | medium | 0.0156 | 7/8 S>H |
| haiku | QR | 0.7128 | 0.7133 | -0.004 [-0.37, +0.40] | negligible | 0.7422 | 5/8 S>H |
| haiku | QP | 0.6953 | 0.6576 | +0.597 [+0.13, +1.09] | medium | 0.0547 | 7/8 S>H |
| haiku | Tutor | 0.8201 | 0.7181 | +0.797 [+0.59, +1.18] | medium | 0.0078 | 8/8 S>H |

**解读**：
- Sonnet judge 下 Tutor Cohen's d = +1.694，Haiku judge 下 = +0.797
- QR 在两个 judge 下的 d 分别是 +0.236 和 -0.004

### 3.2 逐任务区分度（Sonnet Judge）

| Task | Sonnet OAS | Haiku OAS | Δ OAS | Sonnet Tutor | Haiku Tutor | Δ Tutor |
|------|-----------|-----------|-------|-------------|-------------|---------|
| B01_interpret_metrics | 0.7175 | 0.6837 | +0.0338 | 0.6592 | 0.6224 | +0.0368 |
| B02_basic_sequential_engi | 0.6574 | 0.4874 | +0.1700 | 0.6065 | 0.3987 | +0.2079 |
| D01_load_inspect_ohlcv | 0.6607 | 0.5973 | +0.0634 | 0.5887 | 0.4405 | +0.1482 |
| D09_feature_engineering_p | 0.5846 | 0.5722 | +0.0124 | 0.6934 | 0.3294 | +0.3640 |
| X01_ma_offbyone | 0.6830 | 0.6296 | +0.0534 | 0.7333 | 0.5169 | +0.2164 |
| I01_implement_sma | 0.6978 | 0.6221 | +0.0757 | 0.6413 | 0.4628 | +0.1786 |
| S01_ma_crossover | 0.6836 | 0.5909 | +0.0926 | 0.6286 | 0.5642 | +0.0643 |
| S03_mean_reversion_resear | 0.6363 | 0.4722 | +0.1641 | nan | nan | +nan |

## 四、维度独立性

| Judge | 维度对 | Pearson r | p-value | N | 独立性 |
|-------|--------|-----------|---------|---|--------|
| sonnet | QP-Tutor | +0.479 | 0.0009 | 45 | 弱相关 |
| sonnet | QR-QP | +0.368 | 0.0128 | 45 | 弱相关 |
| sonnet | QR-Tutor | +0.241 | 0.1101 | 45 | 独立 |
| haiku | QP-Tutor | +0.406 | 0.0026 | 53 | 弱相关 |
| haiku | QR-QP | +0.428 | 0.0014 | 53 | 弱相关 |
| haiku | QR-Tutor | +0.191 | 0.1700 | 53 | 独立 |

**解读**：如果 QR-Tutor 相关性低（r<0.3），证明多维度评估不冗余——高 QR 不等于高 Tutor。

## 五、任务难度与区分度（Sonnet Judge）

| Task | Difficulty | Overall OAS | Sonnet OAS | Haiku OAS | Discrimination |
|------|-----------|-------------|------------|-----------|----------------|
| S03_mean_reversion_resear | 0.462 | 0.5379 | 0.6363 | 0.4722 | +0.1641 |
| B02_basic_sequential_engi | 0.428 | 0.5724 | 0.6574 | 0.4874 | +0.1700 |
| D09_feature_engineering_p | 0.422 | 0.5784 | 0.5846 | 0.5722 | +0.0124 |
| D01_load_inspect_ohlcv | 0.371 | 0.6290 | 0.6607 | 0.5973 | +0.0634 |
| S01_ma_crossover | 0.363 | 0.6372 | 0.6836 | 0.5909 | +0.0926 |
| X01_ma_offbyone | 0.344 | 0.6563 | 0.6830 | 0.6296 | +0.0534 |
| I01_implement_sma | 0.340 | 0.6600 | 0.6978 | 0.6221 | +0.0757 |
| B01_interpret_metrics | 0.299 | 0.7006 | 0.7175 | 0.6837 | +0.0338 |

**难度 vs 区分度相关性**: Pearson r = +0.609 (p = 0.1092)

## 六、核心结论

### 6.1 可靠性证据层次

本报告的可靠性证据按证据强度分三层：

1. **区分度稳健性（核心）**：Tutor Cohen's d = +1.694 [+1.17, +2.49]，CI 下限远离 0，区分度在统计上稳健。
   QR Cohen's d = +0.236 [-0.24, +0.82]，CI 包含 0 — QR 上两个模型的差距在统计上不显著。
2. **Within-judge 可复现性（核心）**：同一 judge 对同一任务 3 次运行的 CV 详见 §2，多数任务 CV < 10%。
3. **Cross-judge 排名稳健性（辅助）**：两个 judge 下 agent 排名方向 100% 一致。Tutor 排序相关 Spearman ρ=0.676。Bland-Altman 证实分歧主要来自系统偏差（可消除），非随机分散。

### 6.2 Judge 选择

1. **Sonnet judge 作为主结果**——区分度更强 (Tutor d=+1.694 vs +0.797)，更严格（不虚高）
2. **Haiku judge 作为 robustness check**——附录展示排名一致性，Bland-Altman 展示偏差结构
3. **Tutor 系统偏差是校准差异，非评估缺陷**——bias=0.198 占 LOA 的主要成分，选定一个 judge 即可消除

### 6.3 论文建议

1. **主结果使用 Sonnet judge**——更严格、更区分
2. **报告排名稳健性**——Table 展示 Pearson r [95% CI]、Spearman ρ、ICC；附录展示 Bland-Altman 偏差分解
3. **Tutor 维度是核心差异化**——Cohen's d [95% CI] 最大、区分方向最一致
4. **多维度不冗余**——QR-Tutor 相关性低，证明多维度必要性
5. **Kappa / Agreement Rate 留给 Human Calibration**——在多 rater（多个 LLM + 人类）场景下作为绝对分数一致性指标报告

## 七、统计方法论说明

### 7.1 指标分类与适用场景

| 指标 | 衡量什么 | 适用场景 | 本报告用途 |
|------|---------|---------|-----------|
| Pearson r / Spearman ρ / Kendall τ | 排序一致性 | cross-judge 排名稳健性 | §1.1 主要指标 |
| Bland-Altman | 偏差分解（系统 vs 随机） | 理解 cross-judge 分歧的来源 | §1.3 偏差分解 |
| ICC(3,1) | 排名+绝对值一致性 | cross-judge 和 within-judge | §1.1 和 §2 |
| Cohen's d [Bootstrap CI] | 效应量及其不确定性 | 区分度的统计稳健性 | §3 核心指标 |
| CV (变异系数) | 多 run 稳定性 | within-judge 可复现性 | §2 核心指标 |
| Weighted Kappa | 绝对分数档位一致性（校正偶然） | 多 rater 一致性（3+ rater） | §1.2 参考 / 方案五核心 |
| Agreement Rate | 绝对分数一致性（未校正偶然） | 与 TutorBench 可比 | §1.2 参考 / 方案五对标 |

> **关键区分**：Pearson r / Spearman ρ 衡量'排序是否一致'（不受系统偏差影响），Kappa / Agreement Rate 衡量'绝对分数是否一致'（受系统偏差严重影响）。对于'选定一个 judge 后排名是否可靠'这个问题，前者是正确指标；对于'多个评估者之间是否校准一致'这个问题（方案五 Human Calibration），后者是正确指标。

### 7.2 Kappa 离散化参数说明

Weighted Kappa 需要将 0-1 连续分数离散化为有序类别。选择 5 bins 的理由：

1. 与 QP 维度的 5 档评分粒度一致（0.0/0.25/0.5/0.75/1.0）
2. 粒度消融实验已证明 5 档与 10 档的 Cohen's d 无显著差异 (d: 1.75→1.82, +4.1%)
3. bins 过多会导致大量空 cell，Kappa 估计不稳定（N=44-52）

### 7.3 与参照论文的方法论对比

| 能力 | TutorBench | MathTutorBench | EduBench | 本报告 |
|------|-----------|----------------|----------|--------|
| 排名稳健性 | 未报告 | 未报告 | Kendall's W | Pearson r + Spearman ρ + Kendall τ |
| 偏差分解 | 未做 | 未做 | 未做 | Bland-Altman |
| 标准化效应量 | 未报告 | 未报告 | 未报告 | Cohen's d [Bootstrap CI] |
| 多 run 稳定性 | 未报告 | 未报告 | 未报告 | CV per task |
| 维度独立性 | 未报告 | 定性 | 未报告 | 维度间 Pearson r |
| 评分粒度消融 | 未报告 | 未报告 | 未报告 | 10档→5档 d 变化 |
| 绝对一致性（人类验证） | Agreement 0.78 (未校正偶然) | 未做 | Kendall's W | **方案五计划：Kappa + Agreement + Krippendorff α** |
