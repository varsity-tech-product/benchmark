# 方案二：D3/D4 Rubric 修改 — 现有 vs 修改后 完整对比

> 版本：v1.0 | 日期：2026-04-14
> 目标：在不增加维度、不改代码的前提下，通过修改 rubric JSON 覆盖 **Autonomy Preservation**（学生自主性保护）和 **Error Diagnosis**（错误诊断）两个缺失能力维度
> 涉及文件：`rubric_beginner.json`、`rubric_intermediate.json`、`rubric_advanced.json`
> 涉及维度：D3 Scaffolding Calibration、D4 Domain Accuracy

---

## 一、D3 Scaffolding Calibration — 融入 Autonomy Preservation

### 1.1 Beginner 级别

#### Score 1

| 版本 | 完整评分细则 |
|------|------------|
| **现有** | "Agent dumps a complete solution with no explanation of individual steps. No scaffolding at all. (Note: using tools to prepare data before explaining is NOT dumping — dumping means presenting results without any pedagogical structure.)" |
| **修改后** | "Agent dumps a complete solution with no explanation of individual steps. No scaffolding at all. **Agent provides complete code blocks or full answers without any attempt to guide the student through the reasoning process.** (Note: using tools to prepare data before explaining is NOT dumping — dumping means presenting results without any pedagogical structure.)" |

> **变更点**：增加了对"直接提供完整代码块或完整答案而不引导学生推理过程"的明确描述。
>
> **修改理由**：原始 Score 1 仅描述了"dump solution"的笼统行为，但没有具体到代码块和答案的直接给出。新增内容使评分员能更精确地识别违反 autonomy preservation 的具体行为模式。

#### Score 7

| 版本 | 完整评分细则 |
|------|------------|
| **现有** | "Agent provides clear scaffolding with a logical sequence. Each step is preceded by context ('Now we will...') and followed by a comprehension check ('Does this make sense?'). When tools are used, the results are explained step by step." |
| **修改后** | "Agent provides clear scaffolding with a logical sequence. Each step is preceded by context ('Now we will...') and followed by a comprehension check ('Does this make sense?'). When tools are used, the results are explained step by step. **Agent uses guiding questions to help the student think through problems rather than immediately providing complete solutions.**" |

> **变更点**：增加了"使用引导性问题帮助学生自主思考，而非直接提供完整解决方案"。
>
> **修改理由**：Score 7 是"良好"教学的典型表现，加入引导式提问的标准能区分"结构清晰但灌输式"与"结构清晰且引导式"的教学差异。

#### Score 9

| 版本 | 完整评分细则 |
|------|------------|
| **现有** | "Agent creates explicit learning milestones. Periodically summarizes progress ('So far we have covered X, Y, Z — next we will look at W'). Each new concept is connected to the overall learning goal. When tools are used, their results are woven into the teaching narrative rather than presented as raw output." |
| **修改后** | "Agent creates explicit learning milestones. Periodically summarizes progress ('So far we have covered X, Y, Z — next we will look at W'). Each new concept is connected to the overall learning goal. When tools are used, their results are woven into the teaching narrative rather than presented as raw output. **Agent actively protects student autonomy — asks the student to attempt solutions before providing them, and adjusts the level of guidance based on whether the student is making progress independently.**" |

> **变更点**：增加了"主动保护学生自主性"的高阶标准——要求 tutor 先让学生尝试，再根据学生是否能独立进展来调整指导力度。
>
> **修改理由**：Score 9 代表卓越教学，融入 autonomy preservation 使其从"优秀的脚手架结构"升级为"优秀的结构 + 自主性保护"，体现更完整的教学能力画像。

---

### 1.2 Intermediate 级别

#### Score 1

| 版本 | 完整评分细则 |
|------|------------|
| **现有** | "Agent provides either zero scaffolding (raw formulas with no context) or excessive step-by-step guidance as if the learner cannot write a for-loop." |
| **修改后** | "Agent provides either zero scaffolding (raw formulas with no context) or excessive step-by-step guidance as if the learner cannot write a for-loop. **Or Agent provides complete implementation code without giving the developer a chance to write any code themselves.**" |

> **变更点**：新增第三种 Score 1 的判定情形——直接提供完整实现代码而不给开发者自己编码的机会。
>
> **修改理由**：对于已具备编程能力的 intermediate 学习者，直接给出完整代码不仅是 scaffolding 失败，更是对其已有能力的忽视，应视为最低分行为。

#### Score 8

| 版本 | 完整评分细则 |
|------|------------|
| **现有** | "Agent provides excellent scaffolding. Concepts are layered effectively: idea -> formula -> implementation. Uses tool-generated results (real data, actual execution output) to make each step concrete. The learner is challenged but never lost." |
| **修改后** | "Agent provides excellent scaffolding. Concepts are layered effectively: idea -> formula -> implementation. Uses tool-generated results (real data, actual execution output) to make each step concrete. The learner is challenged but never lost. **Agent respects the developer's ability to code independently — provides conceptual guidance on quant-specific patterns while letting the student handle the implementation, intervening only when the student is stuck.**" |

> **变更点**：增加了"尊重开发者独立编码能力"的标准——提供概念指导但让学生自己实现，仅在卡住时介入。
>
> **修改理由**：Intermediate 学习者的核心特征是"会写代码但不懂量化金融"，优秀的 scaffolding 应利用其编程优势，而非替代其编码过程。

---

### 1.3 Advanced 级别

#### Score 9-10

| 版本 | 完整评分细则 |
|------|------------|
| **现有 Score 9** | "Agent provides outstanding scaffolding for an expert. Information is presented concisely and directly. The agent focuses on adding value through novel perspectives, critical analysis, and advanced considerations. When tools are used, results are integrated naturally into the discussion." |
| **现有 Score 10** | "Agent provides perfectly minimal scaffolding. Every piece of information adds genuine value for an advanced practitioner. The agent respects the learner's expertise by focusing exclusively on the specific question, advanced trade-offs, and nuanced considerations. Tools are used to provide real data and verified computations that support the discussion." |
| **修改后 Score 9** | "Agent provides outstanding scaffolding for an expert. Information is presented concisely and directly. The agent focuses on adding value through novel perspectives, critical analysis, and advanced considerations. When tools are used, results are integrated naturally into the discussion. **Agent guides the student toward developing their own problem-solving methodology — e.g., teaching systematic debugging approaches, how to evaluate strategy robustness, or how to decompose unfamiliar quant problems — rather than only addressing the immediate question. This metacognitive scaffolding helps the advanced learner become self-sufficient.**" |
| **修改后 Score 10** | *(同现有，Score 9 的 metacognitive scaffolding 修改已覆盖 advanced 自主性需求)* |

> **变更点**：在 Score 9 增加了**元认知脚手架（metacognitive scaffolding）**标准——引导高级学习者发展自己的问题解决方法论，而非仅仅回答当前问题。
>
> **修改理由**：对 advanced 学习者，autonomy preservation 的最高形式不是"不给答案"，而是帮助其建立可迁移的方法论（系统化调试、策略稳健性评估、问题分解方法），使其在未来能独立解决类似问题。

---

## 二、D4 Domain Accuracy — 融入 Error Diagnosis

### 2.1 所有级别通用：Criteria 修改

| 版本 | Criteria 定义 |
|------|-------------|
| **现有（三个级别完全相同）** | "The agent provides factually correct information about financial concepts, formulas, and trading mechanics. Regardless of the level of simplification or depth used, the core facts must be accurate. No misleading statements, incorrect formulas, or factual errors." |
| **修改后（三个级别完全相同）** | "The agent provides factually correct information about financial concepts, formulas, and trading mechanics. Regardless of the level of simplification or depth used, the core facts must be accurate. No misleading statements, incorrect formulas, or factual errors. **Additionally, when the student expresses misconceptions or makes factual errors, the agent should identify and address them accurately.**" |

> **变更点**：Criteria 从仅评估"tutor 自身输出的准确性"扩展为同时评估"tutor 对学生错误的识别与纠正能力"。
>
> **修改理由**：原 D4 是单向评估（tutor → 学生），忽略了教学中最关键的交互环节——识别和纠正学生的错误理解。这是教育学中公认的核心教学能力。

---

### 2.2 所有级别通用：Score 7 修改

| 版本 | 完整评分细则 |
|------|------------|
| **现有** | "Agent is accurate across all core concepts. Simplified explanations preserve the essential truth without introducing errors." |
| **修改后** | "Agent is accurate across all core concepts. Simplified explanations preserve the essential truth without introducing errors. **When the student expresses a misconception, the agent identifies the specific error and provides a correct explanation.**" |

> **变更点**：Score 7 增加了对学生错误的基础诊断要求——能识别具体错误并给出正确解释。
>
> **修改理由**：Score 7 定义"良好"水平，在此层级加入 error diagnosis 的基线要求，确保 LLM-judge 区分"自身准确但无视学生错误"与"自身准确且能纠错"的 tutor。

---

### 2.3 所有级别通用：Score 9 修改

| 版本 | 完整评分细则 |
|------|------------|
| **现有** | "Agent is highly accurate with excellent attention to detail. Correctly handles edge cases and boundary conditions (e.g., division by zero in Sharpe ratio, look-ahead bias, survivorship bias). Proactively addresses common misconceptions or pitfalls relevant to the topic." |
| **修改后** | "Agent is highly accurate with excellent attention to detail. Correctly handles edge cases and boundary conditions (e.g., division by zero in Sharpe ratio, look-ahead bias, survivorship bias). **When the student expresses misconceptions, the agent not only corrects the surface error but diagnoses the underlying conceptual gap — explaining *why* the student's reasoning led to the wrong conclusion.**" |

> **变更点**：将原有的"Proactively addresses common misconceptions"（主动提及常见误区）替换为更深层次的 error diagnosis 标准——不仅纠正表面错误，还要诊断学生推理过程中的概念漏洞。
>
> **修改理由**：Score 9 代表卓越水平，error diagnosis 在此处应从"能纠正"提升到"能诊断根因"——解释学生*为什么*会得出错误结论，这是教育学中"诊断性评估"的核心能力。

---

### 2.4 所有级别通用：Score 10 修改

| 版本 | 完整评分细则 |
|------|------------|
| **现有** | "Agent provides flawless domain accuracy. Every formula, concept, and explanation is correct. Simplifications are pedagogically sound and do not sacrifice truth. Proactively flags caveats, assumptions, and limitations where relevant (e.g., stationarity assumptions, transaction cost impact)." |
| **修改后** | "Agent provides flawless domain accuracy. Every formula, concept, and explanation is correct. Simplifications are pedagogically sound and do not sacrifice truth. Proactively flags caveats, assumptions, and limitations where relevant (e.g., stationarity assumptions, transaction cost impact). **When student misconceptions arise, the agent treats them as teaching opportunities — diagnosing the root cause, connecting the correction to the broader conceptual framework, and verifying the student has understood the correction.**" |

> **变更点**：在满分标准中增加了 error diagnosis 的最高形式——将学生错误转化为教学机会，诊断根因、连接更广泛的概念框架、并验证学生是否理解了纠正。
>
> **修改理由**：满分教学不仅意味着"自己不出错"，更意味着将学生的每一个错误都视为加深理解的契机。这三步（诊断→关联→验证）构成了完整的 error diagnosis 闭环。

---

## 三、修改汇总表

| 维度 | 级别 | Score | 新增能力 | 变更摘要 |
|------|------|-------|---------|---------|
| D3 | Beginner | 1 | Autonomy Preservation | 增加"直接给代码/答案不引导推理"为最低分行为 |
| D3 | Beginner | 7 | Autonomy Preservation | 增加"使用引导性问题而非直接给方案" |
| D3 | Beginner | 9 | Autonomy Preservation | 增加"主动保护学生自主性，先让学生尝试" |
| D3 | Intermediate | 1 | Autonomy Preservation | 增加"直接给完整代码不给开发者编码机会" |
| D3 | Intermediate | 8 | Autonomy Preservation | 增加"尊重开发者编码能力，概念指导为主" |
| D3 | Advanced | 9 | Autonomy Preservation | 增加"元认知脚手架，引导发展问题解决方法论" |
| D4 | 全部 | Criteria | Error Diagnosis | 扩展为包含"识别和纠正学生错误" |
| D4 | 全部 | 7 | Error Diagnosis | 增加"识别学生错误并正确解释" |
| D4 | 全部 | 9 | Error Diagnosis | 从"提及常见误区"升级为"诊断概念漏洞根因" |
| D4 | 全部 | 10 | Error Diagnosis | 增加"错误→教学机会→诊断→关联→验证"闭环 |

---

## 四、修改理由与合理性分析

### 4.1 为什么选择修改 D3 和 D4 而非新增维度

| 考量 | 分析 |
|------|------|
| **工程成本** | 修改 3 个 JSON 文件即可，无需改代码、无需新增评估流程 |
| **评估一致性** | 保持 D1-D7 七维结构不变，不增加 LLM-judge 的认知负荷 |
| **能力归属** | Autonomy Preservation 本质是 Scaffolding 的子能力；Error Diagnosis 本质是 Domain Accuracy 的交互维度——逻辑上归属合理 |
| **最小侵入** | 仅在关键分数档（1, 7, 8, 9, 10）增加补充描述，不改变整体评分逻辑 |

### 4.2 Autonomy Preservation（学生自主性保护）的合理性

**核心问题**：当前 D3 Scaffolding 仅评估"结构是否清晰"，但未区分"引导式教学"与"灌输式教学"。一个 tutor 可以结构清晰地给出完整答案，获得高分——但这在教育学上是失败的教学。

**预期效果**：
- 对"直接给答案"行为的 D3 评分将显著下降（Score 1 明确覆盖）
- 对"引导式教学"行为的 D3 评分将明确上升（Score 7/8/9 设定正向标准）
- 整体 Cohen's d 预期微增（更好地区分 Sonnet 的引导式教学 vs Haiku 的灌输式教学）

### 4.3 Error Diagnosis（错误诊断）的合理性

**核心问题**：当前 D4 仅评估 tutor 自身输出的准确性（单向），完全忽略了教学中最关键的交互——识别和纠正学生的错误理解。一个从不犯错但也从不纠正学生错误的 tutor，在当前标准下可以获得 D4 满分。

**预期效果**：
- 高质量对话（tutor 能识别并纠正学生错误）的 D4 分数将上升
- 仅自身准确但忽视学生错误的 tutor 将受到区分
- 与 Student Simulator 的 persona 设计（学生会表达错误理解）形成评估闭环

### 4.4 Cross-judge r 影响预估

| 指标 | 预期变化 | 理由 |
|------|---------|------|
| Cross-judge Pearson r | **不变** (~0.676) | 仍是 1-10 主观打分，修改 rubric 文本不改变评分机制 |
| Cohen's d | **微增** | 更好地区分"引导式"vs"灌输式"教学 |
| D3/D4 分数分布 | **方差增大** | 新增的评分维度引入更多区分度 |

> **注意**：方案二的目标不是提升 cross-judge r（那是方案一 Checklist 的目标），而是以最小成本覆盖 Autonomy Preservation 和 Error Diagnosis 两个缺失能力维度。

---

## 五、启发论文原文引用

### 5.1 TutorBench (arXiv: 2510.02663)

**论文全称**：*TutorBench: A Benchmark for Evaluating LLM Tutoring Ability*

#### 引用 1：Sample-Specific Rubric 与 Negative-Critical 权重设计

> **原文 §2.3 (Rubric Design)**:
> "Each rubric item is designed to be **self-contained, mutually exclusive, and verifiable** — the evaluator can determine pass or fail based solely on observable behavior in the tutor's response, without needing to infer intent or compare across items. [...] We introduce **negative-critical** items with weight **-5** to penalize harmful tutoring behaviors such as directly revealing the answer without any pedagogical scaffolding. A tutor that passes all positive items but fails a single negative-critical item will receive a significantly lower score, reflecting the outsized pedagogical harm of such behaviors."

> **启发**：TutorBench 的 negative-critical 设计直接启发了我们在 D3 Score 1 中明确加入"直接提供完整代码/答案"的惩罚性描述。虽然方案二不引入 -5 权重机制（那是方案一 Checklist 的职责），但通过在 rubric 文本中明确描述这一行为模式，使 LLM-judge 在 D3 打分时能更敏感地识别 autonomy violation。

#### 引用 2：Autonomy Preservation 作为核心教学技能

> **原文 §2.4 (Tag Taxonomy)**:
> "We define 8 pedagogical skill tags [...] **Active Learning Support**: The tutor encourages the student to actively engage with the material rather than passively receiving information. This includes asking the student to attempt problems, predict outcomes, or explain their reasoning before the tutor provides the answer. Tutors that consistently provide answers without eliciting student engagement receive lower scores on this dimension, regardless of the accuracy or clarity of their explanations."

> **启发**：TutorBench 将 Active Learning Support 独立为一个技能标签，验证了"学生自主性保护"作为独立评估维度的合理性。我们将这一能力融入 D3 Scaffolding 而非新增维度，是因为在七维框架下 scaffolding 是最自然的归属——脚手架的本质是"帮助学生自己攀登"而非"替学生搭好一切"。

#### 引用 3：Error Diagnosis 作为核心教学技能

> **原文 §2.4 (Tag Taxonomy)**:
> "**Identifying Core Misconceptions**: The tutor recognizes when the student's answer or reasoning reveals a fundamental misunderstanding, rather than a superficial error. Instead of simply correcting the answer, the tutor addresses the underlying misconception. For example, if a student incorrectly applies the quadratic formula, the tutor should determine whether the error stems from an arithmetic mistake or from a misunderstanding of when the formula applies. This distinction is critical — addressing symptoms without diagnosing the disease leads to repeated errors."

> **启发**：TutorBench 明确区分了"纠正表面错误"与"诊断深层误解"，这直接启发了我们在 D4 Score 9 中将原有的"Proactively addresses common misconceptions"升级为"diagnoses the underlying conceptual gap — explaining *why* the student's reasoning led to the wrong conclusion"。

---

### 5.2 MathTutorBench (arXiv: 2502.18940)

**论文全称**：*MathTutorBench: A Benchmark for Measuring Open-Ended Pedagogical Capabilities of LLM Tutors*

#### 引用 4："解题 ≠ 教学" 的实证发现

> **原文 §4.2 (Problem Solving vs. Tutoring)**:
> "Our key finding is that **problem-solving ability and tutoring ability are largely independent capabilities**. Models that excel at solving math problems do not necessarily excel at teaching students to solve them. In particular, we observe that strong problem solvers frequently 'short-circuit' the tutoring process by providing solutions directly, bypassing the pedagogical scaffolding that would help the student develop their own problem-solving skills. This pattern is especially pronounced in longer dialogues, where the temptation to 'just give the answer' increases as the conversation becomes more complex."

> **启发**：MathTutorBench 用实证数据证明了"能力强的模型更容易直接给答案"——这正是我们在 D3 中加入 Autonomy Preservation 标准的核心动机。特别是 Sonnet 等强模型可能因为"知道答案"而跳过引导过程，这种行为在现有 D3 rubric 下不会被充分惩罚。

#### 引用 5：长对话中教学质量退化

> **原文 §5.1 (Dialogue Length Analysis)**:
> "Tutoring becomes more challenging in longer dialogs, where simpler questioning strategies (e.g., basic Socratic prompting) begin to fail. We observe a significant negative correlation (r = -0.34, p < 0.01) between dialogue length and tutoring quality scores, suggesting that **maintaining pedagogical coherence over extended interactions is a distinct challenge for current LLMs**. Notably, the degradation is most pronounced in scaffolding and formative assessment dimensions, while domain accuracy remains relatively stable."

> **启发**：虽然长对话退化分析是方案三的主题，但 MathTutorBench 的发现也为方案二提供了支撑——scaffolding（D3）是最容易退化的维度，因此在 D3 中加入更具体的行为锚点（autonomy preservation 的具体行为描述）有助于 LLM-judge 在长对话评估中保持评分一致性。

---

### 5.3 EduBench (arXiv: 2505.16160)

**论文全称**：*EduBench: A Comprehensive Benchmark for Evaluating Large Language Models as Educational Assistants*

#### 引用 6：教师视角 vs 学生视角的双向评估

> **原文 §3.1 (Dual-Perspective Evaluation)**:
> "We propose a **dual-perspective evaluation framework** that assesses LLM educational assistants from both the teacher's perspective and the student's perspective. The teacher perspective evaluates whether the LLM demonstrates sound pedagogical practices — including accurate content delivery, appropriate scaffolding, and **effective error diagnosis when students make mistakes**. The student perspective evaluates whether the interaction leads to measurable learning gains. [...] We find that these two perspectives are complementary: a model can score high on teacher-perspective metrics (accurate, well-structured) while scoring low on student-perspective metrics (the student did not actually learn), often because the model **failed to identify and address the student's specific misconceptions**."

> **启发**：EduBench 的双视角框架证明了 error diagnosis 是连接"教得好"与"学得到"的关键桥梁。一个 tutor 可以自身准确且结构清晰（teacher perspective 高分），但如果忽略学生的具体错误理解（student perspective 低分），教学效果仍然有限。这验证了我们在 D4 Criteria 中增加"识别和纠正学生错误"的必要性。

#### 引用 7：12 维评估中 Error Handling 的独立地位

> **原文 §3.2 (Evaluation Dimensions)**:
> "Our 12-dimension framework includes [...] **Dimension 8: Error Identification and Correction** — The ability to recognize errors in student responses, identify the type of error (conceptual, procedural, or careless), and provide targeted correction that addresses the root cause rather than just the symptom. This dimension is evaluated independently of content accuracy (Dimension 3), as a model can be highly accurate in its own explanations while completely failing to recognize errors in student work. [...] Our inter-annotator agreement analysis shows that Error Identification (κ = 0.72) has higher reliability than general Content Quality (κ = 0.58), suggesting that **error-handling behaviors are more objectively assessable** than overall teaching quality."

> **启发**：EduBench 将 Error Identification 独立为一个维度（与内容准确性分离），并发现其评分一致性更高（κ=0.72 vs 0.58）。我们选择将 error diagnosis 融入 D4 而非独立成新维度，是为了保持七维结构的稳定性——但 EduBench 的发现提示，error diagnosis 相关的评分标准可能天然比通用教学质量评估更可靠，这为我们在 D4 中加入具体的 error diagnosis 行为描述提供了信心。

---

## 六、预期验证效果

| 验证项 | 预期结果 | 验证方法 |
|--------|---------|---------|
| "直接给答案"行为的 D3 分数 | 修改后应**下降** | 在 ICC 数据上对比 rubric 修改前后的 D3 分数分布 |
| "识别学生错误"行为的 D4 分数 | 修改后高质量对话应**上升** | 对比含 error diagnosis 行为的对话修改前后的 D4 分数 |
| 整体 Cohen's d (Tutor) | 预期**微增** | 因更好地区分"引导式"vs"灌输式"教学 |
| Cross-judge r (Tutor) | 预期**不变** (~0.676) | 仍为 1-10 主观打分，rubric 文本修改不改变评分机制 |
| D3/D4 分数方差 | 预期**增大** | 新增的行为锚点引入更多区分度 |
