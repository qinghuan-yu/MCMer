"""Figure request reevaluation and deterministic recovery stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.artifacts.contracts import ProblemContract
from app.artifacts.figure_plan import FigurePlanBuilder
from app.artifacts.registry import ArtifactRegistry
from app.core.figure_stage import FigureStage
from app.core.skills.renderer import RendererSkill
from app.utils.common_utils import save_json


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
    def reevaluate_solve_spec_requests(
        *,
        work_dir: str | Path,
        solve_spec: dict[str, Any],
        chart_language: str,
        solve_spec_path: str | Path,
        figure_plan_payload: dict[str, Any] | None = None,
    ) -> FigureRequestReevaluation:
        reevaluated = FigureStage.reevaluate_solve_spec_requests(
            solve_spec=solve_spec,
            chart_language=chart_language,
            work_dir=str(work_dir),
        )
        if not reevaluated:
            return FigureRequestReevaluation()

        save_json(solve_spec, str(solve_spec_path))
        updated_plan = dict(figure_plan_payload or {})
        if updated_plan:
            updated_plan["figure_requests"] = reevaluated
            updated_plan["required_feasible_count"] = solve_spec.get("required_feasible_count", 0)
            updated_plan["blocked_count"] = solve_spec.get("blocked_count", 0)
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
