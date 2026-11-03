"""renderer responsibilities."""

from __future__ import annotations

from typing import Any
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, normalize_figure_role
from app.core.skills.renderer_contracts import RendererResult
from app.core.skills.renderer_views import _render_algorithm_flowchart, _render_comparison_bar, _render_distance_time_curve, _render_fitting_curve, _render_geometry_schematic, _render_legacy_requests, _render_peak_valley_annotation, _render_residual_plot, _render_result_overview, _render_spectrum_curve, _render_timeline_schematic, _render_trajectory_shielding


class RendererSkill:
    """Render required figure requests through deterministic view renderers."""

    @staticmethod
    def render_required_requests(
        work_dir: str,
        result_registry: dict[str, Any],
        required_requests: list[dict[str, Any]],
        chart_language: str,
    ) -> RendererResult:
        overview_requests: list[dict[str, Any]] = []
        legacy_requests: list[dict[str, Any]] = []
        for request in required_requests:
            if not isinstance(request, dict):
                continue
            role = normalize_figure_role(request.get("semantic_role") or request.get("role"))
            if role == RESULT_OVERVIEW_ROLE:
                overview_requests.append(request)
            elif role in {
                "comparison_bar",
                "fitting_curve",
                "residual_plot",
                "spectrum_curve",
                "peak_valley_annotation",
                "trajectory_shielding",
                "distance_time_curve",
                "geometry_schematic",
                "trajectory_overview",
                "optimization_variable_diagram",
                "physical_principle_schematic",
                "optical_path_diagram",
                "timeline_schematic",
                "algorithm_flowchart",
            }:
                overview_requests.append(request)
            else:
                legacy_requests.append(request)

        created_images: list[str] = []
        satisfied: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []

        for request in overview_requests:
            role = normalize_figure_role(request.get("semantic_role") or request.get("role"))
            if role == "comparison_bar":
                rendered = _render_comparison_bar(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "fitting_curve":
                rendered = _render_fitting_curve(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "residual_plot":
                rendered = _render_residual_plot(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "spectrum_curve":
                rendered = _render_spectrum_curve(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "peak_valley_annotation":
                rendered = _render_peak_valley_annotation(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "trajectory_shielding":
                rendered = _render_trajectory_shielding(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "distance_time_curve":
                rendered = _render_distance_time_curve(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role in {
                "geometry_schematic",
                "trajectory_overview",
                "optimization_variable_diagram",
                "physical_principle_schematic",
                "optical_path_diagram",
            }:
                rendered = _render_geometry_schematic(
                    work_dir=work_dir,
                    request=request,
                    chart_language=chart_language,
                    role=role,
                )
            elif role == "timeline_schematic":
                rendered = _render_timeline_schematic(
                    work_dir=work_dir,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "algorithm_flowchart":
                rendered = _render_algorithm_flowchart(
                    work_dir=work_dir,
                    request=request,
                    chart_language=chart_language,
                )
            else:
                rendered = _render_result_overview(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            created_images.extend(rendered.created_images)
            satisfied.extend(rendered.satisfied)
            missing.extend(rendered.missing)

        if legacy_requests:
            legacy = _render_legacy_requests(
                work_dir=work_dir,
                result_registry=result_registry,
                required_requests=legacy_requests,
                chart_language=chart_language,
            )
            created_images.extend(legacy.created_images)
            satisfied.extend(legacy.satisfied)
            missing.extend(legacy.missing)

        return RendererResult(
            created_images=list(dict.fromkeys(created_images)),
            satisfied=satisfied,
            missing=missing,
        )
