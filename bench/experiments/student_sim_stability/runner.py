"""Experiment runner for student simulator stability testing.

Single-phase design with parallel execution:
  - Tutor: gpt-4.1-nano via OpenRouter (live, context-aware)
  - Student: 3 models under test via OpenRouter
  - Judge: Claude Code post-hoc (not in this module)
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure bench root is on sys.path
BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.config import (
    EXPERIMENT_TASKS,
    FIXED_TURNS,
    MAX_WORKERS,
    OUTPUT_DIR,
    REPEATS,
    STUDENT_MODELS,
    TASK_PERSONA_MAP,
    TEMPERATURE,
    TUTOR_MODEL,
    TUTOR_TEMPERATURES,
    TrialKey,
)
from server.config.llm_config import OPENROUTER_BASE_URL
from server.config.pricing import _resolve_pricing
from server.config.prompt_config import build_scenario, build_user_description
from server.core.student_sim import StudentSimulator
from server.eval.judges.runtime.llm_client import EwanLLMClient
from server.schemas import QuantTutorTask, StudentPersona

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tutor prompt
# ---------------------------------------------------------------------------

_TUTOR_PROMPT = """\
You are a quantitative finance tutor. Be helpful, patient, and concrete. \
Adapt your explanation level to what the student seems to know. \
Include code snippets or numerical examples where appropriate. \
Keep responses to 3-8 sentences.

Task context: {task_description}

Conversation so far:
{transcript}

The student just said:
"{student_message}"

Your response as tutor:"""

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
    path = BENCH_ROOT / "tasks" / "layer2" / task_cfg["path"]
    with open(path) as f:
        return QuantTutorTask(**json.load(f))


def _load_persona(persona_id: str) -> StudentPersona:
    path = BENCH_ROOT / "personas" / f"{persona_id}.json"
    with open(path) as f:
        return StudentPersona(**json.load(f))


def _generate_tutor_response(
    tutor_client: EwanLLMClient,
    task_description: str,
    conversation: list[dict],
) -> str:
    student_msg = conversation[-1]["content"] if conversation else ""
    # Keep last 6 turns for context
    recent = conversation[-6:]
    transcript = json.dumps(recent, indent=2, ensure_ascii=False)

    prompt = _TUTOR_PROMPT.format(
        task_description=task_description[:500],
        transcript=transcript,
        student_message=student_msg[:500],
    )
    result = tutor_client.generate(prompt)
    text = result[0] if isinstance(result, tuple) else result
    return (text or "").strip()


# ---------------------------------------------------------------------------
# Single trial
# ---------------------------------------------------------------------------


def run_single_trial(
    task: QuantTutorTask,
    persona: StudentPersona,
    student_model: str,
    tutor_client: EwanLLMClient,
    n_turns: int = FIXED_TURNS,
    use_persona: bool = True,
) -> list[dict]:
    """Run one conversation trial. Returns conversation list."""
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

    opening = (task.student_openings or {}).get(
        persona_id, "Hi, I need help with this topic."
    )
    conversation: list[dict] = [{"role": "user", "content": opening}]

    for turn_idx in range(n_turns):
        # Tutor responds
        tutor_msg = _generate_tutor_response(
            tutor_client, task.description, conversation
        )
        conversation.append({"role": "assistant", "content": tutor_msg})

        # Student responds (except after last tutor turn)
        if turn_idx < n_turns - 1:
            student_msg = sim.generate_message(conversation)
            conversation.append({"role": "user", "content": student_msg})

    return conversation


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class ExperimentRunner:
    """Runs the full experiment with parallel execution."""

    def __init__(self, output_dir: str | None = None):
        out = Path(output_dir or OUTPUT_DIR)
        if not out.is_absolute():
            out = BENCH_ROOT / out
        self.output_dir = out
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_dir = self.output_dir / "conversations"
        self.conversations_dir.mkdir(exist_ok=True)
        self.eval_dir = self.output_dir / "evaluations"
        self.eval_dir.mkdir(exist_ok=True)

        self.tasks: dict[str, QuantTutorTask] = {}
        self.personas: dict[str, StudentPersona] = {}
        self._load_data()

    def _load_data(self):
        loaded_personas: set[str] = set()
        for task_cfg in EXPERIMENT_TASKS:
            task = _load_task(task_cfg)
            self.tasks[task.task_id] = task
            for pid in TASK_PERSONA_MAP.get(task.task_id, []):
                if pid not in loaded_personas:
                    self.personas[pid] = _load_persona(pid)
                    loaded_personas.add(pid)

    def _conv_path(self, key: str) -> Path:
        return self.conversations_dir / f"{key}.json"

    def _save_conversation(self, key: str, conversation: list[dict]):
        with open(self._conv_path(key), "w") as f:
            json.dump(conversation, f, indent=2, ensure_ascii=False)

    def _exists(self, key: str) -> bool:
        return self._conv_path(key).exists()

    def _run_trial_wrapped(self, trial: TrialKey, use_persona: bool = True) -> str:
        """Run a single trial (wrapper for parallel execution). Returns trial key."""
        if self._exists(trial.key):
            return f"SKIP {trial.key}"

        task = self.tasks[trial.task_id]
        persona = self.personas[trial.persona_id]
        tutor_client = _make_client(TUTOR_MODEL, trial.tutor_temperature)

        try:
            conv = run_single_trial(
                task=task,
                persona=persona,
                student_model=trial.student_model,
                tutor_client=tutor_client,
                use_persona=use_persona,
            )
            self._save_conversation(trial.key, conv)
            return f"OK   {trial.key}"
        except Exception as exc:
            # Log error detail; StudentSimError carries attempt history
            from server.core.student_sim import StudentSimError

            if isinstance(exc, StudentSimError):
                logger.error(
                    "Trial %s aborted (%s, %d attempts): %s",
                    trial.key,
                    exc.error_type,
                    len(exc.attempts),
                    exc,
                )
            else:
                logger.error("Trial %s failed: %s", trial.key, exc)
            return f"FAIL {trial.key}: {exc}"

    def run_generate(self, max_workers: int = MAX_WORKERS, limit: int | None = None):
        """Generate conversations in parallel.

        Args:
            max_workers: Thread pool size.
            limit: If set, run at most this many pending trials then stop.
        """
        trials: list[tuple[TrialKey, bool]] = []

        # Main experiment — all tutor temperatures
        for task_id in self.tasks:
            for pid in TASK_PERSONA_MAP.get(task_id, []):
                for model in STUDENT_MODELS:
                    for repeat in range(REPEATS):
                        for tutor_t in TUTOR_TEMPERATURES:
                            t = TrialKey("live", task_id, pid, model, repeat, tutor_t)
                            trials.append((t, True))

        # Control group — tutor t=0 only
        for task_id in self.tasks:
            persona_ids = TASK_PERSONA_MAP.get(task_id, [])
            if not persona_ids:
                continue
            pid = persona_ids[0]
            for model in STUDENT_MODELS:
                t = TrialKey("control", task_id, pid, model, 0, tutor_temperature=0.0)
                trials.append((t, False))

        # Filter out already completed, apply limit
        pending = [(t, p) for t, p in trials if not self._exists(t.key)]
        if limit is not None and limit < len(pending):
            pending = pending[:limit]
        total = len(trials)
        done = total - len(pending)

        logger.info(
            "Total trials: %d, already done: %d, pending: %d",
            total,
            done,
            len(pending),
        )

        if not pending:
            logger.info("All trials already completed.")
            return

        start = time.time()
        completed = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._run_trial_wrapped, t, p): t for t, p in pending
            }
            for future in as_completed(futures):
                result = future.result()
                completed += 1
                if result.startswith("FAIL"):
                    failed += 1
                # Progress update every 10 trials
                if completed % 10 == 0 or completed == len(pending):
                    elapsed = time.time() - start
                    logger.info(
                        "Progress: %d/%d (%.0fs elapsed, %d failed)",
                        completed,
                        len(pending),
                        elapsed,
                        failed,
                    )

        elapsed = time.time() - start
        logger.info(
            "Done. %d trials in %.1f min (%d failed)",
            completed,
            elapsed / 60,
            failed,
        )
