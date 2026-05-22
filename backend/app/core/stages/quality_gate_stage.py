"""Pre-writing quality gate facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.artifacts.diagnostics import FigureGateReport, QualityGateReport, WorkflowTrace
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, normalize_figure_role
from app.artifacts.registry import ArtifactRegistry, FigureBundle
from app.core.figure_stage import FigureStage


@dataclass(frozen=True)
class QualityGateSnapshot:
    """Structured result of pre-writing figure/result quality checks."""

    passed: bool
    reason: str
    expected_figures: bool
    workflow_issues: list[dict[str, Any]]
    workflow_issue_summary: dict[str, Any]
    figure_plan_diagnostics: dict[str, Any]
    figure_gate_report: FigureGateReport
    quality_gate_report: QualityGateReport


class QualityGateStage:
    """Centralize report generation for the pre-writing quality gate."""

    @staticmethod
    def evaluate(
        *,
        work_dir: str | Path,
        result_registry: dict[str, Any],
        figure_bundle: FigureBundle,
        artifact_registry: ArtifactRegistry,
        artifact_valid_images: list[str],
        writer_images: list[str],
        solve_spec: dict[str, Any],
        figure_plan_payload: dict[str, Any],
        workflow_mode: str,
        expected_figures: bool,
        trace: WorkflowTrace,
    ) -> QualityGateSnapshot:
        workflow_issues = FigureStage.normalize_workflow_issues(figure_plan_payload)
        workflow_issue_summary = FigureStage.workflow_issue_summary(workflow_issues)
        result_roles = [
            normalize_figure_role(item.get("semantic_role") or item.get("role"))
            for item in figure_bundle.result_figures
            if isinstance(item, dict)
        ]
        overview_only = bool(result_roles) and all(role == RESULT_OVERVIEW_ROLE for role in result_roles)
        figure_plan_diagnostics = {
            "requires_figures_by_problem": bool((solve_spec or {}).get("requires_figures_by_problem", False)),
            "required_feasible_count": int((solve_spec or {}).get("required_feasible_count", 0) or 0),
            "blocked_request_count": len(figure_bundle.blocked_requests),
            "blocked_requests": figure_bundle.blocked_requests,
            "recommended_request_count": len(figure_bundle.recommended_requests),
            "result_figure_roles": result_roles,
            "overview_only_result_figures": overview_only,
            "workflow_issue_count": int(workflow_issue_summary.get("count", 0) or 0),
            "workflow_issue_summary": workflow_issue_summary,
        }

        gate_passed, gate_reason = FigureStage.quality_gate_before_writing(
            result_registry,
            expected_figures,
            figure_bundle.required_requests,
            figure_bundle.satisfied_requests,
            figure_bundle.missing_requests,
            workflow_mode,
            requires_result_figures=bool((solve_spec or {}).get("requires_result_figures")),
            requires_explanatory_figures=bool((solve_spec or {}).get("requires_explanatory_figures")),
            result_figure_count=len(figure_bundle.result_figures),
            explanatory_figure_count=len(figure_bundle.explanatory_figures),
            requires_figures_by_problem=bool((solve_spec or {}).get("requires_figures_by_problem", expected_figures)),
            blocked_requests=figure_bundle.blocked_requests,
        )

        figure_gate = FigureGateReport.from_rejections(
            artifact_valid_images,
            artifact_registry.rejection_summary(),
        )
        figure_gate.save(work_dir)
        trace.add_stage(
            "figure_gate",
            "passed" if figure_gate.passed else "failed",
            figure_gate.reason,
            0,
            {
                "paper_ready_count": len(writer_images),
                "rejected_count": len(artifact_registry.figure_rejections),
            },
        )

        quality_gate = QualityGateReport.from_check(
            verified_count=len(result_registry.get("verified_results", []) or []),
            blocked_count=len(result_registry.get("blocked_results", []) or []),
            expected_figures=expected_figures,
            paper_ready_count=len(writer_images),
            passed=gate_passed,
            reason=gate_reason,
            required_request_count=len(figure_bundle.required_requests),
            satisfied_request_count=len(figure_bundle.satisfied_requests),
            missing_requests=figure_bundle.missing_requests,
        )
        quality_gate.save(work_dir)

        return QualityGateSnapshot(
            passed=gate_passed,
            reason=gate_reason,
            expected_figures=expected_figures,
            workflow_issues=workflow_issues,
            workflow_issue_summary=workflow_issue_summary,
            figure_plan_diagnostics=figure_plan_diagnostics,
            figure_gate_report=figure_gate,
            quality_gate_report=quality_gate,
        )
