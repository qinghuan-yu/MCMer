"""renderer views responsibilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role
from app.artifacts.schematic_renderers import render_algorithm_flowchart, render_geometry_schematic, render_timeline_schematic, render_trajectory_shielding_2d
from app.artifacts.chart_renderers import render_comparison_bar, render_data_overview, render_distance_time_curve, render_fitting_curve, render_peak_valley_annotation, render_residual_plot, render_spectrum_curve
from app.core.skills.renderer_contracts import RendererResult
from app.core.skills.renderer_requests import _artifact_result, _comparison_categories, _linked_result_ids, _model_contract, _request_id_and_data, _request_title, _subproblem_id
from app.core.skills.renderer_series import _load_request_columns, _load_spectral_series
from app.core.figure_data import deterministic_named_columns_from_data_files, deterministic_overview_columns_from_registry, deterministic_series_from_registry, extract_named_series
from app.core.figure_rendering import render_required_requests_deterministically


def _render_legacy_requests(
    work_dir: str,
    result_registry: dict[str, Any],
    required_requests: list[dict[str, Any]],
    chart_language: str,
) -> RendererResult:
    # Imported lazily to avoid a circular import while FigureStage still
    # owns the legacy renderer implementation.

    return RendererResult.from_legacy(
        render_required_requests_deterministically(
            work_dir=work_dir,
            result_registry=result_registry,
            required_requests=required_requests,
            chart_language=chart_language,
        )
    )


def _render_result_overview(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:

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

    columns, matched_sources = deterministic_named_columns_from_data_files(
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
        overview_columns, registry_linked_ids, registry_sources = deterministic_overview_columns_from_registry(result_registry)

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

    request_linked_ids = _linked_result_ids(request, result_registry) or registry_linked_ids
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


def _render_comparison_bar(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:

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
    columns, matched_sources = deterministic_named_columns_from_data_files(
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

    value_col = extract_named_series(columns, ["value", "metric", "score", "误差", "指标"])
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

    categories = _comparison_categories(columns, len(value_col))
    comparison_values = value_col[:16]
    categories = categories[: len(comparison_values)]
    if len(categories) != len(comparison_values):
        categories = [f"C{i + 1}" for i in range(len(comparison_values))]

    request_linked_ids = _linked_result_ids(request, result_registry)
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


def _render_fitting_curve(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:

    req_id, required_data_list, columns, matched_sources = _load_request_columns(work_dir, request)
    if not req_id:
        return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])
    if not columns:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_source_data"}])

    x_values = extract_named_series(columns, ["x", "time", "波数", "wavenumber", "index"])
    observed_values = extract_named_series(columns, ["observed", "obs", "measured", "真实", "观测"])
    fitted_values = extract_named_series(columns, ["fitted", "fit", "pred", "预测", "拟合"])
    if not x_values and observed_values:
        x_values = [float(i + 1) for i in range(len(observed_values))]
    if not x_values or not observed_values or not fitted_values:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_fitting_columns"}])
    min_len = min(len(x_values), len(observed_values), len(fitted_values))
    x_values = x_values[:min_len]
    observed_values = observed_values[:min_len]
    fitted_values = fitted_values[:min_len]

    linked_ids = _linked_result_ids(request, result_registry)
    if not linked_ids:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

    artifact = render_fitting_curve(
        x_values=x_values,
        observed_values=observed_values,
        fitted_values=fitted_values,
        title=_request_title(request, "fitting_curve", chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role="fitting_curve",
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["fitting_curve"],
        linked_result_ids=linked_ids[:12],
        source_data=required_data_list or matched_sources or ["result_registry.json"],
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)


def _render_residual_plot(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:

    req_id, required_data_list, columns, matched_sources = _load_request_columns(work_dir, request)
    if not req_id:
        return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])
    if not columns:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_source_data"}])

    x_values = extract_named_series(columns, ["x", "time", "波数", "wavenumber", "index"])
    residual_values = extract_named_series(columns, ["residual", "res", "残差"])
    if not x_values and residual_values:
        x_values = [float(i + 1) for i in range(len(residual_values))]
    if not x_values or not residual_values:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_residual_columns"}])
    min_len = min(len(x_values), len(residual_values))
    x_values = x_values[:min_len]
    residual_values = residual_values[:min_len]

    linked_ids = _linked_result_ids(request, result_registry)
    if not linked_ids:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

    artifact = render_residual_plot(
        x_values=x_values,
        residual_values=residual_values,
        title=_request_title(request, "residual_plot", chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role="residual_plot",
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["residual_plot"],
        linked_result_ids=linked_ids[:12],
        source_data=required_data_list or matched_sources or ["result_registry.json"],
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)


def _render_spectrum_curve(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:
    loaded = _load_spectral_series(
        work_dir,
        result_registry,
        request,
    )
    if isinstance(loaded, RendererResult):
        return loaded
    req_id, required_data_list, columns, matched_sources, x_values, y_values, series_sources = loaded

    linked_ids = _linked_result_ids(request, result_registry)
    if not linked_ids:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

    artifact = render_spectrum_curve(
        x_values=x_values,
        y_values=y_values,
        title=_request_title(request, "spectrum_curve", chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role="spectrum_curve",
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["spectrum_curve"],
        linked_result_ids=linked_ids[:12],
        source_data=required_data_list or series_sources or matched_sources or ["result_registry.json"],
        source_columns=list(columns.keys()),
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)


def _render_peak_valley_annotation(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:
    loaded = _load_spectral_series(
        work_dir,
        result_registry,
        request,
    )
    if isinstance(loaded, RendererResult):
        return loaded
    req_id, required_data_list, _columns, matched_sources, x_values, y_values, series_sources = loaded

    linked_ids = _linked_result_ids(request, result_registry)
    if not linked_ids:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

    artifact = render_peak_valley_annotation(
        x_values=x_values,
        y_values=y_values,
        title=_request_title(request, "peak_valley_annotation", chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role="peak_valley_annotation",
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["peak_valley_annotation"],
        linked_result_ids=linked_ids[:12],
        source_data=required_data_list or series_sources or matched_sources or ["result_registry.json"],
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)


def _render_trajectory_shielding(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:

    req_id, required_data_list = _request_id_and_data(request)
    if not req_id:
        return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

    x_values, y_values, matched_sources = deterministic_series_from_registry(
        result_registry,
        required_data_list,
    )
    if len(x_values) < 2:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "insufficient_verified_numeric_series"}])

    linked_ids = _linked_result_ids(request, result_registry)
    if not linked_ids:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

    artifact = render_trajectory_shielding_2d(
        points=list(zip(x_values, y_values)),
        title=_request_title(request, "trajectory_shielding", chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role="trajectory_shielding",
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["trajectory_shielding"],
        linked_result_ids=linked_ids[:12],
        source_data=required_data_list or matched_sources or ["result_registry.json"],
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)


def _render_distance_time_curve(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:

    req_id, required_data_list = _request_id_and_data(request)
    if not req_id:
        return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

    x_values, y_values, matched_sources = deterministic_series_from_registry(
        result_registry,
        required_data_list,
    )
    if len(x_values) < 2:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "insufficient_verified_numeric_series"}])

    linked_ids = _linked_result_ids(request, result_registry)
    if not linked_ids:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "missing_verified_result_binding"}])

    artifact = render_distance_time_curve(
        times=x_values,
        distances=y_values,
        title=_request_title(request, "distance_time_curve", chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role="distance_time_curve",
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["distance_time_curve"],
        linked_result_ids=linked_ids[:12],
        source_data=required_data_list or matched_sources or ["result_registry.json"],
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)


def _render_geometry_schematic(
    work_dir: str,
    request: dict[str, Any],
    chart_language: str,
    role: str,
) -> RendererResult:
    req_id, required_data_list = _request_id_and_data(request)
    if not req_id:
        return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

    model_objects, formulas, source_refs = _model_contract(request)
    artifact = render_geometry_schematic(
        title=_request_title(request, role, chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role=role,
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else [role],
        linked_result_ids=[],
        source_data=required_data_list or ["result_registry.json"],
        model_objects=model_objects or ["model_core"],
        formulas=formulas,
        source_problem_refs=source_refs or ([_subproblem_id(request)] if _subproblem_id(request) else ["problem"]),
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)


def _render_timeline_schematic(
    work_dir: str,
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:
    req_id, required_data_list = _request_id_and_data(request)
    if not req_id:
        return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

    model_objects, formulas, source_refs = _model_contract(request)
    artifact = render_timeline_schematic(
        title=_request_title(request, "timeline_schematic", chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role="timeline_schematic",
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["timeline_schematic"],
        linked_result_ids=[],
        source_data=required_data_list or ["result_registry.json"],
        model_objects=model_objects or ["process_stage"],
        formulas=formulas,
        source_problem_refs=source_refs or ([_subproblem_id(request)] if _subproblem_id(request) else ["problem"]),
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)


def _render_algorithm_flowchart(
    work_dir: str,
    request: dict[str, Any],
    chart_language: str,
) -> RendererResult:
    req_id, required_data_list = _request_id_and_data(request)
    if not req_id:
        return RendererResult(missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}])

    model_objects, formulas, source_refs = _model_contract(request)
    artifact = render_algorithm_flowchart(
        title=_request_title(request, "algorithm_flowchart", chart_language),
        output_path=str(Path(work_dir) / "output" / f"{req_id}.png"),
        chart_language=chart_language,
        figure_request_id=req_id,
        subproblem_id=_subproblem_id(request),
        semantic_role="algorithm_flowchart",
        depicts=request.get("expected_content") if isinstance(request.get("expected_content"), list) else ["algorithm_flowchart"],
        linked_result_ids=[],
        source_data=required_data_list or ["result_registry.json"],
        model_objects=model_objects or ["algorithm_pipeline"],
        formulas=formulas,
        source_problem_refs=source_refs or ([_subproblem_id(request)] if _subproblem_id(request) else ["problem"]),
        work_dir=work_dir,
    )
    return _artifact_result(req_id, artifact)
