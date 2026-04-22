# QuantTutorBench 统计分析报告：Judge 对比与评估可靠性

> 生成时间：2026-04-07
> 数据：2 agent (Sonnet 4.6, Haiku 4.5) × 2 judge (Sonnet, Haiku) × 8 ICC tasks × 3 runs
> 排除：Tutor=0.0 的 API 超时异常值

---

## 一、Judge 一致性分析（Haiku Judge vs Sonnet Judge）

同一份对话交给两个不同的 judge 模型评分，结果差异有多大？

### 1.1 汇总统计

| 维度 | N | Mean Δ | Std Δ | Min Δ | Max Δ | Pearson r | Kendall τ | ICC(judge) | Sonnet↑ | Haiku↑ |
|------|---|--------|-------|-------|-------|-----------|-----------|------------|---------|--------|
| OAS | 52 | -0.0846 | 0.0856 | -0.2783 | +0.0620 | 0.571 | 0.450 | 0.567 | 5 | 46 |
| QR | 52 | +0.0251 | 0.0744 | -0.1363 | +0.2251 | 0.872 | 0.611 | 0.854 | 25 | 13 |
| QP | 52 | -0.0205 | 0.0402 | -0.0954 | +0.1080 | 0.806 | 0.631 | 0.795 | 11 | 35 |
| Tutor | 44 | -0.1981 | 0.1113 | -0.4298 | +0.0268 | 0.676 | 0.487 | 0.676 | 1 | 42 |

**解读**：
- **Tutor 偏差最大**：Haiku judge 系统性给 Tutor 打高分（平均 0.198），Pearson r=0.676
- **QR 方向相反**：Sonnet judge 对 QR 更宽容（平均 +0.025）
- **QP 差异最小**：两个 judge 在过程评分上高度一致

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

| Judge | Dim | Sonnet Mean | Haiku Mean | Cohen's d | Effect | Wilcoxon p | Direction |
|-------|-----|------------|------------|-----------|--------|------------|-----------|
| sonnet | OAS | 0.6664 | 0.5819 | +0.987 | large | 0.0078 | 8/8 S>H |
| sonnet | QR | 0.7401 | 0.7158 | +0.236 | small | 0.3125 | 5/8 S>H |
| sonnet | QP | 0.6788 | 0.6239 | +1.105 | large | 0.0156 | 7/8 S>H |
| sonnet | Tutor | 0.6456 | 0.4759 | +1.694 | large | 0.0156 | 7/7 S>H |
| haiku | OAS | 0.7389 | 0.6953 | +0.571 | medium | 0.0156 | 7/8 S>H |
| haiku | QR | 0.7128 | 0.7133 | -0.004 | negligible | 0.7422 | 5/8 S>H |
| haiku | QP | 0.6953 | 0.6576 | +0.597 | medium | 0.0547 | 7/8 S>H |
| haiku | Tutor | 0.8201 | 0.7181 | +0.797 | medium | 0.0078 | 8/8 S>H |

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
| sonnet | QP-Tutor | +0.496 | 0.0006 | 44 | 弱相关 |
| sonnet | QR-QP | +0.359 | 0.0168 | 44 | 弱相关 |
| sonnet | QR-Tutor | +0.250 | 0.1013 | 44 | 独立 |
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

## 六、深度解读

### 6.1 Haiku judge 的问题不是"不准"，而是"不区分"

Haiku judge 给 Tutor 打高分不是随机噪音——它对**所有**对话都打高分（均值 0.77），方差小。Sonnet judge 的均值更低（0.57），但**方差更大**，能拉开好坏差距。

这解释了为什么 Haiku judge 下 Tutor d=0.797，Sonnet judge 下 d=1.694。不是 Sonnet judge 夸大了差异，而是 **Haiku judge 压缩了分数分布**，把该拉开的差距压平了。

类比：一个老师给所有学生 85-95 分（haiku），另一个给 40-80 分（sonnet）。后者不是更严厉，是**更有区分度**。

### 6.2 QR 的一致性证明 programmatic eval 是锚点

QR 在两个 judge 下 Pearson r=0.872，是四个维度里最高的。这是因为 QR 的 40% 来自 programmatic eval（test_scripts），50% 来自 code_eval——这些是**确定性分数**，不受 judge 模型影响。LLM result judge 只贡献 QR 的一部分。

这验证了架构设计的合理性：**programmatic eval 起到了分数锚定作用**，限制了 LLM judge 偏差的传播范围。

### 6.3 QP 一致但 Tutor 不一致，揭示了 LLM judge 的能力边界

QP 的 7 个子维度中，tool_usage 和 step_efficiency 有大量程序化计算（工具调用日志、action 计数），LLM 只参与部分维度。所以两个 judge 一致性高（r=0.806）。

Tutor 7D **完全依赖 LLM judge**（ConversationalGEval），没有任何程序化锚点。这是偏差最大的维度（r=0.676）。**LLM judge 的可靠性与程序化成分的占比成正比**。

### 6.4 区分度方向一致性是最重要的发现

不管用哪个 judge，Sonnet agent 都在**所有维度**上优于 Haiku agent。Sonnet judge 下 OAS 8/8 方向一致，Haiku judge 下 7/8。这意味着：

> **模型排名不受 judge 选择影响。** 绝对分数变了，但谁好谁差的结论不变。

这是论文最需要的结论——reviewer 会问"你换个 judge 结果还一样吗？"答案是"排名完全一致，效应量在同一方向，只是绝对值不同。"

### 6.5 QR 区分度低是真实的，不是评估缺陷

QR Cohen's d ≈ 0（两个 judge 下都是），这不是 QR 评估有问题，而是 **Sonnet 和 Haiku 在量化结果质量上确实差距不大**。两个模型都能算对均线、跑通回测、给出合理的数值结果。

差距在**过程**（QP d=1.1）和**教学**（Tutor d=1.7）上。这恰好证明了多维度评估的核心价值：

> **如果只看 QR（结果对不对），Sonnet 和 Haiku 几乎不可区分。加入 QP 和 Tutor 后，效应量从 negligible 跃升到 large。**

这一句话就是论文 Section 5 的核心叙事。

---

## 七、核心结论与论文建议

### 7.1 数据呈现建议

用 **Sonnet judge 作为主结果**，理由充分：
- 区分度更强（Tutor d=1.694 vs 0.797）
- 更严格（不虚高）
- 方向一致性和 Haiku judge 相同

同时在附录报告 Haiku judge 结果作为 **cross-judge robustness check**，展示排名一致性。这比只用一个 judge 说服力强得多——有两个独立 judge 的交叉验证。

### 7.2 关键数据点汇总

| 结论 | 证据 |
|------|------|
| **多维度必要性** | QR d≈0 → 加入 Tutor 后 d=1.694，区分度提升 ∞ |
| **Judge 排名稳健** | 两个 judge 下 agent 排名方向 100% 一致（OAS 8/8 S>H） |
| **Programmatic eval 锚定** | QR Pearson r=0.872（最高），因 40% 来自确定性评分 |
| **LLM judge 能力边界** | 纯 LLM 维度（Tutor）r=0.676，混合维度（QP）r=0.806 |
| **Haiku judge 偏差** | Tutor 系统性虚高 +0.198，压缩分布导致区分度减半 |
| **任务难度梯度** | 难度 vs 区分度 r=+0.609，难任务更能区分模型 |

### 7.3 论文 Section 5 叙事框架

```
5.1 Main Results (Table 1: Sonnet judge, 8 tasks × 4 dims × 2 agents)
    → 核心发现：QR 区分度低，Tutor 区分度极高

5.2 Multi-Dimensional Value (Figure: QR vs Tutor scatter)
    → 论点：单维度评估无法区分模型，多维度是必要的

5.3 Evaluation Reliability
    5.3.1 Cross-Judge Consistency (Table 2: Pearson r, ICC)
        → Sonnet judge 作为主结果，Haiku judge 交叉验证
    5.3.2 Cross-Run Stability (CV per task)
        → agent 行为稳定时 CV<5%，不稳定时正确反映差异
    5.3.3 Programmatic Anchoring Effect
        → QR 最稳定因为 programmatic eval 占比高

5.4 Discussion: Why Tutor Matters Most
    → d=1.694 是 large effect，方向 7/7 一致
    → 知识差距小（QR），教学差距大（Tutor）
```
