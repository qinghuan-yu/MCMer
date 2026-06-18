"""Deterministic renderer skill.

This skill is the stable boundary for turning FigureRequest artifacts into
FigureArtifact records.  It currently delegates to the existing FigureStage
implementation while the per-view renderers are migrated behind this facade.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role
from app.artifacts.renderers import (
    render_algorithm_flowchart,
    render_comparison_bar,
    render_data_overview,
    render_distance_time_curve,
    render_fitting_curve,
    render_peak_valley_annotation,
    render_geometry_schematic,
    render_residual_plot,
    render_spectrum_curve,
    render_timeline_schematic,
    render_trajectory_shielding_2d,
)


@dataclass(frozen=True)
class RendererResult:
    created_images: list[str] = field(default_factory=list)
    satisfied: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    renderer: str = "RendererSkill"

    @classmethod
    def from_legacy(cls, payload: dict[str, Any]) -> "RendererResult":
        return cls(
            created_images=[
                str(item).strip()
                for item in (payload.get("created_images", []) if isinstance(payload, dict) else [])
                if str(item).strip()
            ],
            satisfied=[
                item for item in (payload.get("satisfied", []) if isinstance(payload, dict) else [])
                if isinstance(item, dict)
            ],
            missing=[
                item for item in (payload.get("missing", []) if isinstance(payload, dict) else [])
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
                rendered = RendererSkill._render_comparison_bar(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "fitting_curve":
                rendered = RendererSkill._render_fitting_curve(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "residual_plot":
                rendered = RendererSkill._render_residual_plot(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "spectrum_curve":
                rendered = RendererSkill._render_spectrum_curve(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "peak_valley_annotation":
                rendered = RendererSkill._render_peak_valley_annotation(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "trajectory_shielding":
                rendered = RendererSkill._render_trajectory_shielding(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "distance_time_curve":
                rendered = RendererSkill._render_distance_time_curve(
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
                rendered = RendererSkill._render_geometry_schematic(
                    work_dir=work_dir,
                    request=request,
                    chart_language=chart_language,
                    role=role,
                )
            elif role == "timeline_schematic":
                rendered = RendererSkill._render_timeline_schematic(
                    work_dir=work_dir,
                    request=request,
                    chart_language=chart_language,
                )
            elif role == "algorithm_flowchart":
                rendered = RendererSkill._render_algorithm_flowchart(
                    work_dir=work_dir,
                    request=request,
                    chart_language=chart_language,
                )
            else:
                rendered = RendererSkill._render_result_overview(
                    work_dir=work_dir,
                    result_registry=result_registry,
                    request=request,
                    chart_language=chart_language,
                )
            created_images.extend(rendered.created_images)
            satisfied.extend(rendered.satisfied)
            missing.extend(rendered.missing)

        if legacy_requests:
            legacy = RendererSkill._render_legacy_requests(
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

    @staticmethod
    def _render_legacy_requests(
        work_dir: str,
        result_registry: dict[str, Any],
        required_requests: list[dict[str, Any]],
        chart_language: str,
    ) -> RendererResult:
        # Imported lazily to avoid a circular import while FigureStage still
        # owns the legacy renderer implementation.
        from app.core.figure_stage import FigureStage

        return RendererResult.from_legacy(
            FigureStage.render_required_requests_deterministically(
                work_dir=work_dir,
                result_registry=result_registry,
                required_requests=required_requests,
                chart_language=chart_language,
            )
        )

    @staticmethod
    def _render_result_overview(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        from app.core.figure_stage import FigureStage

        req_id = str(request.get("id") or "").strip()
        if not req_id:
            return RendererResult(
                missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}]
            )

        required_data = request.get("required_data", [])
        required_data_list = [
            str(item).strip()
            for item in required_data
            if str(item).strip()
        ] if isinstance(required_data, list) else []

        columns, matched_sources = FigureStage.deterministic_named_columns_from_data_files(
            work_dir,
            required_data_list,
        )
        overview_columns: dict[str, list[float]] = {}
        for name, values in columns.items():
            if not isinstance(values, list):
                continue
            clean = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            if clean:
                overview_columns[str(name)] = clean

        registry_linked_ids: list[str] = []
        registry_sources: list[str] = []
        if not overview_columns:
            overview_columns, registry_linked_ids, registry_sources = FigureStage.deterministic_overview_columns_from_registry(result_registry)

        if not overview_columns:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": f"insufficient_numeric_columns:{RESULT_OVERVIEW_ROLE}",
                    }
                ]
            )

        request_linked_ids = RendererSkill._linked_result_ids(request, result_registry) or registry_linked_ids
        if not request_linked_ids:
            request_linked_ids = [
                str(item.get("id") or "").strip()
                for item in (result_registry.get("verified_results", []) if isinstance(result_registry, dict) else [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ][:12]
        if not request_linked_ids:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "missing_verified_result_binding",
                    }
                ]
            )

        subproblem_id = str(request.get("subproblem_id") or request.get("problem_section") or "").strip()
        output_path = str(Path(work_dir) / "output" / f"{req_id}.png")
        artifact = render_data_overview(
            numeric_columns=overview_columns,
            title=canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language),
            output_path=output_path,
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=subproblem_id,
            semantic_role=RESULT_OVERVIEW_ROLE,
            depicts=[RESULT_OVERVIEW_ROLE, "numeric_overview"],
            linked_result_ids=request_linked_ids[:12],
            source_data=matched_sources or registry_sources or required_data_list or ["result_registry.json"],
            view_type=str(request.get("view_type") or "numeric_overview"),
            work_dir=work_dir,
        )
        artifact_path = str(artifact.get("path") or "").strip() if isinstance(artifact, dict) else ""
        if not artifact_path:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "renderer_failed",
                    }
                ]
            )
        return RendererResult(
            created_images=[artifact_path],
            satisfied=[{"figure_request_id": req_id, "required": True, "satisfied": True}],
        )

    @staticmethod
    def _render_comparison_bar(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        from app.core.figure_stage import FigureStage

        req_id = str(request.get("id") or "").strip()
        if not req_id:
            return RendererResult(
                missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}]
            )

        required_data = request.get("required_data", [])
        required_data_list = [
            str(item).strip()
            for item in required_data
            if str(item).strip()
        ] if isinstance(required_data, list) else []
        columns, matched_sources = FigureStage.deterministic_named_columns_from_data_files(
            work_dir,
            required_data_list,
        )
        if not columns:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "missing_source_data",
                    }
                ]
            )

        value_col = FigureStage.extract_named_series(columns, ["value", "metric", "score", "误差", "指标"])
        if not value_col:
            for _, values in columns.items():
                if len(values) >= 2:
                    value_col = values
                    break
        if not value_col:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "missing_comparison_columns",
                    }
                ]
            )

        categories = RendererSkill._comparison_categories(columns, len(value_col))
        comparison_values = value_col[:16]
        categories = categories[: len(comparison_values)]
        if len(categories) != len(comparison_values):
            categories = [f"C{i + 1}" for i in range(len(comparison_values))]

        request_linked_ids = RendererSkill._linked_result_ids(request, result_registry)
        if not request_linked_ids:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "missing_verified_result_binding",
                    }
                ]
            )

        expected_content = request.get("expected_content", [])
        title = (
            str(expected_content[0]).strip()
            if isinstance(expected_content, list) and expected_content and str(expected_content[0]).strip()
            else canonical_caption_for_role("comparison_bar", chart_language) or "comparison_bar"
        )
        subproblem_id = str(request.get("subproblem_id") or request.get("problem_section") or "").strip()
        output_path = str(Path(work_dir) / "output" / f"{req_id}.png")
        artifact = render_comparison_bar(
            categories=categories,
            values=comparison_values,
            title=title,
            output_path=output_path,
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=subproblem_id,
            semantic_role="comparison_bar",
            depicts=expected_content if isinstance(expected_content, list) else ["comparison_bar"],
            linked_result_ids=request_linked_ids[:12],
            source_data=required_data_list or matched_sources or ["result_registry.json"],
            work_dir=work_dir,
        )
        artifact_path = str(artifact.get("path") or "").strip() if isinstance(artifact, dict) else ""
        if not artifact_path:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "renderer_failed",
                    }
                ]
            )
        return RendererResult(
            created_images=[artifact_path],
            satisfied=[{"figure_request_id": req_id, "required": True, "satisfied": True}],
        )

    @staticmethod
    def _render_fitting_curve(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        from app.core.figure_stage import FigureStage

        req_id, required_data_list, columns, matched_sources = RendererSkill._load_request_columns(work_dir, request)
        if not req_id:
            return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])
        if not columns:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_source_data"}])

        x_values = FigureStage.extract_named_series(columns, ["x", "time", "波数", "wavenumber", "index"])
        observed_values = FigureStage.extract_named_series(columns, ["observed", "obs", "measured", "真实", "观测"])
        fitted_values = FigureStage.extract_named_series(columns, ["fitted", "fit", "pred", "预测", "拟合"])
        if not x_values and observed_values:
            x_values = [float(i + 1) for i in range(len(observed_values))]
        if not x_values or not observed_values or not fitted_values:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_fitting_columns"}])
        min_len = min(len(x_values), len(observed_values), len(fitted_values))
        x_values = x_values[:min_len]
        observed_values = observed_values[:min_len]
        fitted_values = fitted_values[:min_len]

        linked_ids = RendererSkill._linked_result_ids(request, result_registry)
        if not linked_ids:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

        artifact = render_fitting_curve(
            x_values=x_values,
            observed_values=observed_values,
            fitted_values=fitted_values,
            title=RendererSkill._request_title(request, "fitting_curve", chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role="fitting_curve",
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["fitting_curve"],
            linked_result_ids=linked_ids[:12],
            source_data=required_data_list or matched_sources or ["result_registry.json"],
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _render_residual_plot(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        from app.core.figure_stage import FigureStage

        req_id, required_data_list, columns, matched_sources = RendererSkill._load_request_columns(work_dir, request)
        if not req_id:
            return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])
        if not columns:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_source_data"}])

        x_values = FigureStage.extract_named_series(columns, ["x", "time", "波数", "wavenumber", "index"])
        residual_values = FigureStage.extract_named_series(columns, ["residual", "res", "残差"])
        if not x_values and residual_values:
            x_values = [float(i + 1) for i in range(len(residual_values))]
        if not x_values or not residual_values:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_residual_columns"}])
        min_len = min(len(x_values), len(residual_values))
        x_values = x_values[:min_len]
        residual_values = residual_values[:min_len]

        linked_ids = RendererSkill._linked_result_ids(request, result_registry)
        if not linked_ids:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

        artifact = render_residual_plot(
            x_values=x_values,
            residual_values=residual_values,
            title=RendererSkill._request_title(request, "residual_plot", chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role="residual_plot",
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["residual_plot"],
            linked_result_ids=linked_ids[:12],
            source_data=required_data_list or matched_sources or ["result_registry.json"],
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _render_spectrum_curve(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        loaded = RendererSkill._load_spectral_series(
            work_dir,
            result_registry,
            request,
        )
        if isinstance(loaded, RendererResult):
            return loaded
        req_id, required_data_list, columns, matched_sources, x_values, y_values, series_sources = loaded

        linked_ids = RendererSkill._linked_result_ids(request, result_registry)
        if not linked_ids:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

        artifact = render_spectrum_curve(
            x_values=x_values,
            y_values=y_values,
            title=RendererSkill._request_title(request, "spectrum_curve", chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role="spectrum_curve",
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["spectrum_curve"],
            linked_result_ids=linked_ids[:12],
            source_data=required_data_list or series_sources or matched_sources or ["result_registry.json"],
            source_columns=list(columns.keys()),
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _render_peak_valley_annotation(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        loaded = RendererSkill._load_spectral_series(
            work_dir,
            result_registry,
            request,
        )
        if isinstance(loaded, RendererResult):
            return loaded
        req_id, required_data_list, _columns, matched_sources, x_values, y_values, series_sources = loaded

        linked_ids = RendererSkill._linked_result_ids(request, result_registry)
        if not linked_ids:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

        artifact = render_peak_valley_annotation(
            x_values=x_values,
            y_values=y_values,
            title=RendererSkill._request_title(request, "peak_valley_annotation", chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role="peak_valley_annotation",
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["peak_valley_annotation"],
            linked_result_ids=linked_ids[:12],
            source_data=required_data_list or series_sources or matched_sources or ["result_registry.json"],
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _render_trajectory_shielding(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        from app.core.figure_stage import FigureStage

        req_id, required_data_list = RendererSkill._request_id_and_data(request)
        if not req_id:
            return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

        x_values, y_values, matched_sources = FigureStage.deterministic_series_from_registry(
            result_registry,
            required_data_list,
        )
        if len(x_values) < 2:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "insufficient_verified_numeric_series"}])

        linked_ids = RendererSkill._linked_result_ids(request, result_registry)
        if not linked_ids:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

        artifact = render_trajectory_shielding_2d(
            points=list(zip(x_values, y_values)),
            title=RendererSkill._request_title(request, "trajectory_shielding", chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role="trajectory_shielding",
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["trajectory_shielding"],
            linked_result_ids=linked_ids[:12],
            source_data=required_data_list or matched_sources or ["result_registry.json"],
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _render_distance_time_curve(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        from app.core.figure_stage import FigureStage

        req_id, required_data_list = RendererSkill._request_id_and_data(request)
        if not req_id:
            return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

        x_values, y_values, matched_sources = FigureStage.deterministic_series_from_registry(
            result_registry,
            required_data_list,
        )
        if len(x_values) < 2:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "insufficient_verified_numeric_series"}])

        linked_ids = RendererSkill._linked_result_ids(request, result_registry)
        if not linked_ids:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

        artifact = render_distance_time_curve(
            times=x_values,
            distances=y_values,
            title=RendererSkill._request_title(request, "distance_time_curve", chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role="distance_time_curve",
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["distance_time_curve"],
            linked_result_ids=linked_ids[:12],
            source_data=required_data_list or matched_sources or ["result_registry.json"],
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _render_geometry_schematic(
        work_dir: str,
        request: dict[str, Any],
        chart_language: str,
        role: str,
    ) -> RendererResult:
        req_id, required_data_list = RendererSkill._request_id_and_data(request)
        if not req_id:
            return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

        model_objects, formulas, source_refs = RendererSkill._model_contract(request)
        artifact = render_geometry_schematic(
            title=RendererSkill._request_title(request, role, chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role=role,
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else [role],
            linked_result_ids=[],
            source_data=required_data_list or ["result_registry.json"],
            model_objects=model_objects or ["model_core"],
            formulas=formulas,
            source_problem_refs=source_refs or ([RendererSkill._subproblem_id(request)] if RendererSkill._subproblem_id(request) else ["problem"]),
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _render_timeline_schematic(
        work_dir: str,
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        req_id, required_data_list = RendererSkill._request_id_and_data(request)
        if not req_id:
            return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

        model_objects, formulas, source_refs = RendererSkill._model_contract(request)
        artifact = render_timeline_schematic(
            title=RendererSkill._request_title(request, "timeline_schematic", chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role="timeline_schematic",
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["timeline_schematic"],
            linked_result_ids=[],
            source_data=required_data_list or ["result_registry.json"],
            model_objects=model_objects or ["process_stage"],
            formulas=formulas,
            source_problem_refs=source_refs or ([RendererSkill._subproblem_id(request)] if RendererSkill._subproblem_id(request) else ["problem"]),
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _render_algorithm_flowchart(
        work_dir: str,
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        req_id, required_data_list = RendererSkill._request_id_and_data(request)
        if not req_id:
            return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

        model_objects, formulas, source_refs = RendererSkill._model_contract(request)
        artifact = render_algorithm_flowchart(
            title=RendererSkill._request_title(request, "algorithm_flowchart", chart_language),
            output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=RendererSkill._subproblem_id(request),
            semantic_role="algorithm_flowchart",
            depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["algorithm_flowchart"],
            linked_result_ids=[],
            source_data=required_data_list or ["result_registry.json"],
            model_objects=model_objects or ["algorithm_pipeline"],
            formulas=formulas,
            source_problem_refs=source_refs or ([RendererSkill._subproblem_id(request)] if RendererSkill._subproblem_id(request) else ["problem"]),
            work_dir=work_dir,
        )
        return RendererSkill._artifact_result(req_id, artifact)

    @staticmethod
    def _comparison_categories(columns: dict[str, list[float]], count: int) -> list[str]:
        for name, values in columns.items():
            lowered = name.lower()
            if any(token in lowered for token in ["method", "category", "group", "class", "方案", "类别", "方法"]):
                labels = [str(value) for value in values[:count]]
                if labels:
                    return labels
        return [f"C{i + 1}" for i in range(count)]

    @staticmethod
    def _linked_result_ids(request: dict[str, Any], result_registry: dict[str, Any]) -> list[str]:
        return [
            str(item).strip()
            for item in (request.get("linked_result_ids", []) if isinstance(request.get("linked_result_ids"), list) else [])
            if str(item).strip()
        ] or [
            str(item.get("id") or "").strip()
            for item in (result_registry.get("verified_results", []) if isinstance(result_registry, dict) else [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ][:12]

    @staticmethod
    def _request_title(request: dict[str, Any], role: str, chart_language: str) -> str:
        expected_content = request.get("expected_content", [])
        if isinstance(expected_content, list) and expected_content and str(expected_content[0]).strip():
            return str(expected_content[0]).strip()
        return canonical_caption_for_role(role, chart_language) or role

    @staticmethod
    def _subproblem_id(request: dict[str, Any]) -> str:
        return RendererSkill._safe_subproblem_id(request.get("subproblem_id") or request.get("problem_section"))

    @staticmethod
    def _request_id_and_data(request: dict[str, Any]) -> tuple[str, list[str]]:
        role = normalize_figure_role(request.get("semantic_role") or request.get("role")) or "figure"
        req_id = RendererSkill._safe_request_id(request.get("id"), request.get("subproblem_id"), role)
        required_data = request.get("required_data", [])
        required_data_list = [
            str(item).strip()
            for item in required_data
            if str(item).strip()
        ] if isinstance(required_data, list) else []
        return req_id, required_data_list

    @staticmethod
    def _safe_request_id(raw_id: Any, raw_subproblem: Any, role: str) -> str:
        explicit = "" if RendererSkill._looks_serialized_mapping(raw_id) else RendererSkill._slug(raw_id)
        if explicit:
            return explicit
        subproblem_id = RendererSkill._safe_subproblem_id(raw_subproblem)
        role_slug = RendererSkill._slug(role) or "figure"
        return f"fig_{subproblem_id}_{role_slug}"

    @staticmethod
    def _looks_serialized_mapping(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        text = value.strip()
        return "{" in text and "}" in text and any(key in text for key in ("number", "title", "keywords", "'number'", '"number"'))

    @staticmethod
    def _safe_subproblem_id(raw: Any) -> str:
        if isinstance(raw, dict):
            number = str(raw.get("number") or raw.get("id") or raw.get("name") or "").strip()
            mapped = RendererSkill._problem_number_slug(number)
            if mapped:
                return mapped
            title = RendererSkill._slug(raw.get("title"))
            if title:
                return title
            return "problem"
        mapped = RendererSkill._problem_number_slug(str(raw or "").strip())
        if mapped:
            return mapped
        return RendererSkill._slug(raw) or "problem"

    @staticmethod
    def _problem_number_slug(text: str) -> str:
        if not text:
            return ""
        match = re.search(r"(?:problem|question|q)\s*([0-9]+)", text, re.IGNORECASE)
        if match:
            return f"problem_{match.group(1)}"
        match = re.search(r"第\s*([一二三四五六七八九十0-9]+)\s*[题問问]", text)
        if match:
            value = RendererSkill._chinese_number_to_int(match.group(1))
            if value:
                return f"problem_{value}"
        if text.strip().isdigit():
            return f"problem_{text.strip()}"
        return ""

    @staticmethod
    def _chinese_number_to_int(text: str) -> int:
        if text.isdigit():
            return int(text)
        digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if text == "十":
            return 10
        if text.startswith("十"):
            return 10 + digits.get(text[1:], 0)
        if "十" in text:
            left, right = text.split("十", 1)
            return digits.get(left, 0) * 10 + digits.get(right, 0)
        return digits.get(text, 0)

    @staticmethod
    def _slug(value: Any) -> str:
        if not isinstance(value, (str, int, float)):
            return ""
        text = str(value).strip().lower()
        if not text:
            return ""
        text = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_")
        return text

    @staticmethod
    def _model_contract(request: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
        depends_on = request.get("depends_on", {}) if isinstance(request.get("depends_on"), dict) else {}

        def _clean_list(key: str) -> list[str]:
            value = depends_on.get(key, [])
            if not isinstance(value, list):
                return []
            return [str(item).strip() for item in value if str(item).strip()]

        return _clean_list("model_objects"), _clean_list("formulas"), _clean_list("source_problem_refs")

    @staticmethod
    def _load_spectral_series(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
    ) -> tuple[str, list[str], dict[str, list[float]], list[str], list[float], list[float], list[str]] | RendererResult:
        from app.core.figure_stage import FigureStage

        req_id, required_data_list, columns, matched_sources = RendererSkill._load_request_columns(work_dir, request)
        if not req_id:
            return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

        column_names = [str(name).strip().lower() for name in columns.keys() if str(name).strip()]
        spectral_tokens = ["wavenumber", "reflectance", "spectrum", "intensity", "frequency", "wave"]
        request_context = " ".join(
            str(item or "")
            for item in [
                request.get("id"),
                request.get("purpose"),
                request.get("chart_intent"),
                request.get("semantic_role"),
                request.get("view_type"),
                " ".join(str(x) for x in request.get("expected_content", []) if str(x).strip())
                if isinstance(request.get("expected_content"), list)
                else "",
            ]
        ).lower()
        explicit_spectrum_request = any(token in request_context for token in spectral_tokens) or any(
            token in request_context for token in ["光谱", "波数", "反射率", "峰", "谷"]
        )
        has_numeric_pair = sum(1 for values in columns.values() if isinstance(values, list) and len(values) >= 2) >= 2
        if (
            column_names
            and not any(any(token in name for token in spectral_tokens) for name in column_names)
            and not (explicit_spectrum_request and has_numeric_pair)
        ):
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "semantic_data_mismatch:spectrum_curve"}])

        x_values, y_values, series_sources = FigureStage.deterministic_series_from_data_files(
            work_dir,
            required_data_list,
        )
        if len(x_values) < 2:
            x_values, y_values, series_sources = FigureStage.deterministic_series_from_registry(
                result_registry,
                required_data_list,
            )
        if len(x_values) < 2:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "insufficient_data_series"}])

        return req_id, required_data_list, columns, matched_sources, x_values, y_values, series_sources

    @staticmethod
    def _load_request_columns(work_dir: str, request: dict[str, Any]) -> tuple[str, list[str], dict[str, list[float]], list[str]]:
        from app.core.figure_stage import FigureStage

        req_id, required_data_list = RendererSkill._request_id_and_data(request)
        columns, matched_sources = FigureStage.deterministic_named_columns_from_data_files(
            work_dir,
            required_data_list,
        )
        return req_id, required_data_list, columns, matched_sources

    @staticmethod
    def _artifact_result(req_id: str, artifact: dict[str, Any]) -> RendererResult:
        artifact_path = str(artifact.get("path") or "").strip() if isinstance(artifact, dict) else ""
        if not artifact_path:
            return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "renderer_failed"}])
        return RendererResult(
            created_images=[artifact_path],
            satisfied=[{"figure_request_id": req_id, "required": True, "satisfied": True}],
        )
