"""chart language responsibilities."""

from __future__ import annotations

import re


def _detect_document_language(*texts: str) -> str:
    combined = "\n".join(text for text in texts if text)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", combined))
    latin_words = len(re.findall(r"\b[A-Za-z][A-Za-z0-9'-]*\b", combined))

    if latin_words >= 80 and latin_words >= cjk_count * 2:
        return "English"
    if cjk_count >= 30 and cjk_count > latin_words:
        return "Simplified Chinese"
    return "English" if latin_words > cjk_count else "Simplified Chinese"


def _chart_language_policy(document_language: str) -> str:
    if document_language == "English":
        return (
            "## Chart language policy\n"
            "- The document/problem language is English. All visible chart text must be English: titles, axis labels, legends, annotations, colorbar labels, and in-figure captions.\n"
            "- Do not use Chinese labels in generated figures for this task.\n"
            "- Prefer ASCII English labels to avoid missing-font boxes in PNG output.\n"
        )
    return (
        "## Chart language policy\n"
        "- The document/problem language is Simplified Chinese. All visible chart text must use Simplified Chinese: titles, axis labels, legends, annotations, colorbar labels, tick-category labels, and in-figure captions.\n"
        "- Translate generic labels such as Time, Distance, Speed, Heading, Shading duration, Solution, Parameter, Value, Unit, UAV, and Missile into Chinese. Keep units such as m, s, m/s, rad, and kg unchanged.\n"
        "- Do not leave English-only chart labels in figures for Chinese tasks.\n"
        "- Keep labels short and rely on the environment's preset matplotlib font configuration.\n"
    )


def _strict_chart_artifact_contract(document_language: str) -> str:
    expected = "English" if document_language == "English" else "Simplified Chinese"
    if document_language == "English":
        audit_example = '["Wavelength (nm)", "Reflectance (%)", "Fitted curve"]'
        example_filename = "problem1_trajectory.png"
    else:
        audit_example = '["波长/nm", "反射率/%", "拟合曲线"]'
        example_filename = "problem1_trajectory.png"
    return (
        "## Strict chart artifact contract\n"
        "- Use save_paper_figure(fig, filename, visible_text_audit=[...], linked_result_ids=[...], source_data=[...]) for every final paper figure.\n"
        "- Do not use plt.savefig/fig.savefig for deliverable images.\n"
        "- You MUST use the exact dict returned by save_paper_figure() as the generated_files entry. "
        "Do NOT hand-write or fabricate the artifact object — the pipeline verifies created_by and helper_version fields "
        "that only save_paper_figure() can produce.\n"
        f"- Example call: save_paper_figure(fig, '{example_filename}', visible_text_audit={audit_example}, linked_result_ids=['result_id_1'], source_data=['result_registry.json'])\n"
        "- Expected return value shape: "
        "{\"path\":\"output/figure.png\",\"kind\":\"figure\",\"paper_ready\":true,"
        f"\"visible_text_language\":\"{expected}\",\"chart_language_verified\":true,"
        f"\"visible_text_audit\":{audit_example},"
        "\"created_by\":\"save_paper_figure\",\"helper_version\":3,"
        "\"is_placeholder\":false,\"figure_role\":\"result_summary\","
        "\"linked_result_ids\":[\"result_id_1\"],\"source_data\":[\"result_registry.json\"]}.\n"
        f"- visible_text_audit must contain the ACTUAL visible text strings rendered on the figure in {expected}. "
        "Do NOT use generic field names like 'title', 'axis labels', 'legend'.\n"
        "- If any visible chart text does not match the Chart language policy, set paper_ready=false and save the file under debug_artifacts/ or redraw it before registration.\n"
        "- The writer will only receive images that pass this metadata gate; unverified images are intentionally withheld from the paper.\n"
        "- For required paper figures, you must also provide semantic fields in the generated_files artifact object: "
        "figure_request_id, subproblem_id, semantic_role, depicts, data_bindings, semantic_verified=true.\n"
        "- Diagnostic charts (including verified_result_summary) must use semantic_verified=false and paper_section='diagnostics'.\n"
    )


def _chart_language_context(document_language: str) -> dict[str, object]:
    if document_language == "English":
        return {
            "document_language": "English",
            "chart_language": "English",
            "required_visible_text": "English",
            "final_image_contract": {
                "paper_ready": True,
                "visible_text_language": "English",
                "chart_language_verified": True,
                "is_placeholder": False,
                "figure_role": "result_summary",
                "linked_result_ids": [],
                "source_data": [],
            },
            "rules": [
                "All visible chart text must be English.",
                "Do not cite or register Chinese-labeled figures as paper_ready.",
                "Use save_paper_figure for final figures.",
                "Every figure must have linked_result_ids referencing verified results.",
                "Placeholder/test figures (test_plot.png, demo.png, etc.) are forbidden.",
            ],
        }
    return {
        "document_language": "Simplified Chinese",
        "chart_language": "Simplified Chinese",
        "required_visible_text": "Simplified Chinese",
        "font_policy": "Use container Chinese fonts configured by LocalCodeInterpreter.",
        "final_image_contract": {
            "paper_ready": True,
            "visible_text_language": "Simplified Chinese",
            "chart_language_verified": True,
            "is_placeholder": False,
            "figure_role": "result_summary",
            "linked_result_ids": [],
            "source_data": [],
        },
        "rules": [
            "All visible chart text must be Simplified Chinese.",
            "English is allowed only for units, formulas, and established symbols.",
            "Use save_paper_figure for final figures.",
            "Every figure must have linked_result_ids referencing verified results.",
            "Placeholder/test figures (test_plot.png, demo.png, etc.) are forbidden.",
        ],
    }


def _image_manifest_text(image_manifest: list[dict] | None) -> str:
    if not image_manifest:
        return "未提供图片资源"

    rows = ["| 图片路径 | 是否被 Markdown 引用 | 大小(KB) |", "| --- | --- | ---: |"]
    for item in image_manifest:
        rows.append(
            f"| {item.get('path', '')} | {'是' if item.get('referenced') else '否'} | {item.get('size_kb', 0)} |"
        )
    return "\n".join(rows)
