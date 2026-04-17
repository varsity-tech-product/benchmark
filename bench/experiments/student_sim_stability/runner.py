"""Main experiment runner for student simulator stability testing.

Orchestrates Phase 1 (scripted tutor), Phase 2 (live tutor), and
control group experiments. Calls evaluator for D1-D4 scoring.
"""

import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# Ensure bench root is on sys.path
BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.config import (
    EXPERIMENT_TASKS,
    FIXED_TURNS,
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
    OUTPUT_DIR,
    REPEATS,
    STUDENT_MODELS,
    TASK_PERSONA_MAP,
    TEMPERATURE,
    TUTOR_MODEL,
    TUTOR_TEMPERATURE,
    TrialKey,
)
from experiments.student_sim_stability.evaluator import StabilityEvaluator
from experiments.student_sim_stability.scripted_tutor import (
    load_or_generate_scripted_plans,
)
from server.config.llm_config import OPENROUTER_BASE_URL
from server.config.pricing import _resolve_pricing
from server.config.prompt_config import build_scenario, build_user_description
from server.core.student_sim import StudentSimulator
from server.eval.ewan_eval.llm_client import EwanLLMClient
from server.schemas import QuantTutorTask, StudentPersona

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Live tutor prompt
# ---------------------------------------------------------------------------

_LIVE_TUTOR_PROMPT = """\
You are a quantitative finance tutor. A student is asking for help.

Task context: {task_description}

The student has just said:
"{student_message}"

Conversation so far:
{transcript}

Respond as a helpful, patient tutor. Adapt your explanation level to what \
the student seems to know. Include concrete examples, code snippets, or \
numerical results where appropriate. Keep your response focused and \
3-8 sentences long.

Your response:
"""

# No-persona prompt for control group
_CONTROL_USER_DESCRIPTION = (
    "You are a student learning about quantitative finance and programming. "
    "You are curious and want to understand the topics being discussed. "
    "Respond naturally in 2-4 sentences."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(model: str, temperature: float = 0.0) -> EwanLLMClient:
    """Create an EwanLLMClient for the given model."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    or_model = model if "/" in model else f"openai/{model}"
    pricing = _resolve_pricing(or_model) or (0.0, 0.0)
    return EwanLLMClient(
        model=or_model,
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
        cost_per_input_token=pricing[0],
        cost_per_output_token=pricing[1],
    )


def _load_task(task_cfg: dict) -> QuantTutorTask:
    """Load a task JSON file."""
    path = BENCH_ROOT / "tasks" / "layer2" / task_cfg["path"]
    with open(path) as f:
        return QuantTutorTask(**json.load(f))


def _load_persona(persona_id: str) -> StudentPersona:
    """Load a persona JSON file."""
    path = BENCH_ROOT / "personas" / f"{persona_id}.json"
    with open(path) as f:
        return StudentPersona(**json.load(f))


def _generate_live_tutor_response(
    tutor_client: EwanLLMClient,
    task_description: str,
    conversation: list[dict],
) -> str:
    """Generate a tutor response using the live model."""
    student_msg = conversation[-1]["content"] if conversation else ""
    transcript = json.dumps(conversation[-6:], indent=2, ensure_ascii=False)

    prompt = _LIVE_TUTOR_PROMPT.format(
        task_description=task_description,
        student_message=student_msg[:500],
        transcript=transcript,
    )
    result = tutor_client.generate(prompt)
    text = result[0] if isinstance(result, tuple) else result
    return text.strip()


# ---------------------------------------------------------------------------
# Single trial runner
# ---------------------------------------------------------------------------


def run_single_trial(
    phase: str,
    task: QuantTutorTask,
    persona: StudentPersona,
    student_model: str,
    scripted_tutor_messages: list[str] | None,
    tutor_client: EwanLLMClient | None,
    n_turns: int = FIXED_TURNS,
    use_persona: bool = True,
) -> list[dict]:
    """Run a single conversation trial.

    Args:
        phase: "scripted", "live", or "control"
        task: Task definition
        persona: Student persona (used for sim prompt even in control)
        student_model: Model name for student simulator
        scripted_tutor_messages: Pre-generated tutor messages (Phase 1 only)
        tutor_client: Live tutor LLM client (Phase 2 only)
        n_turns: Number of conversation turns
        use_persona: If False, use generic description (control group)

    Returns:
        Conversation as list of {role, content} dicts
    """
    # Build student simulator
    student_client = _make_client(student_model, temperature=TEMPERATURE)
    persona_id = persona.persona_id

    if use_persona:
        user_desc = build_user_description(persona)
        scenario = build_scenario(task, persona_id)
    else:
        user_desc = _CONTROL_USER_DESCRIPTION
        scenario = f"Scenario: {task.description}"

    sim = StudentSimulator(
        scenario=scenario,
        user_description=user_desc,
        model=student_client,
    )

    # Get student opening
    opening = (task.student_openings or {}).get(
        persona_id, "Hi, I need help with this topic."
    )
    conversation: list[dict] = [{"role": "user", "content": opening}]

    for turn_idx in range(n_turns):
        # Generate tutor response
        if phase == "scripted" and scripted_tutor_messages:
            if turn_idx < len(scripted_tutor_messages):
                tutor_msg = scripted_tutor_messages[turn_idx]
            else:
                tutor_msg = "Let me know if you have any other questions."
        elif tutor_client is not None:
            tutor_msg = _generate_live_tutor_response(
                tutor_client, task.description, conversation
            )
        else:
            raise ValueError(f"No tutor source for phase={phase}")

        conversation.append({"role": "assistant", "content": tutor_msg})

        # Generate student response (except after last tutor turn)
        if turn_idx < n_turns - 1:
            try:
                student_msg = sim.generate_message(conversation)
            except Exception as exc:
                logger.warning("Student sim failed at turn %d: %s", turn_idx, exc)
                student_msg = "Could you explain that in a bit more detail?"
            conversation.append({"role": "user", "content": student_msg})

    return conversation


# ---------------------------------------------------------------------------
# Full experiment orchestrator
# ---------------------------------------------------------------------------


class ExperimentRunner:
    """Runs the full stability experiment."""

    def __init__(self, output_dir: str | None = None):
        self.output_dir = Path(output_dir or OUTPUT_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_dir = self.output_dir / "conversations"
        self.conversations_dir.mkdir(exist_ok=True)
        self.eval_dir = self.output_dir / "evaluations"
        self.eval_dir.mkdir(exist_ok=True)

        # Load tasks and personas
        self.tasks: dict[str, QuantTutorTask] = {}
        self.personas: dict[str, StudentPersona] = {}
        self._load_data()

        # Clients (lazy init)
        self._tutor_client: EwanLLMClient | None = None
        self._judge_client: EwanLLMClient | None = None
        self._scripted_gen_client: EwanLLMClient | None = None

    def _load_data(self):
        """Load all task and persona definitions."""
        loaded_personas = set()
        for task_cfg in EXPERIMENT_TASKS:
            task = _load_task(task_cfg)
            self.tasks[task.task_id] = task
            for pid in TASK_PERSONA_MAP.get(task.task_id, []):
                if pid not in loaded_personas:
                    self.personas[pid] = _load_persona(pid)
                    loaded_personas.add(pid)

    @property
    def tutor_client(self) -> EwanLLMClient:
        if self._tutor_client is None:
            self._tutor_client = _make_client(TUTOR_MODEL, TUTOR_TEMPERATURE)
        return self._tutor_client

    @property
    def judge_client(self) -> EwanLLMClient:
        if self._judge_client is None:
            self._judge_client = _make_client(JUDGE_MODEL, JUDGE_TEMPERATURE)
        return self._judge_client

    @property
    def scripted_gen_client(self) -> EwanLLMClient:
        if self._scripted_gen_client is None:
            self._scripted_gen_client = _make_client(TUTOR_MODEL, 0.3)
        return self._scripted_gen_client

    def _save_conversation(self, key: str, conversation: list[dict]):
        path = self.conversations_dir / f"{key}.json"
        with open(path, "w") as f:
            json.dump(conversation, f, indent=2, ensure_ascii=False)

    def _load_conversation(self, key: str) -> list[dict] | None:
        path = self.conversations_dir / f"{key}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return None

    # ----- Phase 1: Scripted tutor -----

    def run_phase1(self):
        """Run Phase 1: scripted tutor for reproducibility testing."""
        logger.info("=== Phase 1: Scripted Tutor ===")

        # Generate scripted tutor plans
        scripted_plans = load_or_generate_scripted_plans(
            tasks=list(self.tasks.values()),
            personas=self.personas,
            task_persona_map=TASK_PERSONA_MAP,
            model_client=self.scripted_gen_client,
            cache_dir=str(self.output_dir),
            n_turns=FIXED_TURNS,
        )

        for task_id, task in self.tasks.items():
            persona_ids = TASK_PERSONA_MAP.get(task_id, [])
            for pid in persona_ids:
                plan_key = f"{task_id}__{pid}"
                tutor_msgs = scripted_plans.get(plan_key)
                if not tutor_msgs:
                    logger.warning("No scripted plan for %s, skipping", plan_key)
                    continue

                for model in STUDENT_MODELS:
                    for repeat in range(REPEATS):
                        trial = TrialKey("scripted", task_id, pid, model, repeat)
                        # Skip if already run
                        if self._load_conversation(trial.key):
                            logger.info("Skipping (cached): %s", trial.key)
                            continue

                        logger.info("Running: %s", trial.key)
                        conv = run_single_trial(
                            phase="scripted",
                            task=task,
                            persona=self.personas[pid],
                            student_model=model,
                            scripted_tutor_messages=tutor_msgs,
                            tutor_client=None,
                        )
                        self._save_conversation(trial.key, conv)

    # ----- Phase 2: Live tutor -----

    def run_phase2(self):
        """Run Phase 2: live tutor for real-scenario testing."""
        logger.info("=== Phase 2: Live Tutor ===")

        for task_id, task in self.tasks.items():
            persona_ids = TASK_PERSONA_MAP.get(task_id, [])
            for pid in persona_ids:
                for model in STUDENT_MODELS:
                    for repeat in range(REPEATS):
                        trial = TrialKey("live", task_id, pid, model, repeat)
                        if self._load_conversation(trial.key):
                            logger.info("Skipping (cached): %s", trial.key)
                            continue

                        logger.info("Running: %s", trial.key)
                        conv = run_single_trial(
                            phase="live",
                            task=task,
                            persona=self.personas[pid],
                            student_model=model,
                            scripted_tutor_messages=None,
                            tutor_client=self.tutor_client,
                        )
                        self._save_conversation(trial.key, conv)

    # ----- Control group -----

    def run_control(self):
        """Run control group: no persona description."""
        logger.info("=== Control Group ===")

        # Use scripted tutor plans (first persona available) for consistency
        scripted_plans = load_or_generate_scripted_plans(
            tasks=list(self.tasks.values()),
            personas=self.personas,
            task_persona_map=TASK_PERSONA_MAP,
            model_client=self.scripted_gen_client,
            cache_dir=str(self.output_dir),
            n_turns=FIXED_TURNS,
        )

        for task_id, task in self.tasks.items():
            persona_ids = TASK_PERSONA_MAP.get(task_id, [])
            if not persona_ids:
                continue
            pid = persona_ids[0]  # Use first persona's tutor plan
            plan_key = f"{task_id}__{pid}"
            tutor_msgs = scripted_plans.get(plan_key)
            if not tutor_msgs:
                continue

            for model in STUDENT_MODELS:
                trial = TrialKey("control", task_id, pid, model, 0)
                if self._load_conversation(trial.key):
                    logger.info("Skipping (cached): %s", trial.key)
                    continue

                logger.info("Running: %s", trial.key)
                conv = run_single_trial(
                    phase="scripted",
                    task=task,
                    persona=self.personas[pid],
                    student_model=model,
                    scripted_tutor_messages=tutor_msgs,
                    tutor_client=None,
                    use_persona=False,
                )
                self._save_conversation(trial.key, conv)

    # ----- Evaluation -----

    def run_evaluation(self):
        """Run D1-D4 evaluation on all collected conversations."""
        logger.info("=== Running Evaluation ===")
        evaluator = StabilityEvaluator(self.judge_client)
        all_results: dict[str, list] = defaultdict(list)

        for phase in ("scripted", "live"):
            for task_id, task in self.tasks.items():
                persona_ids = TASK_PERSONA_MAP.get(task_id, [])
                for pid in persona_ids:
                    persona = self.personas[pid]

                    # Collect conversations for this (task, persona) group
                    model_runs: dict[str, list[list[dict]]] = defaultdict(list)
                    for model in STUDENT_MODELS:
                        for repeat in range(REPEATS):
                            trial = TrialKey(phase, task_id, pid, model, repeat)
                            conv = self._load_conversation(trial.key)
                            if conv:
                                model_runs[model].append(conv)

                    # D1: Per-message persona adherence (sample: first run per model)
                    for model, runs in model_runs.items():
                        if runs:
                            d1_results = evaluator.eval_d1_conversation(
                                runs[0], persona
                            )
                            for r in d1_results:
                                r.metadata.update(
                                    {
                                        "phase": phase,
                                        "task_id": task_id,
                                        "persona_id": pid,
                                        "model": model,
                                    }
                                )
                            all_results["D1"].extend(d1_results)

                    # D2: Cross-run reproducibility (per model, needs 3 runs)
                    for model, runs in model_runs.items():
                        if len(runs) >= 2:
                            d2 = evaluator.eval_d2(runs, persona, task)
                            d2.metadata.update(
                                {
                                    "phase": phase,
                                    "task_id": task_id,
                                    "persona_id": pid,
                                    "model": model,
                                    "n_runs": len(runs),
                                }
                            )
                            all_results["D2"].append(d2)

                    # D3: Cross-model consistency (one conv per model)
                    model_convs = {m: runs[0] for m, runs in model_runs.items() if runs}
                    if len(model_convs) >= 2:
                        d3 = evaluator.eval_d3(model_convs, persona, task)
                        d3.metadata.update(
                            {
                                "phase": phase,
                                "task_id": task_id,
                                "persona_id": pid,
                            }
                        )
                        all_results["D3"].append(d3)

                    # D4: Drift detection (all conversations)
                    for model, runs in model_runs.items():
                        for i, conv in enumerate(runs):
                            d4 = evaluator.eval_d4(conv, persona)
                            d4.metadata.update(
                                {
                                    "phase": phase,
                                    "task_id": task_id,
                                    "persona_id": pid,
                                    "model": model,
                                    "repeat": i,
                                }
                            )
                            all_results["D4"].append(d4)

        # Control group evaluation
        for task_id, task in self.tasks.items():
            persona_ids = TASK_PERSONA_MAP.get(task_id, [])
            if not persona_ids:
                continue
            pid = persona_ids[0]
            persona = self.personas[pid]

            for model in STUDENT_MODELS:
                control_trial = TrialKey("control", task_id, pid, model, 0)
                control_conv = self._load_conversation(control_trial.key)

                # Compare with scripted phase first run
                persona_trial = TrialKey("scripted", task_id, pid, model, 0)
                persona_conv = self._load_conversation(persona_trial.key)

                if control_conv and persona_conv:
                    ctrl = evaluator.eval_control(persona_conv, control_conv, persona)
                    ctrl.metadata.update(
                        {
                            "task_id": task_id,
                            "persona_id": pid,
                            "model": model,
                        }
                    )
                    all_results["control"].append(ctrl)

        # Save evaluation results
        serializable = {}
        for dim, results in all_results.items():
            serializable[dim] = [
                {
                    "dimension": r.dimension,
                    "scores": r.scores,
                    "reasoning": r.reasoning,
                    "metadata": r.metadata,
                }
                for r in results
            ]

        eval_path = self.eval_dir / "all_evaluations.json"
        with open(eval_path, "w") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)

        logger.info(
            "Evaluation complete. Judge calls: %d, cost: $%.4f",
            evaluator.total_calls,
            evaluator.total_cost,
        )
        logger.info("Results saved to %s", eval_path)
        return serializable

    # ----- Run all -----

    def run_all(self):
        """Run complete experiment: Phase 1 + Phase 2 + Control + Evaluation."""
        start = time.time()
        self.run_phase1()
        self.run_phase2()
        self.run_control()
        results = self.run_evaluation()
        elapsed = time.time() - start
        logger.info("Total experiment time: %.1f minutes", elapsed / 60)
        return results
