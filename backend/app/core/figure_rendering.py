"""figure rendering responsibilities."""

from __future__ import annotations

from pathlib import Path
from app.artifacts.schematic_renderers import render_algorithm_flowchart, render_geometry_schematic, render_timeline_schematic, render_trajectory_shielding_2d
from app.artifacts.chart_renderers import render_comparison_bar, render_data_overview, render_distance_time_curve, render_fitting_curve, render_peak_valley_annotation, render_residual_plot, render_spectrum_curve
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role
from app.core.figure_data import deterministic_named_columns_from_data_files, deterministic_overview_columns_from_registry, deterministic_series_from_data_files, deterministic_series_from_registry, extract_named_series, has_semantic_required_data_contract


def render_required_requests_deterministically(
    work_dir: str,
    result_registry: dict[str, object],
    required_requests: list[dict[str, object]],
    chart_language: str,
) -> dict[str, object]:
    """Compatibility renderer entrypoint.

        New workflow code should call RendererSkill.render_required_requests().
        This method remains while per-view renderers are migrated out of
        FigureStage.
        """
    created_images: list[str] = []
    satisfied: list[dict[str, object]] = []
    missing: list[dict[str, object]] = []

    for req in required_requests:
        req_id = str(req.get("id") or "").strip()
        subproblem_id = str(req.get("subproblem_id") or "").strip()
        raw_role = str(req.get("semantic_role") or req.get("role") or "").strip().lower()
        role = "data_overview" if raw_role == "data_overview" else normalize_figure_role(raw_role)
        figure_kind = str(req.get("figure_kind") or "result_figure").strip().lower()
        expected_content = req.get("expected_content", [])
        required_data = req.get("required_data", [])
        required_data_list = [str(item).strip() for item in required_data if str(item).strip()] if isinstance(required_data, list) else []
        depends_on = req.get("depends_on", {}) if isinstance(req.get("depends_on"), dict) else {}
        model_objects = depends_on.get("model_objects", []) if isinstance(depends_on.get("model_objects"), list) else []
        formulas = depends_on.get("formulas", []) if isinstance(depends_on.get("formulas"), list) else []
        source_problem_refs = depends_on.get("source_problem_refs", []) if isinstance(depends_on.get("source_problem_refs"), list) else []

        if not req_id:
            continue
        if role not in {
            "trajectory_shielding",
            "distance_time_curve",
            "geometry_schematic",
            "physical_principle_schematic",
            "optical_path_diagram",
            "timeline_schematic",
            "trajectory_overview",
            "optimization_variable_diagram",
            "algorithm_flowchart",
            "spectrum_curve",
            "peak_valley_annotation",
            "fitting_curve",
            "residual_plot",
            "comparison_bar",
            "data_overview",
            RESULT_OVERVIEW_ROLE,
        }:
            missing.append({
                "figure_request_id": req_id,
                "required": True,
                "satisfied": False,
                "reason": f"unsupported_deterministic_role:{role or 'unknown'}",
            })
            continue

        if role in {"trajectory_shielding", "distance_time_curve"} and not has_semantic_required_data_contract(role, required_data_list):
            missing.append({
                "figure_request_id": req_id,
                "required": True,
                "satisfied": False,
                "reason": "insufficient_semantic_required_data_contract",
            })
            continue

        if required_data_list:
            missing_files = [src for src in required_data_list if not (Path(work_dir) / src).exists()]
            if missing_files and role not in {"spectrum_curve", "peak_valley_annotation", RESULT_OVERVIEW_ROLE}:
                missing.append({
                    "figure_request_id": req_id,
                    "required": True,
                    "satisfied": False,
                    "reason": f"required_data_missing:{missing_files}",
                })
                continue
            if role in {"spectrum_curve", "peak_valley_annotation"} and len(missing_files) == len(required_data_list):
                missing.append({
                    "figure_request_id": req_id,
                    "required": True,
                    "satisfied": False,
                    "reason": f"required_data_missing:{missing_files}",
                })
                continue

        x_values: list[float] = []
        y_values: list[float] = []
        matched_sources: list[str] = []
        observed_values: list[float] = []
        fitted_values: list[float] = []
        categories: list[str] = []
        comparison_values: list[float] = []
        registry_linked_ids: list[str] = []
        registry_sources: list[str] = []
        if role in {"trajectory_shielding", "distance_time_curve"}:
            x_values, y_values, matched_sources = deterministic_series_from_registry(
                result_registry,
                required_data_list,
            )
            if len(x_values) < 2:
                missing.append({
                    "figure_request_id": req_id,
                    "required": True,
                    "satisfied": False,
                    "reason": "insufficient_verified_numeric_series",
                })
                continue

        if role in {"spectrum_curve", "peak_valley_annotation"}:
            columns, _ = deterministic_named_columns_from_data_files(
                work_dir,
                required_data_list,
            )
            column_names = [str(name).strip() for name in columns.keys() if str(name).strip()]
            spectral_tokens = [
                "wavenumber", "reflectance", "spectrum", "intensity",
                "波数", "反射率", "光谱", "强度",
            ]
            if column_names and not any(
                any(token in name.lower() for token in spectral_tokens)
                for name in column_names
            ):
                missing.append({
                    "figure_request_id": req_id,
                    "required": True,
                    "satisfied": False,
                    "reason": "semantic_data_mismatch:spectrum_curve",
                })
                continue
            x_values, y_values, matched_sources = deterministic_series_from_data_files(
                work_dir,
                required_data_list,
            )
            if len(x_values) < 2:
                x_values, y_values, matched_sources = deterministic_series_from_registry(
                    result_registry,
                    required_data_list,
                )
            if len(x_values) < 2:
                missing.append({
                    "figure_request_id": req_id,
                    "required": True,
                    "satisfied": False,
                    "reason": "insufficient_data_series",
                })
                continue

        if role in {"fitting_curve", "residual_plot", "comparison_bar"}:
            columns, matched_sources = deterministic_named_columns_from_data_files(
                work_dir,
                required_data_list,
            )
            if not columns:
                missing.append({
                    "figure_request_id": req_id,
                    "required": True,
                    "satisfied": False,
                    "reason": "missing_source_data",
                })
                continue
            if role == "fitting_curve":
                x_values = extract_named_series(columns, ["x", "time", "波数", "wavenumber", "index"])
                observed_values = extract_named_series(columns, ["observed", "obs", "measured", "真实", "观测"])
                fitted_values = extract_named_series(columns, ["fitted", "fit", "pred", "预测", "拟合"])
                if not x_values and observed_values:
                    x_values = [float(i + 1) for i in range(len(observed_values))]
                if not x_values or not observed_values or not fitted_values:
                    missing.append({
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "missing_fitting_columns",
                    })
                    continue
                min_len = min(len(x_values), len(observed_values), len(fitted_values))
                x_values = x_values[:min_len]
                observed_values = observed_values[:min_len]
                fitted_values = fitted_values[:min_len]
            elif role == "residual_plot":
                x_values = extract_named_series(columns, ["x", "time", "波数", "wavenumber", "index"])
                y_values = extract_named_series(columns, ["residual", "res", "残差"])
                if not x_values and y_values:
                    x_values = [float(i + 1) for i in range(len(y_values))]
                if not x_values or not y_values:
                    missing.append({
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "missing_residual_columns",
                    })
                    continue
                min_len = min(len(x_values), len(y_values))
                x_values = x_values[:min_len]
                y_values = y_values[:min_len]
            else:
                value_col = extract_named_series(columns, ["value", "metric", "score", "误差", "指标"])
                if not value_col:
                    for _, values in columns.items():
                        if len(values) >= 2:
                            value_col = values
                            break
                if not value_col:
                    missing.append({
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "missing_comparison_columns",
                    })
                    continue
                comparison_values = value_col[:16]
                categories = [f"C{i + 1}" for i in range(len(comparison_values))]

        overview_columns: dict[str, list[float]] = {}
        if role in {RESULT_OVERVIEW_ROLE, "data_overview"}:
            columns, matched_sources = deterministic_named_columns_from_data_files(
                work_dir,
                required_data_list,
            )
            for name, values in columns.items():
                if not isinstance(values, list):
                    continue
                clean = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
                if len(clean) >= 2:
                    overview_columns[str(name)] = clean
            min_overview_columns = 1 if role == RESULT_OVERVIEW_ROLE else 2
            if len(overview_columns) < min_overview_columns:
                overview_columns, registry_linked_ids, registry_sources = deterministic_overview_columns_from_registry(result_registry)
                if registry_linked_ids:
                    matched_sources = registry_sources
                if len(overview_columns) < min_overview_columns:
                    missing.append({
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": f"insufficient_numeric_columns:{role}",
                    })
                    continue

        output_path = str(Path(work_dir) / "output" / f"{req_id}.png")
        title = (
            str(expected_content[0]).strip()
            if isinstance(expected_content, list) and expected_content and str(expected_content[0]).strip()
            else ("轨迹遮蔽过程" if chart_language == "Simplified Chinese" else "Trajectory Shielding")
        )

        if role == "trajectory_shielding":
            artifact = render_trajectory_shielding_2d(
                points=list(zip(x_values, y_values)),
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role="trajectory_shielding",
                depicts=expected_content if isinstance(expected_content, list) else ["trajectory_shielding"],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or (matched_sources or ["result_registry.json"]),
                work_dir=work_dir,
            )
        elif role == "distance_time_curve":
            artifact = render_distance_time_curve(
                times=x_values,
                distances=y_values,
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role="distance_time_curve",
                depicts=expected_content if isinstance(expected_content, list) else ["distance_time_curve"],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or (matched_sources or ["result_registry.json"]),
                work_dir=work_dir,
            )
        elif role in {
            "geometry_schematic",
            "trajectory_overview",
            "optimization_variable_diagram",
            "physical_principle_schematic",
            "optical_path_diagram",
        }:
            artifact = render_geometry_schematic(
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role=role,
                depicts=expected_content if isinstance(expected_content, list) else [role],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or ["result_registry.json"],
                model_objects=[str(item).strip() for item in model_objects if str(item).strip()] or ["model_core"],
                formulas=[str(item).strip() for item in formulas if str(item).strip()],
                source_problem_refs=[str(item).strip() for item in source_problem_refs if str(item).strip()] or ([subproblem_id] if subproblem_id else ["problem"]),
                work_dir=work_dir,
            )
        elif role == "timeline_schematic":
            artifact = render_timeline_schematic(
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role="timeline_schematic",
                depicts=expected_content if isinstance(expected_content, list) else ["timeline_schematic"],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or ["result_registry.json"],
                model_objects=[str(item).strip() for item in model_objects if str(item).strip()] or ["process_stage"],
                formulas=[str(item).strip() for item in formulas if str(item).strip()],
                source_problem_refs=[str(item).strip() for item in source_problem_refs if str(item).strip()] or ([subproblem_id] if subproblem_id else ["problem"]),
                work_dir=work_dir,
            )
        elif role == "algorithm_flowchart":
            artifact = render_algorithm_flowchart(
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role="algorithm_flowchart",
                depicts=expected_content if isinstance(expected_content, list) else ["algorithm_flowchart"],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or ["result_registry.json"],
                model_objects=[str(item).strip() for item in model_objects if str(item).strip()] or ["algorithm_pipeline"],
                formulas=[str(item).strip() for item in formulas if str(item).strip()],
                source_problem_refs=[str(item).strip() for item in source_problem_refs if str(item).strip()] or ([subproblem_id] if subproblem_id else ["problem"]),
                work_dir=work_dir,
            )
        elif role == "spectrum_curve":
            columns, _ = deterministic_named_columns_from_data_files(
                work_dir,
                required_data_list,
            )
            artifact = render_spectrum_curve(
                x_values=x_values,
                y_values=y_values,
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role="spectrum_curve",
                depicts=expected_content if isinstance(expected_content, list) else ["spectrum_curve"],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or (matched_sources or ["result_registry.json"]),
                source_columns=list(columns.keys()),
                work_dir=work_dir,
            )
        elif role == "fitting_curve":
            artifact = render_fitting_curve(
                x_values=x_values,
                observed_values=observed_values,
                fitted_values=fitted_values,
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role="fitting_curve",
                depicts=expected_content if isinstance(expected_content, list) else ["fitting_curve"],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or (matched_sources or ["result_registry.json"]),
                work_dir=work_dir,
            )
        elif role == "residual_plot":
            artifact = render_residual_plot(
                x_values=x_values,
                residual_values=y_values,
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role="residual_plot",
                depicts=expected_content if isinstance(expected_content, list) else ["residual_plot"],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or (matched_sources or ["result_registry.json"]),
                work_dir=work_dir,
            )
        elif role == "comparison_bar":
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
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or (matched_sources or ["result_registry.json"]),
                work_dir=work_dir,
            )
        elif role in {RESULT_OVERVIEW_ROLE, "data_overview"}:
            request_linked_ids = [
                str(item).strip()
                for item in (req.get("linked_result_ids", []) if isinstance(req.get("linked_result_ids"), list) else [])
                if str(item).strip()
            ]
            if not request_linked_ids:
                request_linked_ids = registry_linked_ids
            if not request_linked_ids:
                request_linked_ids = [
                    str(item.get("id") or "").strip()
                    for item in (result_registry.get("verified_results", []) if isinstance(result_registry, dict) else [])
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ][:12]
            if not request_linked_ids and role == RESULT_OVERVIEW_ROLE:
                missing.append({
                    "figure_request_id": req_id,
                    "required": True,
                    "satisfied": False,
                    "reason": "missing_verified_result_binding",
                })
                continue
            artifact = render_data_overview(
                numeric_columns=overview_columns,
                title=canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language),
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role=role,
                depicts=[role, "numeric_overview"],
                linked_result_ids=request_linked_ids[:12],
                source_data=matched_sources or required_data_list or ["result_registry.json"],
                view_type=str(req.get("view_type") or "numeric_overview"),
                work_dir=work_dir,
            )
        else:
            artifact = render_peak_valley_annotation(
                x_values=x_values,
                y_values=y_values,
                title=title,
                output_path=output_path,
                chart_language=chart_language,
                figure_request_id=req_id,
                subproblem_id=subproblem_id,
                semantic_role="peak_valley_annotation",
                depicts=expected_content if isinstance(expected_content, list) else ["peak_valley_annotation"],
                linked_result_ids=[str(item.get("id") or "").strip() for item in (result_registry.get("verified_results", []) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()][:12],
                source_data=required_data_list or (matched_sources or ["result_registry.json"]),
                work_dir=work_dir,
            )

        if isinstance(artifact, dict):
            artifact["figure_kind"] = figure_kind or "result_figure"

        artifact_path = str(artifact.get("path") or "").strip() if isinstance(artifact, dict) else ""
        if artifact_path:
            created_images.append(artifact_path)
            satisfied.append({
                "figure_request_id": req_id,
                "required": True,
                "satisfied": True,
            })
        else:
            missing.append({
                "figure_request_id": req_id,
                "required": True,
                "satisfied": False,
                "reason": "renderer_failed",
            })

    return {
        "created_images": list(dict.fromkeys(created_images)),
        "satisfied": satisfied,
        "missing": missing,
    }
