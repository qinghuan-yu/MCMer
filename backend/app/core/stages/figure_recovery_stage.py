"""Figure request reevaluation and deterministic recovery stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.artifacts.contracts import ProblemContract
from app.artifacts.figure_plan import FigurePlanBuilder
from app.artifacts.registry import ArtifactRegistry
from app.core.skills.renderer import RendererSkill


def _unique_strings(*items: object) -> list[str]:
    values: list[str] = []
    for item in items:
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                text = str(nested or "").strip()
                if text:
                    values.append(text)
        else:
            text = str(item or "").strip()
            if text:
                values.append(text)
    return list(dict.fromkeys(values))


@dataclass(frozen=True)
class FigureRequestReevaluation:
    """Result of request feasibility reevaluation."""

    requests: list[dict[str, Any]] = field(default_factory=list)
    figure_plan_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeterministicRecoverySnapshot:
    """Deterministic renderer result and refreshed artifact registry."""

    report: dict[str, Any]
    chart_images: list[str]
    artifact_registry: ArtifactRegistry
    artifact_valid_images: list[str]


class FigureRecoveryStage:
    """Deterministic figure recovery helpers used before LLM repair."""

    @staticmethod
    def _plan_counts(requests: list[dict[str, Any]]) -> dict[str, int]:
        required = [
            item for item in requests
            if isinstance(item, dict) and bool(item.get("required", True))
        ]
        required_result = [
            item for item in required
            if str(item.get("figure_kind") or "result_figure").strip().lower() == "result_figure"
        ]
        required_explanatory = [
            item for item in required
            if str(item.get("figure_kind") or "").strip().lower() == "explanatory_figure"
        ]
        blocked = [
            item for item in requests
            if isinstance(item, dict) and str(item.get("status") or "") == "blocked"
        ]
        recommended = [
            item for item in requests
            if isinstance(item, dict) and str(item.get("status") or "") == "recommended"
        ]
        return {
            "required_feasible_count": len(required),
            "required_feasible_result_count": len(required_result),
            "required_feasible_explanatory_count": len(required_explanatory),
            "required_count": len(required),
            "recommended_count": len(recommended),
            "blocked_count": len(blocked),
        }

    @staticmethod
    def reevaluate_solve_spec_requests(
        *,
        work_dir: str | Path,
        solve_spec: dict[str, Any],
        chart_language: str,
        solve_spec_path: str | Path,
        figure_plan_payload: dict[str, Any] | None = None,
    ) -> FigureRequestReevaluation:
        plan_payload = dict(figure_plan_payload or {})
        source_requests = plan_payload.get("figure_requests", []) if plan_payload else []
        source = "figure_plan"
        if not isinstance(source_requests, list) or not any(isinstance(item, dict) for item in source_requests):
            source_requests = solve_spec.get("figure_requests", []) if isinstance(solve_spec, dict) else []
            source = "solve_spec_compat"
        if not isinstance(source_requests, list) or not source_requests:
            return FigureRequestReevaluation()

        reevaluated = FigurePlanBuilder.reevaluate_requests(
            requests=[item for item in source_requests if isinstance(item, dict)],
            chart_language=chart_language,
            work_dir=str(work_dir),
        )
        if not reevaluated:
            return FigureRequestReevaluation()

        updated_plan = plan_payload or {
            "planner": "FigureRecoveryStage",
            "source": source,
        }
        updated_plan["figure_requests"] = reevaluated
        updated_plan.update(FigureRecoveryStage._plan_counts(reevaluated))
        updated_plan["requires_figures"] = any(
            isinstance(item, dict) and bool(item.get("required", True))
            for item in reevaluated
        )
        updated_plan["requires_result_figures"] = any(
            isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("figure_kind") or "result_figure").strip().lower() == "result_figure"
            for item in reevaluated
        )
        updated_plan["requires_explanatory_figures"] = any(
            isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("figure_kind") or "").strip().lower() == "explanatory_figure"
            for item in reevaluated
        )
        FigurePlanBuilder.save(str(work_dir), updated_plan)

        return FigureRequestReevaluation(
            requests=reevaluated,
            figure_plan_payload=updated_plan,
        )

    @staticmethod
    def render_required_requests(
        *,
        work_dir: str | Path,
        result_registry: dict[str, Any],
        required_requests: list[dict[str, Any]],
        chart_language: str,
        chart_images: list[str],
        structured_result_files: list[str],
        contract: ProblemContract,
    ) -> DeterministicRecoverySnapshot:
        report = RendererSkill.render_required_requests(
            work_dir=str(work_dir),
            result_registry=result_registry,
            required_requests=required_requests,
            chart_language=chart_language,
        ).to_dict()
        created_images = report.get("created_images", [])
        updated_chart_images = _unique_strings(chart_images, created_images)
        registry = ArtifactRegistry.load(
            str(work_dir),
            structured_result_files=structured_result_files,
            contract=contract,
        )
        artifact_valid_images = registry.validate_for_paper()
        return DeterministicRecoverySnapshot(
            report=report,
            chart_images=updated_chart_images,
            artifact_registry=registry,
            artifact_valid_images=artifact_valid_images,
        )
