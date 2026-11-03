import ast
import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("module_name", "symbol"),
    [
        ("app.core.workflow_runtime", "_progress"),
        ("app.core.writing_support", "_run_writer_stage"),
        ("app.core.chart_support", "_create_verified_result_summary_figure"),
        ("app.core.solver_planning", "_solver_budget_for_subproblem"),
        ("app.utils.file_io", "load_json"),
        ("app.utils.problem_text", "build_problem_facts"),
        ("app.utils.result_registry", "build_result_registry"),
        ("app.utils.markdown", "normalize_math_markdown"),
        ("app.utils.document_export", "finalize_markdown_export"),
        ("app.utils.paper_audit", "build_paper_audit_report"),
        ("app.artifacts.plotting", "_configure_fonts"),
        ("app.artifacts.chart_renderers", "render_distance_time_curve"),
        ("app.artifacts.schematic_renderers", "render_geometry_schematic"),
        ("app.artifacts.table_renderers", "render_verified_summary"),
        ("app.artifacts.guidance_audit_evidence", "_check_registered_numbers"),
        ("app.artifacts.guidance_audit_sections", "_check_reader_guidance_value"),
        ("app.artifacts.guidance_audit_tables", "_final_answer_table_rows_missing_evidence"),
        ("app.artifacts.figure_need", "FigureNeedDetector"),
        ("app.artifacts.figure_feasibility", "_apply_capability_constraints"),
        ("app.artifacts.figure_request_planning", "_backfill_requests"),
        ("app.artifacts.document_validation", "_validate_coverage_disclosure"),
        ("app.artifacts.document_images", "_normalize_image_refs"),
        ("app.artifacts.document_outputs", "append_guidance_audit_appendix"),
        ("app.core.figure_requests", "ensure_figure_requests_in_solve_spec"),
        ("app.core.figure_gate", "quality_gate_before_writing"),
        ("app.core.figure_data", "deterministic_series_from_registry"),
        ("app.core.figure_rendering", "render_required_requests_deterministically"),
        ("app.core.skills.renderer_requests", "_safe_request_id"),
        ("app.core.skills.renderer_views", "_render_fitting_curve"),
        ("app.core.agents.coder_artifacts", "_validate_structured_result_file"),
        ("app.core.agents.coder_chart_language", "_english_chart_label_literals"),
        ("app.core.agents.coder_chart_language", "_chart_language_guard_message"),
        ("app.utils.result_legacy", "_extract_legacy_registry_entries"),
        ("app.utils.result_audit", "_audit_torque_coefficient_artifacts"),
        ("app.core.chart_language", "_chart_language_policy"),
        ("app.artifacts.guidance_audit_answers", "_check_final_answer_evidence_binding"),
    ],
)
def test_writing_support_implementation_has_a_specific_owner(module_name, symbol):
    implementation = getattr(importlib.import_module(module_name), symbol)
    assert implementation.__module__ == module_name


def test_polish_imports_support_without_workflow_router_dependency():
    source = Path(__file__).parents[1] / "app" / "polish" / "workflow.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.workflow"
        for alias in node.names
    }
    assert imported <= {
        "_build_completed_task_result", "_build_failed_task_result", "_classify_failure"
    }


def test_migrated_text_and_runtime_contracts_keep_existing_behavior():
    from app.core.workflow_runtime import _message, _preview
    from app.utils.markdown import normalize_math_markdown
    from app.utils.problem_text import extract_symbol_assignments

    assert _message("writer", "ready", section="summary") == {
        "type": "success", "agent": "writer", "content": "ready", "section": "summary"
    }
    assert _preview(" one\n two ", 30) == "one two"
    assert normalize_math_markdown(r"\(x=1\)") == "$x=1$\n"
    assert extract_symbol_assignments("alpha = 12 MPa")["alpha"]["numeric_value"] == 12


def test_polish_resolves_its_budget_from_the_budget_owner():
    from app.polish import workflow
    from app.core.workflow_budget import resolve_workflow_budget

    assert workflow.resolve_workflow_budget.__module__ == "app.core.workflow_budget"
    assert workflow.resolve_workflow_budget("fast") == resolve_workflow_budget("fast")
