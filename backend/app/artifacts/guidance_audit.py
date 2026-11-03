"""guidance audit responsibilities."""

from __future__ import annotations

import json
from pathlib import Path
from app.artifacts.guidance_audit_evidence import _check_minimum_guidance_evidence, _check_parameter_coverage, _check_registered_numbers
from app.artifacts.guidance_audit_sections import _check_blocked_disclosure, _check_data_source_section_context, _check_figure_coverage, _check_figure_interpretation_context, _check_identifiability_section_structure, _check_problem_understanding_section_context, _check_reader_decision_section_structure, _check_reader_guidance_value, _check_required_sections, _check_route_tradeoff_section_structure
from app.artifacts.guidance_audit_answers import _check_calculation_results_section_structure, _check_claim_registry_section_structure, _check_final_answer_evidence_binding, _check_parameter_source_table_structure, _check_review_section_error_context, _check_subproblem_steps_section_structure


def build_guidance_audit_report(
    *,
    guidance_markdown: str,
    guidance_context: dict[str, object] | None,
    result_registry: dict[str, object] | None,
    parameter_registry: dict[str, object] | None = None,
    figure_registry: dict[str, object] | None = None,
    artifact_root: Path | None = None,
) -> dict[str, object]:
    """Audit whether a guidance document is traceable enough to trust."""
    text = guidance_markdown or ""
    context = guidance_context if isinstance(guidance_context, dict) else {}
    results = result_registry if isinstance(result_registry, dict) else {}
    parameters = parameter_registry if isinstance(parameter_registry, dict) else {}
    figures = figure_registry if isinstance(figure_registry, dict) else {}

    blocks: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []

    _check_required_sections(text, context, blocks)
    _check_blocked_disclosure(text, results, blocks)
    if not _is_blocking_diagnostic(text):
        _check_reader_guidance_value(text, context, blocks)
        _check_reader_decision_section_structure(text, context, blocks)
        _check_problem_understanding_section_context(text, context, blocks)
        _check_data_source_section_context(text, context, results, parameters, blocks)
        _check_route_tradeoff_section_structure(text, context, blocks)
        _check_identifiability_section_structure(text, context, blocks)
        _check_claim_registry_section_structure(text, context, blocks)
        _check_subproblem_steps_section_structure(text, context, blocks)
        _check_calculation_results_section_structure(text, context, parameters, blocks)
        _check_final_answer_evidence_binding(text, context, results, parameters, blocks)
        _check_parameter_coverage(text, parameters, blocks)
        _check_parameter_source_table_structure(text, context, parameters, blocks)
        _check_review_section_error_context(text, context, blocks)
        _check_minimum_guidance_evidence(results, parameters, blocks)
        _check_registered_numbers(text, results, parameters, blocks)
        _check_figure_interpretation_context(text, context, figures, blocks)
        _check_figure_coverage(text, figures, artifact_root, blocks)

    status = "PASS"
    if blocks:
        status = "BLOCK"
    elif warnings:
        status = "PARTIAL"

    return {
        "artifact_type": "guidance",
        "status": status,
        "scores": _score_report(
            text=text,
            blocks=blocks,
            warnings=warnings,
            result_registry=results,
            parameter_registry=parameters,
        ),
        "blocks": blocks,
        "warnings": warnings,
    }


def _is_blocking_diagnostic(text: str) -> bool:
    return text.startswith((
        "# 计算链路阻断诊断",
        "# 建模链路阻断诊断",
        "# 结果复核阻断诊断",
    ))


def _score_report(
    *,
    text: str,
    blocks: list[dict[str, object]],
    warnings: list[dict[str, object]],
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
) -> dict[str, float]:
    verified = result_registry.get("verified_results", [])
    blocked = result_registry.get("blocked_results", [])
    parameters = parameter_registry.get("parameters", [])

    verified_count = len(verified) if isinstance(verified, list) else 0
    blocked_count = len(blocked) if isinstance(blocked, list) else 0
    parameter_count = len(parameters) if isinstance(parameters, list) else 0

    block_types = {str(block.get("type")) for block in blocks}
    warning_penalty = min(len(warnings) * 0.08, 0.4)

    return {
        "result_traceability": 0.0 if "unregistered_numeric_claim" in block_types else (1.0 if verified_count else 0.5),
        "parameter_provenance": 0.0 if "missing_parameter_registry_coverage" in block_types else (1.0 if parameter_count else 0.4),
        "blocked_disclosure": 0.0 if "blocked_items_not_disclosed" in block_types else (1.0 if blocked_count else 1.0),
        "guidance_actionability": max(0.0, min(1.0, (0.8 if text.strip() else 0.0) - warning_penalty)),
    }


def report_to_json(report: dict[str, object]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
