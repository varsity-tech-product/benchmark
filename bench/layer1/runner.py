"""Layer 1 test runner.

Design doc §5.0: Layer 1 (~2000 single-turn Q&A items via LLMTestCase + GEval).
Evaluates single-turn quant knowledge using DeepEval GEval metric.
Can run with or without DeepEval (falls back to simple string matching).

Design doc §5.0 Layer 1 categories and eval methods:
- Conceptual Q&A:        GEval with domain expertise rubric
- Strategy Explanation:   GEval with strategy rubric
- Code Generation:        Automated execution + GEval code quality rubric
- Code Debugging:         Automated execution + GEval code quality rubric
- Data Interpretation:    GEval with data interpretation rubric
- Multi-step Reasoning:   GEval with multi-step rubric

Supports two modes:
1. Pure Q&A: agent_callback(question, context) -> str
2. Tool-enabled: agent_callback(question, context, tools, tool_callback) -> str
"""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from layer1.data_loader import (
    Layer1Item,
)

try:
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCase

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


class Layer1Result:
    """Result of evaluating a single Layer 1 item."""

    def __init__(
        self,
        item_id: str,
        category: str,
        difficulty: str,
        score: float,
        reason: str = "",
        actual_output: str = "",
        duration_ms: float = 0.0,
        code_execution_result: Optional[dict] = None,
    ):
        self.item_id = item_id
        self.category = category
        self.difficulty = difficulty
        self.score = score
        self.reason = reason
        self.actual_output = actual_output
        self.duration_ms = duration_ms
        self.code_execution_result = code_execution_result

    def to_dict(self) -> dict:
        d = {
            "item_id": self.item_id,
            "category": self.category,
            "difficulty": self.difficulty,
            "score": round(self.score, 4),
            "reason": self.reason,
            "actual_output": self.actual_output[:300],
            "duration_ms": round(self.duration_ms, 2),
        }
        if self.code_execution_result:
            d["code_execution"] = self.code_execution_result
        return d


class Layer1Runner:
    """Runs Layer 1 evaluations with category-specific rubrics.

    Design doc §5.0: Each Layer 1 category uses a different evaluation rubric.
    """

    def __init__(
        self,
        agent_callback=None,
        eval_model: Optional[str] = None,
        use_deepeval: bool = True,
        container_manager=None,
        use_docker: bool = False,
    ):
        """
        Args:
            agent_callback: Function(question, context, [tools, tool_callback]) -> str.
                If None, uses synthetic_response.
            eval_model: LLM model for GEval judge (string, e.g. "openai/gpt-4o").
            use_deepeval: Whether to use DeepEval GEval (vs simple scoring).
            container_manager: ContainerManager instance for sandbox execution.
            use_docker: Whether Docker execution is active.
        """
        self.agent_callback = agent_callback
        self.eval_model = eval_model
        self.use_deepeval = use_deepeval and DEEPEVAL_AVAILABLE
        self.container_manager = container_manager
        self.use_docker = use_docker
        self._geval_cache: dict[str, "GEval"] = {}

    def _get_geval_metric(self, category: str = "conceptual_qa"):
        """Get or create a category-specific GEval metric.

        Caches metrics per category to avoid re-creating them for each item.
        """
        if category not in self._geval_cache and self.use_deepeval:
            from evaluation.deepeval_metrics.quant_geval import (
                create_quant_geval_metric,
            )

            self._geval_cache[category] = create_quant_geval_metric(
                model=self.eval_model,
                category=category,
            )
        return self._geval_cache.get(category)

    def evaluate_item(self, item: Layer1Item) -> Layer1Result:
        """Evaluate a single Layer 1 item.

        For requires_tool items: creates a sandbox + MCP proxy with all core
        tools (no distractors), passes tools to agent_callback so the agent
        can internally loop (think → call tools → verify → respond).

        For all items: final answer is scored by GEval (or fallback).
        Code tasks additionally run _execute_code as a secondary signal.
        """
        start = time.time()
        container = None
        proxy = None

        try:
            # Set up sandbox + tools if needed
            if item.requires_tool and self.container_manager and self.agent_callback:
                container, proxy = self._setup_sandbox_and_proxy(item)

            # Get actual output from agent
            if self.agent_callback:
                context_str = item.context if item.context else ""
                if proxy:
                    actual_output = self.agent_callback(
                        context_str,
                        item.question,
                        tools=proxy.get_available_tools(),
                        tool_callback=proxy.call_tool,
                    )
                else:
                    actual_output = self.agent_callback(context_str, item.question)
            else:
                actual_output = item.synthetic_response

            # Evaluate based on category
            code_exec_result = None
            if item.category in (
                "code_generation",
                "code_debugging",
            ) and _contains_python_code(actual_output):
                # §5.0: Code tasks use automated execution + GEval code quality rubric
                code_exec_result = _execute_code(actual_output)
                exec_score = code_exec_result.get("score", 0.0)

                if self.use_deepeval:
                    geval_score, reason = self._evaluate_with_deepeval(
                        item, actual_output
                    )
                    # Combine: 60% execution correctness + 40% GEval code quality
                    score = 0.6 * exec_score + 0.4 * geval_score
                    reason = f"Code exec: {exec_score:.2f}, GEval: {geval_score:.2f}. {reason}"
                else:
                    score = exec_score
                    reason = code_exec_result.get("reason", "")
            elif self.use_deepeval:
                score, reason = self._evaluate_with_deepeval(item, actual_output)
            else:
                score, reason = self._evaluate_simple(item, actual_output)

            duration_ms = (time.time() - start) * 1000

            return Layer1Result(
                item_id=item.item_id,
                category=item.category,
                difficulty=item.difficulty,
                score=score,
                reason=reason,
                actual_output=actual_output[:500],
                duration_ms=duration_ms,
                code_execution_result=code_exec_result,
            )
        finally:
            # Teardown sandbox
            if container and self.container_manager:
                self.container_manager.destroy_container(container.container_id)

    def _setup_sandbox_and_proxy(self, item: Layer1Item):
        """Create a sandbox container and MCP proxy with all core tools.

        Layer 1 tool-enabled tasks get ALL core tools (no distractors),
        unlike Layer 2 which uses per-task tool subsets + random distractors.
        """
        from mcp_servers.core.tools import CORE_TOOLS
        from mcp_servers.registry import create_proxy_for_task

        # Create sandbox
        data_dir = os.environ.get(
            "QTB_DATA_DIR", str(Path(__file__).parent.parent / "data" / "frozen")
        )
        docs_dir = os.environ.get(
            "QTB_DOCS_DIR", str(Path(__file__).parent.parent / "docs")
        )

        container = self.container_manager.create_container(
            task_id=f"l1_{item.item_id}",
            data_dir=data_dir,
            docs_dir=docs_dir,
        )

        # Set environment vars for tool implementations
        os.environ["QTB_WORKSPACE_DIR"] = container.workspace_path

        # Create proxy with ALL core tools, no distractors
        proxy = create_proxy_for_task(
            core_tool_names=list(CORE_TOOLS.keys()),
            distractor_pool=[],
            num_distractors=0,
            container_manager=self.container_manager,
            container_id=container.container_id,
            workspace_path=container.workspace_path,
            use_docker=self.use_docker,
        )

        return container, proxy

    def _evaluate_with_deepeval(
        self, item: Layer1Item, actual_output: str
    ) -> tuple[float, str]:
        """Evaluate using category-specific DeepEval GEval."""
        metric = self._get_geval_metric(category=item.category)

        test_case = LLMTestCase(
            input=item.question,
            actual_output=actual_output,
            expected_output=item.reference_answer,
            context=[item.context] if item.context else None,
        )

        try:
            metric.measure(test_case)
            raw_score = metric.score
            # Normalize: GEval may return 0-10 depending on rubric configuration
            if raw_score > 1.0:
                raw_score = raw_score / 10.0
            return max(0.0, min(1.0, raw_score)), getattr(metric, "reason", "")
        except Exception as e:
            return 0.5, f"GEval error: {e}"

    def _evaluate_simple(
        self, item: Layer1Item, actual_output: str
    ) -> tuple[float, str]:
        """Simple keyword-based evaluation fallback."""
        if not actual_output:
            return 0.0, "No output generated"

        ref_words = set(item.reference_answer.lower().split())
        out_words = set(actual_output.lower().split())

        if not ref_words:
            return 0.5, "No reference answer for comparison"

        overlap = len(ref_words & out_words)
        coverage = overlap / len(ref_words) if ref_words else 0

        score = min(1.0, coverage * 1.5)
        if len(actual_output) > 200:
            score = min(1.0, score + 0.1)

        return round(score, 4), f"Keyword coverage: {coverage:.2%}"

    def run_batch(
        self,
        items: list[Layer1Item],
        max_items: Optional[int] = None,
    ) -> list[Layer1Result]:
        """Run evaluation on a batch of items."""
        if max_items:
            items = items[:max_items]

        results = []
        for i, item in enumerate(items):
            print(
                f"  [{i+1}/{len(items)}] Evaluating {item.item_id} ({item.category})..."
            )
            result = self.evaluate_item(item)
            results.append(result)
            print(f"    Score: {result.score:.4f}")

        return results

    @staticmethod
    def compute_layer1_scores(results: list[Layer1Result]) -> dict:
        """Compute aggregate scores from Layer 1 results."""
        if not results:
            return {"error": "No results"}

        scores = [r.score for r in results]
        by_category = {}
        by_difficulty = {}

        for r in results:
            by_category.setdefault(r.category, []).append(r.score)
            by_difficulty.setdefault(r.difficulty, []).append(r.score)

        import statistics

        return {
            "total_items": len(results),
            "mean_score": round(statistics.mean(scores), 4),
            "median_score": round(statistics.median(scores), 4),
            "min_score": round(min(scores), 4),
            "max_score": round(max(scores), 4),
            "by_category": {
                k: round(statistics.mean(v), 4) for k, v in by_category.items()
            },
            "by_difficulty": {
                k: round(statistics.mean(v), 4) for k, v in by_difficulty.items()
            },
        }

    def save_results(self, results: list[Layer1Result], output_path: str | Path):
        """Save Layer 1 results to JSON with full KPI structure."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        summary = self.compute_layer1_scores(results)

        data = {
            "layer": 1,
            "eval_mode": "deepeval_geval" if self.use_deepeval else "keyword_fallback",
            "summary": summary,
            "kpis": {
                "mean_geval_score": summary.get("mean_score", 0),
                "by_category": summary.get("by_category", {}),
                "by_difficulty": summary.get("by_difficulty", {}),
            },
            "results": [r.to_dict() for r in results],
        }

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

        return str(output_path)


# ──────────────────────────────────────────────────────────────
# Code execution helpers (§5.0: Code Generation/Debugging tasks)
# ──────────────────────────────────────────────────────────────


def _contains_python_code(text: str) -> bool:
    """Check if the text contains Python code blocks."""
    return (
        "```python" in text
        or "```py" in text
        or ("import " in text and ("def " in text or "print(" in text))
    )


def _extract_python_code(text: str) -> str:
    """Extract Python code from markdown code blocks or raw code."""
    blocks = []
    in_block = False
    current_block = []

    for line in text.split("\n"):
        if line.strip().startswith("```python") or line.strip().startswith("```py"):
            in_block = True
            current_block = []
        elif line.strip() == "```" and in_block:
            in_block = False
            blocks.append("\n".join(current_block))
        elif in_block:
            current_block.append(line)

    if blocks:
        return "\n\n".join(blocks)

    # Fallback: try to find raw Python code
    code_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and (
            stripped.startswith("import ")
            or stripped.startswith("from ")
            or stripped.startswith("def ")
            or stripped.startswith("class ")
            or stripped.startswith("print(")
            or stripped.startswith("#")
            or (code_lines and stripped)
        ):
            code_lines.append(line)
        elif code_lines and not stripped:
            code_lines.append(line)
        elif code_lines:
            break

    return "\n".join(code_lines) if len(code_lines) >= 3 else ""


def _execute_code(output: str) -> dict:
    """Execute Python code extracted from agent output.

    Design doc §5.0: Code Generation/Debugging tasks use automated execution.

    Returns:
        Dict with score, reason, success, and optional stdout/stderr.
    """
    code = _extract_python_code(output)
    if not code:
        return {
            "score": 0.0,
            "reason": "No executable Python code found",
            "success": False,
        }

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            result = subprocess.run(
                ["python", f.name],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=tempfile.gettempdir(),
            )

        success = result.returncode == 0
        stdout = result.stdout[:1000]
        stderr = result.stderr[:500]

        if success:
            if stdout.strip():
                return {
                    "score": 0.8,
                    "reason": f"Code executed successfully with output ({len(stdout)} chars)",
                    "success": True,
                    "stdout": stdout,
                }
            else:
                return {
                    "score": 0.6,
                    "reason": "Code executed successfully but produced no output",
                    "success": True,
                }
        else:
            return {
                "score": 0.2,
                "reason": f"Code execution failed: {stderr[:200]}",
                "success": False,
                "stderr": stderr,
            }

    except subprocess.TimeoutExpired:
        return {
            "score": 0.1,
            "reason": "Code execution timed out (30s)",
            "success": False,
        }
    except Exception as e:
        return {"score": 0.1, "reason": f"Code execution error: {e}", "success": False}
