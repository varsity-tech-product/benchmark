"""Render judge-qualification judge inputs from the fixed corpus."""

from __future__ import annotations

import argparse
import functools
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from experiments.student_sim_stability.core.config import (
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
)
from experiments.student_sim_stability.core.contracts import (
    list_persona_contracts,
    load_persona_contract,
)
from experiments.student_sim_stability.core.contracts import (
    render_persona_contract_text as _core_render_persona_contract_text,
)
from experiments.student_sim_stability.core.io_utils import atomic_write_json
from experiments.student_sim_stability.core.paths import (
    BENCH_ROOT,
    RESOURCE_ROOT,
    resolve_results_dir,
)
from experiments.student_sim_stability.core.rubrics import (
    rubric_metadata,
    rubric_prompt_template,
)
from experiments.student_sim_stability.pipeline._render_common import (
    persona_block_kwargs as _persona_block_kwargs,
)
from experiments.student_sim_stability.pipeline._render_common import (
    truncate as _truncate,
)

DEFAULT_CORPUS = (
    RESOURCE_ROOT / "judge_qualification" / "judge_qualification_corpus.json"
)
DEFAULT_GATE_DIRNAME = "judge_qualification"
DEFAULT_GATE_RESULTS_DIR = (
    BENCH_ROOT
    / "experiments"
    / "student_sim_stability"
    / "results"
    / "judge_qualification"
)
STALE_REPORT_ARTIFACTS = {
    "llm_cost_estimate.json",
    "judge_qualification_report.html",
    "judge_qualification_report.md",
    "judge_qualification_stats.json",
    "judge_run_stats.json",
}
SUPPORTED_PROMPT_VARIANTS = {
    "baseline",
    "role_blocks",
    "markdown_transcript",
}
VARIANT_DIMENSIONS = {"D3", "B1", "control"}


def load_corpus(path: Path | None = None) -> dict[str, Any]:
    corpus_path = path or DEFAULT_CORPUS
    with open(corpus_path, encoding="utf-8") as fh:
        corpus = json.load(fh)
    _validate_corpus(corpus)
    return corpus


def _validate_corpus(corpus: dict[str, Any]) -> None:
    sample_ids = [str(item.get("sample_id", "")) for item in corpus.get("items", [])]
    duplicates = sorted(
        {sample_id for sample_id in sample_ids if sample_ids.count(sample_id) > 1}
    )
    if duplicates:
        raise ValueError(f"Persona-gate corpus has duplicate sample IDs: {duplicates}")
    known_ids = set(sample_ids)
    for case in corpus.get("sensitivity_cases", []):
        for field in ["baseline_sample_id", "perturbed_sample_id"]:
            if case.get(field) not in known_ids:
                raise ValueError(
                    f"Sensitivity case {case.get('case_id')} references "
                    f"unknown {field}: {case.get(field)}"
                )


def _render_persona_contract_text(persona_id: str) -> str:
    """Thin alias to the core helper so gate and main experiment emit identical
    candidate-contract text (same expanded emotional profile, same field order)."""
    return _core_render_persona_contract_text(persona_id)


@functools.lru_cache(maxsize=1)
def _b1_candidate_contracts_block() -> str:
    return "\n\n".join(
        _render_persona_contract_text(contract["persona_id"])
        for contract in list_persona_contracts()
    )


def _split_csv(value: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value]
    else:
        parts = [part.strip() for part in str(value).split(",")]
    return [part for part in parts if part]


def parse_prompt_variants(value: str | list[str] | tuple[str, ...]) -> list[str]:
    variants = _split_csv(value)
    unknown = sorted(set(variants) - SUPPORTED_PROMPT_VARIANTS)
    if unknown:
        known = ", ".join(sorted(SUPPORTED_PROMPT_VARIANTS))
        raise ValueError(f"Unsupported prompt variants {unknown}; choose from {known}")
    return variants or ["baseline"]


def _variants_for_item(item: dict[str, Any], requested: list[str]) -> list[str]:
    if item.get("dimension") not in VARIANT_DIMENSIONS:
        return ["baseline"]
    return requested


def _rubric_prompt(dimension: str, prompt: str) -> tuple[dict[str, Any], str]:
    metadata = rubric_metadata(dimension)
    header = (
        "## Rubric Metadata\n"
        f"rubric_id: {metadata['rubric_id']}\n"
        f"rubric_version: {metadata['rubric_version']}\n\n"
    )
    return metadata, header + prompt


def _format_turns(turns: list[str], *, variant: str) -> str:
    if variant == "role_blocks":
        return "\n\n".join(
            f"Turn {idx + 1} - Student\n{_truncate(text)}"
            for idx, text in enumerate(turns)
        )
    if variant == "markdown_transcript":
        return "\n\n".join(
            f"### Student Turn {idx + 1}\n\n{_truncate(text)}"
            for idx, text in enumerate(turns)
        )
    return "\n".join(
        f'  Student turn {idx + 1}: "{_truncate(text)}"'
        for idx, text in enumerate(turns)
    )


def _format_d3_context(item: dict[str, Any], *, variant: str) -> tuple[str, int]:
    rows = item.get("conversation_context") or []
    scored = [
        row
        for row in rows
        if str(row.get("label", "")).startswith("Scored student turn")
    ]
    if variant == "role_blocks":
        text = "\n\n".join(
            f"{row.get('label', '')}\n{_truncate(str(row.get('content', '')))}"
            for row in rows
        )
    elif variant == "markdown_transcript":
        text = "\n\n".join(
            f"### {row.get('label', '')}\n\n{_truncate(str(row.get('content', '')))}"
            for row in rows
        )
    else:
        text = "\n".join(
            f"{row.get('label', '')}: "
            f"{json.dumps(_truncate(str(row.get('content', ''))), ensure_ascii=False)}"
            for row in rows
        )
    return text, len(scored)


def _render_p1(item: dict[str, Any], *, variant: str) -> str:
    del variant
    expected_signals = item.get("expected_signals") or []
    expected_signals_text = (
        "\n".join(f"- {signal}" for signal in expected_signals)
        if expected_signals
        else "- (no persona-specific indirect signals authored)"
    )
    prompt = rubric_prompt_template("P1").format(
        persona_contract=_render_persona_contract_text(item["persona_id"]),
        probe_facet=item.get("facet", ""),
        probe_message=_truncate(str(item.get("probe_message", ""))),
        student_response=_truncate(str(item.get("student_response", ""))),
        expected_signals=expected_signals_text,
    )
    _, prompt = _rubric_prompt("P1", prompt)
    return prompt


def _render_d3(item: dict[str, Any], *, variant: str) -> str:
    context, total_turns = _format_d3_context(item, variant=variant)
    prompt = rubric_prompt_template("D3").format(
        **_persona_block_kwargs(item["persona_id"]),
        student_messages_text=context,
        total_turns=total_turns,
    )
    _, prompt = _rubric_prompt("D3", prompt)
    return prompt


def _render_b1(item: dict[str, Any], *, variant: str) -> str:
    transcript = _format_turns(item.get("student_turns") or [], variant=variant)
    prompt = rubric_prompt_template("B1").format(
        candidate_contracts=_b1_candidate_contracts_block(),
        context_label=item.get("context_label", ""),
        transcript=transcript,
    )
    _, prompt = _rubric_prompt("B1", prompt)
    return prompt


def _render_control(item: dict[str, Any], *, variant: str) -> str:
    set_a = _format_turns(item.get("set_a_turns") or [], variant=variant)
    set_b = _format_turns(item.get("set_b_turns") or [], variant=variant)
    prompt = rubric_prompt_template("control").format(
        **_persona_block_kwargs(item["persona_id"]),
        set_description=(
            "One set was produced with a detailed persona definition. "
            "The other used a generic student description. "
            "You do not know which set is which."
        ),
        set_a_conversation=set_a,
        set_b_conversation=set_b,
    )
    _, prompt = _rubric_prompt("control", prompt)
    return prompt


def _render_prompt(item: dict[str, Any], *, variant: str) -> str:
    dimension = item["dimension"]
    if dimension == "P1":
        return _render_p1(item, variant=variant)
    if dimension == "D3":
        return _render_d3(item, variant=variant)
    if dimension == "B1":
        return _render_b1(item, variant=variant)
    if dimension == "control":
        return _render_control(item, variant=variant)
    raise ValueError(f"Unsupported judge-qualification dimension: {dimension}")


def _contract_metadata(persona_id: str) -> dict[str, str]:
    contract = load_persona_contract(persona_id)
    return {
        "persona_contract_id": contract["persona_id"],
        "persona_contract_version": contract["contract_version"],
    }


def render_judge_qualification_inputs(
    *,
    gate_dir: Path | None = None,
    results_dir: Path | None = None,
    corpus_path: Path | None = None,
    repeats: int | None = None,
    prompt_variants: list[str] | None = None,
    clean: bool = False,
) -> dict[str, Any]:
    corpus = load_corpus(corpus_path)
    requested_variants = (
        prompt_variants or corpus.get("default_prompt_variants") or ["baseline"]
    )
    variants = parse_prompt_variants(requested_variants)
    repeat_count = int(repeats or corpus.get("default_repeats") or 3)
    gate_dir = _coerce_gate_dir(gate_dir=gate_dir, results_dir=results_dir)
    input_dir = gate_dir / "judge_inputs"
    report_dir = gate_dir / "report"
    if clean and input_dir.exists():
        shutil.rmtree(input_dir)
    if clean and report_dir.exists():
        for filename in STALE_REPORT_ARTIFACTS:
            (report_dir / filename).unlink(missing_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)

    rendered: list[dict[str, Any]] = []
    for item in corpus.get("items", []):
        dimension = item["dimension"]
        rubric_meta = rubric_metadata(dimension)
        item_variants = _variants_for_item(item, variants)
        for variant in item_variants:
            prompt = _render_prompt(item, variant=variant)
            for repeat_index in range(repeat_count):
                eval_id = (
                    f"{dimension}__PG__{item['sample_id']}__{variant}__r{repeat_index}"
                )
                payload = {
                    "eval_id": eval_id,
                    "dimension": dimension,
                    "rubric_id": rubric_meta["rubric_id"],
                    "rubric_version": rubric_meta["rubric_version"],
                    "prompt": prompt,
                    "metadata": {
                        **rubric_meta,
                        **_contract_metadata(item["persona_id"]),
                        "judge_qualification": True,
                        "judge_qualification_version": corpus.get("version"),
                        "sample_id": item["sample_id"],
                        "prompt_variant_id": variant,
                        "repeat_index": repeat_index,
                        "persona_id": item["persona_id"],
                        "expected_score_band": item.get("expected_score_band"),
                        "expected_failure_types": item.get(
                            "expected_failure_types", []
                        ),
                        "judge_model": JUDGE_MODEL,
                        "judge_temperature": JUDGE_TEMPERATURE,
                    },
                }
                if dimension == "B1":
                    payload["metadata"]["expected_persona_id"] = item["persona_id"]
                if dimension == "control":
                    payload["metadata"]["set_a_is_persona"] = item.get(
                        "set_a_is_persona"
                    )
                output_path = input_dir / f"{eval_id}.json"
                atomic_write_json(output_path, payload)
                rendered.append(
                    {
                        "eval_id": eval_id,
                        "dimension": dimension,
                        "sample_id": item["sample_id"],
                        "prompt_variant_id": variant,
                        "repeat_index": repeat_index,
                        "path": str(output_path),
                    }
                )

    manifest = {
        "version": "judge_qualification_render_manifest_v1",
        "corpus_version": corpus.get("version"),
        "repeats": repeat_count,
        "prompt_variants": variants,
        "counts": {
            "items": len(corpus.get("items", [])),
            "judge_inputs": len(rendered),
        },
        "judge_input_dir": str(input_dir),
        "rendered": rendered,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "corpus_snapshot.json", corpus)
    atomic_write_json(report_dir / "render_manifest.json", manifest)
    return manifest


def _coerce_gate_dir(
    *,
    gate_dir: Path | None = None,
    results_dir: Path | None = None,
) -> Path:
    if gate_dir is not None:
        return gate_dir
    if results_dir is not None:
        return results_dir / DEFAULT_GATE_DIRNAME
    return DEFAULT_GATE_RESULTS_DIR


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--prompt-variants", default=None)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    variants = (
        parse_prompt_variants(args.prompt_variants) if args.prompt_variants else None
    )
    manifest = render_judge_qualification_inputs(
        gate_dir=resolve_results_dir(args.results_dir),
        corpus_path=args.corpus,
        repeats=args.repeats,
        prompt_variants=variants,
        clean=args.clean,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
