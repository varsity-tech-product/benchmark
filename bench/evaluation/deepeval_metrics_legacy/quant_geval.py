"""GEval configuration for Layer 1 quant knowledge scoring.

Design doc §5.0: Layer 1 has 6 task categories, each needing specific evaluation criteria.
- Conceptual Q&A:        GEval with domain expertise rubric
- Strategy Explanation:   GEval with domain expertise rubric
- Code Generation:        Automated execution + unit tests (separate path)
- Code Debugging:         Automated execution + unit tests (separate path)
- Data Interpretation:    GEval with data interpretation rubric
- Multi-step Reasoning:   GEval + ToolCorrectnessMetric

Reference: https://github.com/confident-ai/deepeval

DeepEval API (v3.8+):
    from deepeval.metrics import GEval
    from deepeval.metrics.g_eval import Rubric
    from deepeval.test_case import LLMTestCase, LLMTestCaseParams
"""

from typing import Optional

from config.model_resolver import resolve_deepeval_model

try:
    from deepeval.metrics import GEval
    from deepeval.metrics.g_eval import Rubric
    from deepeval.test_case import LLMTestCaseParams

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# ──────────────────────────────────────────────────────────────
# Category-specific GEval criteria (design doc §5.0)
# ──────────────────────────────────────────────────────────────

CONCEPTUAL_QA_CRITERIA = """You are evaluating a quantitative finance tutoring response to a conceptual question.

WHAT TO EVALUATE:
Does the response correctly and completely explain the financial concept asked about?

SCORING RUBRIC (1-10 scale):

Score 1-2 — Critically Incorrect:
Contains factual errors in financial concepts, formulas, or definitions. Confuses fundamental terms
(e.g., variance vs. standard deviation, Sharpe vs. Sortino ratio). Would mislead a student.

Score 3-4 — Below Average:
Factually correct at a surface level but superficial. Repeats textbook definitions without insight.
Misses key nuances or related concepts. Does not connect to practical applications.

Score 5-6 — Sufficient:
Correct explanation covering the main concept. Defines key terms accurately. Mentions at least
one practical use case. Addresses the core of the question without major gaps.

Score 7-8 — Good:
Correct, nuanced explanation with context on assumptions and limitations. Demonstrates practical
application with a specific example (e.g., "In a momentum strategy, you would..."). Proactively
mentions related concepts and common misconceptions. Appropriate depth for the question.

Score 9-10 — Excellent:
Deeply nuanced explanation addressing edge cases and common misconceptions. Draws connections
between concepts to build a coherent knowledge framework. Uses concrete quantitative examples.
Distinguishes between theoretical and practical considerations. Would genuinely help a student
build lasting understanding.

EVALUATION INSTRUCTIONS:
- Verify FACTUAL ACCURACY of all financial claims, formulas, and definitions.
- An engaging but factually wrong response should score very low.
- Completeness: does the response address ALL parts of the question?
- Consider whether the depth is appropriate (not too superficial, not unnecessarily complex)."""

CONCEPTUAL_QA_RUBRIC = [
    {
        "score_range": (0, 2),
        "expected_outcome": "Factually incorrect. Contains major errors in financial concepts, formulas, or definitions. Confuses fundamental terms.",
    },
    {
        "score_range": (3, 4),
        "expected_outcome": "Partially correct but superficial. Repeats textbook definitions without insight. Misses key nuances.",
    },
    {
        "score_range": (5, 6),
        "expected_outcome": "Correct explanation covering the main concept. Defines key terms accurately with at least one practical use case.",
    },
    {
        "score_range": (7, 8),
        "expected_outcome": "Correct, nuanced explanation with practical examples, assumptions, limitations, and related concepts.",
    },
    {
        "score_range": (9, 10),
        "expected_outcome": "Deeply nuanced. Addresses edge cases, misconceptions, builds coherent knowledge framework with concrete quant examples.",
    },
]


STRATEGY_EXPLANATION_CRITERIA = """You are evaluating a quantitative finance tutoring response about a trading or investment strategy.

WHAT TO EVALUATE:
Does the response correctly explain the strategy's logic, assumptions, strengths, weaknesses,
and practical implementation considerations?

SCORING RUBRIC (1-10 scale):

Score 1-2 — Critically Incorrect:
Describes the strategy mechanism incorrectly. Confuses entry/exit logic, misidentifies signals,
or provides fundamentally wrong parameter guidance. Would lead to a flawed implementation.

Score 3-4 — Below Average:
Describes the basic mechanism correctly but omits critical assumptions or limitations.
No discussion of market conditions where the strategy fails. No risk considerations.

Score 5-6 — Sufficient:
Correctly describes the strategy mechanism and basic parameters. Mentions at least one key
assumption or limitation. Provides a reasonable overview suitable for an intermediate learner.

Score 7-8 — Good:
Correct and thorough. Discusses the strategy's theoretical basis, parameter sensitivity,
market regime dependence, risk management, and transaction cost considerations.
Provides implementation guidance (e.g., "use a 50/200 day crossover for trend following").

Score 9-10 — Excellent:
Comprehensive treatment covering: mechanism, mathematical formulation, parameter optimization,
regime analysis, risk/return profile, common pitfalls, comparison with alternatives, and
practical implementation details including code structure. Discusses backtesting methodology
and out-of-sample validation.

EVALUATION INSTRUCTIONS:
- Verify the strategy description is technically correct.
- Check that risks and limitations are mentioned (not just the "happy path").
- A strategy explanation that only covers "how it works" without "when it fails" is incomplete."""

STRATEGY_EXPLANATION_RUBRIC = [
    {
        "score_range": (0, 2),
        "expected_outcome": "Strategy mechanism described incorrectly. Would lead to flawed implementation.",
    },
    {
        "score_range": (3, 4),
        "expected_outcome": "Basic mechanism correct but omits critical assumptions, limitations, or risk considerations.",
    },
    {
        "score_range": (5, 6),
        "expected_outcome": "Correct description with basic parameters and at least one key assumption or limitation.",
    },
    {
        "score_range": (7, 8),
        "expected_outcome": "Thorough coverage of mechanism, parameter sensitivity, regime dependence, risk management, and implementation guidance.",
    },
    {
        "score_range": (9, 10),
        "expected_outcome": "Comprehensive: mathematical formulation, parameter optimization, regime analysis, pitfalls, alternatives, backtesting methodology.",
    },
]


CODE_QUALITY_CRITERIA = """You are evaluating a quantitative finance tutoring response that includes Python code.

WHAT TO EVALUATE:
Is the code correct, efficient, well-explained, and appropriate for the task?
Does the explanation help the student UNDERSTAND the code, not just copy it?

SCORING RUBRIC (1-10 scale):

Score 1-2 — Critically Incorrect:
Code contains bugs, syntax errors, or produces wrong results. Uses incorrect pandas/numpy operations.
Financial calculations are wrong (e.g., wrong return calculation, incorrect moving average logic).

Score 3-4 — Below Average:
Code runs but is poorly written: non-idiomatic Python, no error handling, hard-coded values,
no comments. Explanation is minimal or missing. Code complexity doesn't match the task.

Score 5-6 — Sufficient:
Code is correct and produces right results. Uses appropriate libraries (pandas, numpy).
Includes basic comments. Explanation covers what the code does at a high level.

Score 7-8 — Good:
Correct, efficient code using vectorized operations where appropriate. Well-commented with
clear variable names. Explanation covers WHY each step is needed, not just WHAT it does.
Includes output interpretation. Code complexity matches the student's level.

Score 9-10 — Excellent:
Production-quality code: vectorized operations, proper error handling, configurable parameters,
clear documentation. Explanation teaches coding PATTERNS, not just this specific solution.
Includes performance considerations, edge case handling, and visualization of results.
Code serves as a learning tool, not just a deliverable.

EVALUATION INSTRUCTIONS:
- Execute the code mentally: would it produce correct results?
- Check financial calculations for accuracy.
- Evaluate whether the explanation helps understanding or is just comments restating the code.
- Code without explanation should score lower than code with good explanation."""

CODE_QUALITY_RUBRIC = [
    {
        "score_range": (0, 2),
        "expected_outcome": "Code contains bugs or produces wrong results. Financial calculations are incorrect.",
    },
    {
        "score_range": (3, 4),
        "expected_outcome": "Code runs but is poorly written. Minimal explanation. Non-idiomatic Python.",
    },
    {
        "score_range": (5, 6),
        "expected_outcome": "Correct code with appropriate libraries. Basic comments and high-level explanation.",
    },
    {
        "score_range": (7, 8),
        "expected_outcome": "Efficient, well-commented code. Explanation covers WHY, not just WHAT. Output interpretation included.",
    },
    {
        "score_range": (9, 10),
        "expected_outcome": "Production-quality code with error handling, configurable params, visualization. Teaches patterns, not just solutions.",
    },
]


DATA_INTERPRETATION_CRITERIA = """You are evaluating a quantitative finance tutoring response that involves interpreting financial data, charts, or tables.

WHAT TO EVALUATE:
Does the response correctly interpret the data? Does it draw valid conclusions?
Does it identify key patterns, trends, anomalies, or statistical properties?

SCORING RUBRIC (1-10 scale):

Score 1-2 — Critically Incorrect:
Misinterprets the data. Draws wrong conclusions from charts or tables. Confuses correlation
with causation. Reads trends in the wrong direction. Ignores obvious patterns.

Score 3-4 — Below Average:
Identifies obvious patterns (e.g., "the price went up") but provides no deeper analysis.
No discussion of statistical significance, sample size, or data quality.
Conclusions are vague or unsupported.

Score 5-6 — Sufficient:
Correctly identifies main patterns and trends. Provides reasonable interpretation with
some quantitative detail (e.g., specific percentages, date ranges). Draws valid high-level
conclusions.

Score 7-8 — Good:
Thorough interpretation addressing trends, volatility, correlations, and outliers.
Quantifies observations (e.g., "daily returns show 2.3% annualized volatility").
Discusses data limitations and potential biases. Connects observations to financial theory.

Score 9-10 — Excellent:
Comprehensive analysis: identifies patterns, quantifies them, tests statistical significance,
considers alternative explanations, discusses data quality and survivorship bias.
Creates a narrative that connects data observations to actionable insights.
Suggests follow-up analyses to validate findings.

EVALUATION INSTRUCTIONS:
- Check that numerical claims about the data are accurate.
- Evaluate whether conclusions are supported by the evidence presented.
- A response that describes data without interpreting it should score lower.
- Consider whether the response helps the student develop data analysis skills."""

DATA_INTERPRETATION_RUBRIC = [
    {
        "score_range": (0, 2),
        "expected_outcome": "Misinterprets data. Wrong conclusions. Confuses correlation with causation.",
    },
    {
        "score_range": (3, 4),
        "expected_outcome": "Identifies obvious patterns only. No statistical depth. Vague conclusions.",
    },
    {
        "score_range": (5, 6),
        "expected_outcome": "Correctly identifies main patterns with some quantitative detail. Valid high-level conclusions.",
    },
    {
        "score_range": (7, 8),
        "expected_outcome": "Thorough: trends, volatility, correlations, outliers. Quantified observations. Discusses data limitations.",
    },
    {
        "score_range": (9, 10),
        "expected_outcome": "Comprehensive: statistical significance, alternative explanations, data quality, actionable insights, follow-up analyses.",
    },
]


MULTI_STEP_REASONING_CRITERIA = """You are evaluating a quantitative finance tutoring response that requires multi-step reasoning.

WHAT TO EVALUATE:
Does the response correctly break down the problem into steps? Is each step logically valid?
Does the final answer follow from the intermediate steps? Are tools used appropriately?

SCORING RUBRIC (1-10 scale):

Score 1-2 — Critically Incorrect:
Logical errors in the reasoning chain. Skips critical steps. Final answer is wrong because
intermediate steps are wrong. Tool calls (if any) produce wrong inputs or misinterpret outputs.

Score 3-4 — Below Average:
Correct final answer but reasoning is unclear or skips steps. The student would not be able
to reproduce the reasoning. Tool usage is present but not explained.

Score 5-6 — Sufficient:
Correct reasoning with clear steps. Each step follows logically from the previous one.
Tools are used appropriately. Explanation is adequate but could be more detailed.

Score 7-8 — Good:
Clear, well-structured multi-step reasoning. Each step is explained with its purpose.
Tool usage is well-motivated ("We need to fetch the data first because...").
Intermediate results are checked for reasonableness. Alternative approaches mentioned.

Score 9-10 — Excellent:
Exemplary multi-step reasoning: problem decomposition, clear justification for each step,
error checking at each stage, sensitivity analysis, discussion of assumptions.
Teaches the PROCESS of structured problem-solving, not just this specific problem.
Considers what could go wrong at each step and how to verify.

EVALUATION INSTRUCTIONS:
- Trace the reasoning chain: does each step logically follow?
- Check that intermediate calculations are correct.
- A correct final answer with wrong intermediate steps should score lower.
- Evaluate whether the response teaches problem-solving methodology."""

MULTI_STEP_REASONING_RUBRIC = [
    {
        "score_range": (0, 2),
        "expected_outcome": "Logical errors in reasoning chain. Skips critical steps. Wrong final answer from wrong intermediates.",
    },
    {
        "score_range": (3, 4),
        "expected_outcome": "Correct answer but unclear reasoning. Steps skipped. Student cannot reproduce the logic.",
    },
    {
        "score_range": (5, 6),
        "expected_outcome": "Correct reasoning with clear steps. Each step follows logically. Adequate explanation.",
    },
    {
        "score_range": (7, 8),
        "expected_outcome": "Well-structured reasoning. Steps explained with purpose. Tool usage motivated. Intermediate checks. Alternatives mentioned.",
    },
    {
        "score_range": (9, 10),
        "expected_outcome": "Exemplary: problem decomposition, justification, error checking, sensitivity analysis, teaches structured problem-solving.",
    },
]


# ──────────────────────────────────────────────────────────────
# Category -> criteria/rubric mapping
# ──────────────────────────────────────────────────────────────

CATEGORY_CONFIGS = {
    "conceptual_qa": {
        "name": "Conceptual Q&A Expertise",
        "criteria": CONCEPTUAL_QA_CRITERIA,
        "rubric": CONCEPTUAL_QA_RUBRIC,
    },
    "strategy_explanation": {
        "name": "Strategy Explanation",
        "criteria": STRATEGY_EXPLANATION_CRITERIA,
        "rubric": STRATEGY_EXPLANATION_RUBRIC,
    },
    "code_generation": {
        "name": "Code Quality",
        "criteria": CODE_QUALITY_CRITERIA,
        "rubric": CODE_QUALITY_RUBRIC,
    },
    "code_debugging": {
        "name": "Code Debugging Quality",
        "criteria": CODE_QUALITY_CRITERIA,
        "rubric": CODE_QUALITY_RUBRIC,
    },
    "data_interpretation": {
        "name": "Data Interpretation",
        "criteria": DATA_INTERPRETATION_CRITERIA,
        "rubric": DATA_INTERPRETATION_RUBRIC,
    },
    "multi_step_reasoning": {
        "name": "Multi-step Reasoning",
        "criteria": MULTI_STEP_REASONING_CRITERIA,
        "rubric": MULTI_STEP_REASONING_RUBRIC,
    },
}


# ──────────────────────────────────────────────────────────────
# Metric construction
# ──────────────────────────────────────────────────────────────


def create_quant_geval_metric(
    model: Optional[str] = None,
    category: Optional[str] = None,
) -> "GEval":
    """Create a DeepEval GEval metric for quant domain expertise.

    Design doc §5.0: Different Layer 1 categories use different rubrics.

    Args:
        model: LLM model for evaluation (default: deepeval default).
        category: Layer 1 category (conceptual_qa, strategy_explanation,
                  code_generation, code_debugging, data_interpretation,
                  multi_step_reasoning). Defaults to conceptual_qa.

    Returns:
        Configured GEval metric instance.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    config = CATEGORY_CONFIGS.get(
        category or "conceptual_qa", CATEGORY_CONFIGS["conceptual_qa"]
    )

    rubric = [
        Rubric(score_range=r["score_range"], expected_outcome=r["expected_outcome"])
        for r in config["rubric"]
    ]

    kwargs = {
        "name": config["name"],
        "criteria": config["criteria"],
        "evaluation_params": [
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        "rubric": rubric,
        "threshold": 0.5,
    }
    kwargs["model"] = resolve_deepeval_model(model)

    return GEval(**kwargs)
