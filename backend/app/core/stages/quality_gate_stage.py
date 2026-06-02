"""Pre-writing quality gate facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.artifacts.answer_plan import AnswerPlanBuilder
from app.artifacts.diagnostics import (
    FigureGateReport,
    QualityGateReport,
    WorkflowTrace,
    summarize_visualization_issues,
)
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
    answer_plan_diagnostics: dict[str, Any]
    figure_plan_diagnostics: dict[str, Any]
    figure_gate_report: FigureGateReport
    quality_gate_report: QualityGateReport


class QualityGateStage:
    """Centralize report generation for the pre-writing quality gate."""

    @staticmethod
    def _figure_plan_bool(
        figure_plan_payload: dict[str, Any],
        key: str,
        fallback: bool = False,
    ) -> bool:
        if isinstance(figure_plan_payload, dict) and key in figure_plan_payload:
            return bool(figure_plan_payload.get(key))
        return fallback

    @staticmethod
    def _has_figure_plan_signal(figure_plan_payload: dict[str, Any]) -> bool:
        if not isinstance(figure_plan_payload, dict) or not figure_plan_payload:
            return False
        signal_keys = {
            "source",
            "planner",
            "requires_figures",
            "requires_result_figures",
            "requires_explanatory_figures",
            "requires_figures_by_problem",
            "required_feasible_count",
            "figure_requests",
        }
        return any(key in figure_plan_payload for key in signal_keys)

    @staticmethod
    def _effective_figure_requirements(
        *,
        figure_plan_payload: dict[str, Any],
        figure_bundle: FigureBundle,
        solve_spec: dict[str, Any],
        expected_figures: bool,
    ) -> dict[str, Any]:
        has_plan_signal = QualityGateStage._has_figure_plan_signal(figure_plan_payload)
        if has_plan_signal:
            requires_result = QualityGateStage._figure_plan_bool(
                figure_plan_payload, "requires_result_figures"
            )
            requires_explanatory = QualityGateStage._figure_plan_bool(
                figure_plan_payload, "requires_explanatory_figures"
            )
            requires_by_problem = QualityGateStage._figure_plan_bool(
                figure_plan_payload, "requires_figures_by_problem"
            )
            requires_any = (
                QualityGateStage._figure_plan_bool(figure_plan_payload, "requires_figures")
                or requires_result
                or requires_explanatory
                or requires_by_problem
                or bool(figure_bundle.required_requests)
            )
            required_feasible_count = int(
                figure_plan_payload.get("required_feasible_count", len(figure_bundle.required_requests)) or 0
            )
            source = "figure_plan"
        else:
            requires_result = bool((solve_spec or {}).get("requires_result_figures"))
            requires_explanatory = bool((solve_spec or {}).get("requires_explanatory_figures"))
            requires_by_problem = bool((solve_spec or {}).get("requires_figures_by_problem", expected_figures))
            requires_any = bool(expected_figures or figure_bundle.required_requests)
            required_feasible_count = int((solve_spec or {}).get("required_feasible_count", 0) or 0)
            source = "solve_spec_compat"
        return {
            "source": source,
            "expected_figures": requires_any,
            "requires_result_figures": requires_result,
            "requires_explanatory_figures": requires_explanatory,
            "requires_figures_by_problem": requires_by_problem,
            "required_feasible_count": required_feasible_count,
        }

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
        question: str = "",
    ) -> QualityGateSnapshot:
        answer_plan = AnswerPlanBuilder.evaluate(
            solve_spec=solve_spec,
            result_registry=result_registry,
            question=question,
        )
        AnswerPlanBuilder.save(work_dir, answer_plan)
        workflow_issues = FigureStage.normalize_workflow_issues(figure_plan_payload)
        workflow_issue_summary = FigureStage.workflow_issue_summary(workflow_issues)
        diagnostic_summary = summarize_visualization_issues([
            *workflow_issues,
            *figure_bundle.blocked_requests,
            *figure_bundle.missing_requests,
        ])
        effective_requirements = QualityGateStage._effective_figure_requirements(
            figure_plan_payload=figure_plan_payload,
            figure_bundle=figure_bundle,
            solve_spec=solve_spec,
            expected_figures=expected_figures,
        )
        effective_expected_figures = bool(effective_requirements["expected_figures"])
        result_roles = [
            normalize_figure_role(item.get("semantic_role") or item.get("role"))
            for item in figure_bundle.result_figures
            if isinstance(item, dict)
        ]
        overview_only = bool(result_roles) and all(role == RESULT_OVERVIEW_ROLE for role in result_roles)
        figure_plan_diagnostics = {
            "requirement_source": effective_requirements["source"],
            "input_expected_figures": expected_figures,
            "effective_expected_figures": effective_expected_figures,
            "requires_figures_by_problem": bool(effective_requirements["requires_figures_by_problem"]),
            "required_feasible_count": int(effective_requirements["required_feasible_count"]),
            "blocked_request_count": len(figure_bundle.blocked_requests),
            "blocked_requests": figure_bundle.blocked_requests,
            "recommended_request_count": len(figure_bundle.recommended_requests),
            "result_figure_roles": result_roles,
            "overview_only_result_figures": overview_only,
            "workflow_issue_count": int(workflow_issue_summary.get("count", 0) or 0),
            "workflow_issue_summary": workflow_issue_summary,
            "diagnostic_summary": diagnostic_summary,
        }
        protocol_warnings = (
            artifact_registry.protocol_warning_summary()
            if hasattr(artifact_registry, "protocol_warning_summary")
            else []
        )
        if protocol_warnings:
            figure_plan_diagnostics["protocol_warnings"] = protocol_warnings

        gate_passed, gate_reason = FigureStage.quality_gate_before_writing(
            result_registry,
            effective_expected_figures,
            figure_bundle.required_requests,
            figure_bundle.satisfied_requests,
            figure_bundle.missing_requests,
            workflow_mode,
            requires_result_figures=bool(effective_requirements["requires_result_figures"]),
            requires_explanatory_figures=bool(effective_requirements["requires_explanatory_figures"]),
            result_figure_count=len(figure_bundle.result_figures),
            explanatory_figure_count=len(figure_bundle.explanatory_figures),
            requires_figures_by_problem=bool(effective_requirements["requires_figures_by_problem"]),
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
            expected_figures=effective_expected_figures,
            paper_ready_count=len(writer_images),
            passed=gate_passed,
            reason=gate_reason,
            required_request_count=len(figure_bundle.required_requests),
            satisfied_request_count=len(figure_bundle.satisfied_requests),
            missing_requests=figure_bundle.missing_requests,
            workflow_issues=workflow_issues,
            blocked_requests=figure_bundle.blocked_requests,
            diagnostic_summary=diagnostic_summary,
            answer_completeness=answer_plan,
        )
        quality_gate.save(work_dir)

        return QualityGateSnapshot(
            passed=gate_passed,
            reason=gate_reason,
            expected_figures=effective_expected_figures,
            workflow_issues=workflow_issues,
            workflow_issue_summary=workflow_issue_summary,
            answer_plan_diagnostics=answer_plan,
            figure_plan_diagnostics=figure_plan_diagnostics,
            figure_gate_report=figure_gate,
            quality_gate_report=quality_gate,
        )
