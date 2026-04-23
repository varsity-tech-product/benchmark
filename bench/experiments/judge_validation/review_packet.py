"""Reviewer packet export for judge-validation human labels."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REVIEW_PACKET_VERSION = "judge_validation_human_review_packet_v1"
REVIEW_SAMPLE_MAP_VERSION = "judge_validation_human_review_sample_map_v1"

FORM_FIELDS = [
    "reviewer_id",
    "sample_id",
    "transcript_id",
    "rubric_id",
    "dimension",
    "human_score",
    "confidence",
    "human_rationale",
    "evidence_spans",
    "failure_tags",
    "notes",
]

SUGGESTED_FAILURE_TAGS = [
    "quant_error",
    "code_error",
    "missing_task_completion",
    "hallucinated_tool_output",
    "poor_adaptation",
    "answer_dump",
    "unsafe_financial_advice",
    "unclear_rubric",
    "other",
]

BILINGUAL_FORM_FIELDS = [
    {
        "field": "reviewer_id",
        "title": "reviewer_id / 专家 ID",
        "type": "short_answer",
        "required": True,
        "help": "Use an anonymized reviewer ID or collected email. / 使用匿名专家 ID 或自动收集的邮箱。",
    },
    {
        "field": "sample_id",
        "title": "sample_id / 样本 ID",
        "type": "dropdown",
        "required": True,
        "help": "Choose one sample from the review packet. / 从标注包中选择一个样本。",
    },
    {
        "field": "rubric_id",
        "title": "rubric_id / 评分规则 ID",
        "type": "dropdown",
        "required": True,
        "help": "Copy the rubric ID shown for the sample. / 选择样本对应的评分规则 ID。",
    },
    {
        "field": "dimension",
        "title": "dimension / 评分维度",
        "type": "short_answer",
        "required": True,
        "help": "Copy the dimension shown for the sample. / 填写样本对应的评分维度。",
    },
    {
        "field": "human_score",
        "title": "human_score / 人类专家评分",
        "type": "multiple_choice",
        "required": True,
        "options": ["1", "2", "3", "4", "5"],
        "help": "Integer score from 1 to 5 using the listed anchors. / 按锚点评 1 到 5 的整数分。",
    },
    {
        "field": "confidence",
        "title": "confidence / 评分信心",
        "type": "multiple_choice",
        "required": True,
        "options": ["high / 高", "medium / 中", "low / 低"],
        "help": "How confident you are in the label. / 你对这个标注的信心。",
    },
    {
        "field": "human_rationale",
        "title": "human_rationale / 专家理由",
        "type": "paragraph",
        "required": True,
        "help": "Brief rubric-grounded rationale. / 用评分规则说明理由。",
    },
    {
        "field": "evidence_spans",
        "title": "evidence_spans / 证据片段",
        "type": "paragraph",
        "required": False,
        "help": "Paste relevant transcript spans, one per line. / 粘贴相关对话证据，每行一个。",
    },
    {
        "field": "failure_tags",
        "title": "failure_tags / 失败标签",
        "type": "checkboxes",
        "required": False,
        "options": [f"{tag} / {tag.replace('_', ' ')}" for tag in SUGGESTED_FAILURE_TAGS],
        "help": "Select all applicable tags. / 选择所有适用标签。",
    },
    {
        "field": "notes",
        "title": "notes / 备注",
        "type": "paragraph",
        "required": False,
        "help": "Optional reviewer notes. / 可选备注。",
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rubric_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(entry.get("rubric_id", "")): entry
        for entry in registry.get("rubrics", [])
        if entry.get("rubric_id")
    }


def _conversation_context(item: dict[str, Any]) -> str:
    if item.get("context"):
        return str(item["context"])
    blocks = []
    for index, turn in enumerate(item.get("conversation", []), start=1):
        role = str(turn.get("role") or "unknown").title()
        content = str(turn.get("content") or "")
        blocks.append(f"Turn {index} - {role}\n{content}")
    return "\n\n".join(blocks)


def _selected_items(
    corpus: dict[str, Any],
    *,
    sample_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    indexed_items = list(enumerate(corpus.get("items", []), start=1))
    if sample_ids:
        wanted = set(sample_ids)
        indexed_items = [
            (index, item)
            for index, item in indexed_items
            if str(item.get("sample_id")) in wanted
        ]
    if limit and limit > 0:
        indexed_items = indexed_items[:limit]
    return indexed_items


def build_review_packet(
    *,
    corpus: dict[str, Any],
    rubric_registry: dict[str, Any],
    sample_ids: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a reviewer-facing packet without expected-score hints."""

    rubrics = _rubric_index(rubric_registry)
    packet_items: list[dict[str, Any]] = []
    sample_map: list[dict[str, Any]] = []
    for corpus_index, item in _selected_items(
        corpus,
        sample_ids=sample_ids,
        limit=limit,
    ):
        rubric_id = str(item.get("registry_rubric_id") or "")
        rubric = rubrics.get(rubric_id, {})
        original_sample_id = str(item.get("sample_id") or "")
        review_sample_id = f"jv_review_{corpus_index:03d}"
        sample_map.append(
            {
                "review_sample_id": review_sample_id,
                "original_sample_id": original_sample_id,
                "rubric_id": rubric_id,
                "dimension": item.get("dimension"),
            }
        )
        packet_items.append(
            {
                "sample_id": review_sample_id,
                "transcript_id": review_sample_id,
                "task_id": item.get("task_id"),
                "category": item.get("category"),
                "persona_id": item.get("persona_id"),
                "transcript_source": item.get("transcript_source"),
                "track": item.get("track"),
                "dimension": item.get("dimension"),
                "rubric_id": rubric_id,
                "rubric_version": rubric.get("version"),
                "rubric_dimension": rubric.get("dimension"),
                "score_scale": rubric.get("score_scale"),
                "score_anchors": rubric.get("score_anchors") or {},
                "required_evidence": rubric.get("required_evidence") or [],
                "common_failure_cases": rubric.get("common_failure_cases") or [],
                "examples": rubric.get("examples") or {},
                "review_context": _conversation_context(item),
            }
        )

    return {
        "version": REVIEW_PACKET_VERSION,
        "generated_at": _utc_now(),
        "description": (
            "Human expert review packet for judge validation. Reviewers score "
            "each transcript against the listed rubric only."
        ),
        "form_fields": FORM_FIELDS,
        "bilingual_google_form_fields": BILINGUAL_FORM_FIELDS,
        "suggested_failure_tags": SUGGESTED_FAILURE_TAGS,
        "counts": {
            "items": len(packet_items),
        },
        "private_sample_map": sample_map,
        "items": packet_items,
    }


def _markdown_list(values: list[Any]) -> list[str]:
    return [f"- {value}" for value in values] or ["-"]


def markdown_review_packet(packet: dict[str, Any]) -> str:
    lines = [
        "# Judge Validation Human Review Packet",
        "",
        f"Generated: {packet.get('generated_at')}",
        f"Items: {packet.get('counts', {}).get('items')}",
        "",
        "## Reviewer Fields",
        "",
    ]
    lines.extend(_markdown_list(packet.get("form_fields") or []))
    lines.extend(["", "## Suggested Failure Tags", ""])
    lines.extend(_markdown_list(packet.get("suggested_failure_tags") or []))

    for item in packet.get("items", []):
        lines.extend(
            [
                "",
                f"## {item.get('sample_id')}",
                "",
                f"- Task: {item.get('task_id')}",
                f"- Category: {item.get('category')}",
                f"- Persona: {item.get('persona_id')}",
                f"- Rubric: {item.get('rubric_id')} ({item.get('rubric_version')})",
                f"- Dimension: {item.get('dimension')}",
                "",
                "### Score Anchors",
                "",
            ]
        )
        for score, anchor in sorted((item.get("score_anchors") or {}).items()):
            lines.append(f"- {score}: {anchor}")
        lines.extend(["", "### Required Evidence", ""])
        lines.extend(_markdown_list(item.get("required_evidence") or []))
        lines.extend(["", "### Common Failure Cases", ""])
        lines.extend(_markdown_list(item.get("common_failure_cases") or []))
        lines.extend(["", "### Transcript Or Evaluation Context", "", "```text"])
        lines.append(str(item.get("review_context") or ""))
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def markdown_bilingual_google_form(packet: dict[str, Any]) -> str:
    sample_options = [str(item.get("sample_id")) for item in packet.get("items", [])]
    rubric_options = sorted(
        {
            str(item.get("rubric_id"))
            for item in packet.get("items", [])
            if item.get("rubric_id")
        }
    )
    lines = [
        "# Human Expert Labeling Google Form / 人类专家标注 Google Form",
        "",
        "Form title / 表单标题:",
        "Judge Validation Human Labels / 裁判验证人类专家标注",
        "",
        "Form description / 表单说明:",
        (
            "Submit one response per sample. Read the matching sample in "
            "human_review_packet.md, then score the transcript using the listed "
            "rubric anchors. / 每个样本提交一次。先阅读 human_review_packet.md "
            "中对应样本，再按评分锚点给该转录打分。"
        ),
        "",
        "Recommended setting / 推荐设置:",
        "- Collect email addresses / 收集电子邮件地址",
        "- Keep responses editable / 允许提交后编辑",
        "",
        "## Questions / 问题",
        "",
    ]
    for field in packet.get("bilingual_google_form_fields") or []:
        lines.extend(
            [
                f"### {field.get('title')}",
                "",
                f"- Type / 类型: {field.get('type')}",
                f"- Required / 必填: {field.get('required')}",
                f"- Help / 帮助: {field.get('help')}",
            ]
        )
        options = list(field.get("options") or [])
        if field.get("field") == "sample_id":
            options = sample_options
        if field.get("field") == "rubric_id":
            options = rubric_options
        if options:
            lines.append("- Options / 选项:")
            lines.extend(f"  - {option}" for option in options)
        lines.append("")

    lines.extend(["## Sample Index / 样本索引", ""])
    for item in packet.get("items", []):
        lines.extend(
            [
                f"### {item.get('sample_id')}",
                "",
                f"- Task / 任务: {item.get('task_id')}",
                f"- Category / 类别: {item.get('category')}",
                f"- Persona / 学生画像: {item.get('persona_id')}",
                f"- Rubric / 评分规则: {item.get('rubric_id')}",
                f"- Dimension / 维度: {item.get('dimension')}",
                "",
            ]
        )
    return "\n".join(lines)


def write_review_packet(
    *,
    packet: dict[str, Any],
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "human_review_packet.json"
    md_path = output_dir / "human_review_packet.md"
    csv_path = output_dir / "human_label_template.csv"
    google_form_path = output_dir / "google_form_bilingual.md"
    sample_map_path = output_dir / "human_review_sample_map.json"
    public_packet = {
        key: value for key, value in packet.items() if key != "private_sample_map"
    }

    json_path.write_text(
        json.dumps(public_packet, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(markdown_review_packet(public_packet), encoding="utf-8")
    google_form_path.write_text(
        markdown_bilingual_google_form(public_packet),
        encoding="utf-8",
    )
    sample_map_path.write_text(
        json.dumps(
            {
                "version": REVIEW_SAMPLE_MAP_VERSION,
                "generated_at": _utc_now(),
                "mappings": packet.get("private_sample_map", []),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FORM_FIELDS)
        writer.writeheader()
        for item in public_packet.get("items", []):
            writer.writerow(
                {
                    "reviewer_id": "",
                    "sample_id": item.get("sample_id"),
                    "transcript_id": item.get("transcript_id"),
                    "rubric_id": item.get("rubric_id"),
                    "dimension": item.get("dimension"),
                    "human_score": "",
                    "confidence": "",
                    "human_rationale": "",
                    "evidence_spans": "",
                    "failure_tags": "",
                    "notes": "",
                }
            )

    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "csv": str(csv_path),
        "google_form_bilingual": str(google_form_path),
        "sample_map": str(sample_map_path),
    }
