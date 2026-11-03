"""figure request planning responsibilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role
from app.artifacts.visualization_protocol import VerifiedResult, result_overview_request
from app.artifacts.figure_need import _norm_text, _solve_spec_subproblems


EXPLANATORY_TEMPLATE_BY_NEED = {'physical_principle_schematic': ('physical_principle_schematic', True), 'optical_path_diagram': ('optical_path_diagram', True), 'geometry_schematic': ('geometry_schematic', True), 'timeline_schematic': ('timeline_schematic', True), 'algorithm_flowchart': ('algorithm_flowchart', True), 'trajectory_overview': ('trajectory_overview', True), 'optimization_variable_diagram': ('optimization_variable_diagram', False)}


RESULT_TEMPLATE_BY_NEED = {'spectrum_curve': 'spectrum_curve', 'peak_valley_annotation': 'peak_valley_annotation', 'fitting_curve': 'fitting_curve', 'residual_plot': 'residual_plot', 'result_data_plot': 'comparison_bar', 'comparison_bar': 'comparison_bar', 'data_overview': RESULT_OVERVIEW_ROLE, 'result_overview': RESULT_OVERVIEW_ROLE}


def _solve_spec_from_requests(requests: list[dict[str, Any]]) -> dict[str, Any]:
    subproblems: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in requests:
        if not isinstance(item, dict):
            continue
        sid = _norm_text(item.get("subproblem_id") or item.get("problem_section"))
        if not sid or sid in seen:
            continue
        seen.add(sid)
        data_files = []
        required_data = item.get("required_data", [])
        if isinstance(required_data, list):
            data_files = [str(src).strip() for src in required_data if str(src).strip()]
        subproblems.append(
            {
                "id": sid,
                "objective": _norm_text(item.get("purpose")) or sid,
                "input_files": [src for src in data_files if src != "result_registry.json"],
            }
        )
    if not subproblems:
        subproblems = [{"id": "problem_1", "objective": "problem", "input_files": []}]
    return {"subproblems": subproblems}


def _planning_basis_from_reasons(reasons: list[str]) -> list[str]:
    basis: list[str] = []
    mapping = {
        "problem_has_measurement_data": "problem_has_measurement_data",
        "solve_spec_has_input_files": "problem_has_measurement_data",
        "solve_spec_method_requires_explanatory_figure": "problem_has_physical_model",
        "solve_spec_method_requires_result_figure": "problem_has_algorithmic_process",
        "spectrum_columns_detected": "problem_has_measurement_data",
        "time_series_column_detected": "problem_has_algorithmic_process",
        "domain_template_optical_thin_film_interference": "problem_has_formula_derivation",
    }
    for reason in reasons:
        value = mapping.get(reason)
        if value:
            basis.append(value)
    return list(dict.fromkeys(basis))


def _seed_from_existing_requests(solve_spec: dict[str, Any], chart_language: str) -> list[dict[str, Any]]:
    existing = solve_spec.get("figure_requests", []) if isinstance(solve_spec, dict) else []
    if not isinstance(existing, list):
        return []
    seeded: list[dict[str, Any]] = []
    for item in existing:
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        payload.setdefault("chart_language", chart_language)
        payload.setdefault("language", chart_language)
        payload.setdefault("figure_kind", "result_figure")
        payload.setdefault("semantic_role", _norm_text(payload.get("role")) or "result_visualization")
        payload["semantic_role"] = normalize_figure_role(payload.get("semantic_role"))
        payload["role"] = payload["semantic_role"]
        payload.setdefault("must_include_in_paper", bool(payload.get("required", True)))
        payload.setdefault("depends_on", {
            "model_objects": [],
            "formulas": [],
            "data_files": payload.get("required_data", [] if not isinstance(payload.get("required_data"), list) else payload.get("required_data", [])),
            "result_ids": payload.get("linked_result_ids", []),
        })
        seeded.append(payload)
    return seeded


def _backfill_requests(requests: list[dict[str, Any]],
    solve_spec: dict[str, Any],
    chart_language: str,
    domain_type: str,
    detected_needs: list[str],
    available_data_files: list[str],
) -> list[dict[str, Any]]:
    existing_ids = {
        _norm_text(item.get("id"))
        for item in requests
        if isinstance(item, dict) and _norm_text(item.get("id"))
    }

    subproblems = _solve_spec_subproblems(solve_spec)
    if not subproblems:
        subproblems = [{"id": "problem_1", "objective": _norm_text(solve_spec.get("summary")) or "problem"}]

    def _sub_id(index: int, sub: dict[str, Any]) -> str:
        return _norm_text(sub.get("id")) or f"problem_{index + 1}"

    def _resolve_data_files(sub: dict[str, Any], prefer_spectrum: bool = False) -> list[str]:
        input_files = sub.get("input_files", [])
        names = [str(item).strip() for item in input_files if str(item).strip()] if isinstance(input_files, list) else []
        matched: list[str] = []
        for name in names:
            name_base = Path(name).name.lower()
            for rel in available_data_files:
                if Path(rel).name.lower() == name_base:
                    matched.append(rel)
        if not matched:
            matched = [rel for rel in available_data_files if rel.lower().endswith((".csv", ".xlsx", ".xls"))]
        if prefer_spectrum:
            spectral = [rel for rel in matched if any(token in rel.lower() for token in ["spectrum", "光谱", "波数", "reflectance"])]
            if spectral:
                matched = spectral
        if "result_registry.json" not in matched:
            matched.append("result_registry.json")
        return list(dict.fromkeys(matched))

    def _mk_request(
        req_id: str,
        subproblem_id: str,
        figure_kind: str,
        semantic_role: str,
        purpose: str,
        required: bool,
        depends_on: dict[str, Any],
    ) -> dict[str, Any]:
        chart_intent = purpose
        required_columns: list[str] = []
        x_axis = ""
        y_axis = ""
        group_by = ""
        if semantic_role == "spectrum_curve":
            required_columns = ["wavenumber", "reflectance"]
            x_axis = "wavenumber"
            y_axis = "reflectance"
        elif semantic_role == "fitting_curve":
            required_columns = ["x", "observed", "fitted"]
            x_axis = "x"
            y_axis = "observed"
        elif semantic_role == "residual_plot":
            required_columns = ["x", "residual"]
            x_axis = "x"
            y_axis = "residual"
        elif semantic_role == "comparison_bar":
            required_columns = ["method", "metric", "value"]
            x_axis = "method"
            y_axis = "value"
            group_by = "metric"
        elif semantic_role == RESULT_OVERVIEW_ROLE:
            required_columns = []
            x_axis = "variables"
            y_axis = "numeric_value"
            group_by = ""
        canonical_caption = canonical_caption_for_role(semantic_role, chart_language)
        return {
            "id": req_id,
            "problem_section": subproblem_id,
            "subproblem_id": subproblem_id,
            "figure_kind": figure_kind,
            "semantic_role": semantic_role,
            "role": semantic_role,
            "canonical_caption": canonical_caption,
            "purpose": purpose,
            "chart_intent": chart_intent,
            "required": required,
            "must_include_in_paper": bool(required),
            "chart_language": chart_language,
            "language": chart_language,
            "required_columns": required_columns,
            "x_axis": x_axis,
            "y_axis": y_axis,
            "group_by": group_by,
            "view_type": "numeric_overview" if semantic_role == RESULT_OVERVIEW_ROLE else "",
            "depends_on": depends_on,
            "required_data": list(depends_on.get("data_files", []) or []),
            "linked_result_ids": list(depends_on.get("result_ids", []) or []),
            "expected_content": [purpose],
        }

    for i, sub in enumerate(subproblems):
        sid = _sub_id(i, sub)
        objective = _norm_text(sub.get("objective")) or sid

        # Domain-first template for optical interference
        if domain_type == "optical_thin_film_interference":
            optical_templates = [
                ("optical_path_diagram", "explanatory_figure", "说明干涉光程差关系", True),
                ("spectrum_curve", "result_figure", "展示反射率-波数光谱曲线", True),
                ("peak_valley_annotation", "result_figure", "标注峰谷并支撑厚度估计", True),
                ("algorithm_flowchart", "explanatory_figure", "说明厚度求解与复核流程", True),
            ]
            for role, kind, purpose, required in optical_templates:
                req_id = f"fig_{sid}_{role}"
                if req_id in existing_ids:
                    continue
                existing_ids.add(req_id)
                requests.append(
                    _mk_request(
                        req_id=req_id,
                        subproblem_id=sid,
                        figure_kind=kind,
                        semantic_role=role,
                        purpose=f"{objective}：{purpose}",
                        required=required,
                        depends_on={
                            "model_objects": ["epitaxial_layer", "substrate", "incident_beam", "reflected_beam"] if kind == "explanatory_figure" else [],
                            "formulas": ["2 n d cos(theta) = m lambda"] if role in {"optical_path_diagram", "peak_valley_annotation"} else [],
                            "data_files": ["result_registry.json"] if kind == "explanatory_figure" else _resolve_data_files(sub, prefer_spectrum=True),
                            "result_ids": [] if kind == "explanatory_figure" else ["thickness_estimate", "peak_wavenumbers"],
                        },
                    )
                )

        # Generic detected-needs backfill
        for need in detected_needs:
            if need in EXPLANATORY_TEMPLATE_BY_NEED:
                role, required = EXPLANATORY_TEMPLATE_BY_NEED[need]
                req_id = f"fig_{sid}_{role}"
                if req_id in existing_ids:
                    continue
                existing_ids.add(req_id)
                requests.append(
                    _mk_request(
                        req_id=req_id,
                        subproblem_id=sid,
                        figure_kind="explanatory_figure",
                        semantic_role=role,
                        purpose=f"{objective}：{role}",
                        required=required,
                        depends_on={
                            "model_objects": ["model_core"],
                            "formulas": [],
                            "data_files": ["result_registry.json"],
                            "result_ids": [],
                        },
                    )
                )
            elif need in RESULT_TEMPLATE_BY_NEED:
                role = RESULT_TEMPLATE_BY_NEED[need]
                req_id = f"fig_{sid}_{role}"
                if req_id in existing_ids:
                    continue
                existing_ids.add(req_id)
                requests.append(
                    _mk_request(
                        req_id=req_id,
                        subproblem_id=sid,
                        figure_kind="result_figure",
                        semantic_role=role,
                        purpose=f"{objective}：{role}",
                        required=True,
                        depends_on={
                            "model_objects": [],
                            "formulas": [],
                            "data_files": _resolve_data_files(sub, prefer_spectrum=(role in {"spectrum_curve", "peak_valley_annotation"})),
                            "result_ids": [],
                        },
                    )
                )

    return requests


def _inject_fallback_data_overview_requests(requests: list[dict[str, Any]],
    solve_spec: dict[str, Any],
    chart_language: str,
    available_data_files: list[str],
    verified_results: list[VerifiedResult] | None = None,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper for the canonical result overview fallback."""
    return _inject_fallback_result_overview_requests(
        requests=requests,
        solve_spec=solve_spec,
        chart_language=chart_language,
        available_data_files=available_data_files,
        verified_results=verified_results,
    )


def _inject_fallback_result_overview_requests(requests: list[dict[str, Any]],
    solve_spec: dict[str, Any],
    chart_language: str,
    available_data_files: list[str],
    verified_results: list[VerifiedResult] | None = None,
) -> list[dict[str, Any]]:
    existing_ids = {
        _norm_text(item.get("id"))
        for item in requests
        if isinstance(item, dict) and _norm_text(item.get("id"))
    }
    existing_overview = next(
        (
            item for item in requests
            if isinstance(item, dict)
            and _norm_text(item.get("semantic_role") or item.get("role")).lower() != "data_overview"
            and normalize_figure_role(item.get("semantic_role") or item.get("role")) == RESULT_OVERVIEW_ROLE
        ),
        None,
    )
    if existing_overview is not None:
        return _collapse_result_overview_requests(requests)

    subproblems = _solve_spec_subproblems(solve_spec)
    if not subproblems:
        subproblems = [{"id": "problem_1", "objective": "problem"}]

    def _resolve_data_files(sub: dict[str, Any]) -> list[str]:
        input_files = sub.get("input_files", [])
        names = [str(item).strip() for item in input_files if str(item).strip()] if isinstance(input_files, list) else []
        matched: list[str] = []
        for name in names:
            name_base = Path(name).name.lower()
            for rel in available_data_files:
                if Path(rel).name.lower() == name_base:
                    matched.append(rel)
        if not matched:
            matched = [rel for rel in available_data_files if rel.lower().endswith((".csv", ".xlsx", ".xls"))]
        if "result_registry.json" not in matched:
            matched.append("result_registry.json")
        return list(dict.fromkeys(matched))

    for idx, sub in enumerate(subproblems, start=1):
        sid = _norm_text(sub.get("id")) or f"problem_{idx}"
        req_id = f"fig_{sid}_{RESULT_OVERVIEW_ROLE}"
        if req_id in existing_ids:
            continue
        objective = _norm_text(sub.get("objective")) or sid
        caption = canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language)
        data_files = _resolve_data_files(sub)
        matched_result = next(
            (
                result for result in (verified_results or [])
                if (result.section and result.section == sid)
                or (not result.section and idx == 1)
            ),
            None,
        )
        result_ids_for_overview = [
            result.id
            for result in (verified_results or [])
            if result.id and (
                (result.section and result.section == sid)
                or (not result.section and idx == 1)
            )
        ]
        if not result_ids_for_overview and idx == 1:
            result_ids_for_overview = [
                result.id for result in (verified_results or []) if result.id
            ][:12]
        if matched_result is not None or result_ids_for_overview:
            overview_result = matched_result or next(
                (result for result in (verified_results or []) if result.id),
                None,
            )
            if overview_result is None:
                continue
            request = result_overview_request(
                result=overview_result,
                chart_language=chart_language,
                data_files=data_files,
            )
            request["id"] = req_id
            request["problem_section"] = sid
            request["subproblem_id"] = sid
            request["purpose"] = f"{objective}: {caption}"
            request["depends_on"]["result_ids"] = result_ids_for_overview
            request["linked_result_ids"] = list(request["depends_on"]["result_ids"])
            requests.append(request)
            existing_ids.add(req_id)
            continue
        if verified_results:
            continue
        requests.append(
            {
                "id": req_id,
                "problem_section": sid,
                "subproblem_id": sid,
                "figure_kind": "result_figure",
                "semantic_role": "data_overview",
                "role": "data_overview",
                "canonical_caption": caption,
                "purpose": f"{objective}: {caption}",
                "chart_intent": "numeric_overview",
                "required": True,
                "must_include_in_paper": True,
                "chart_language": chart_language,
                "language": chart_language,
                "required_columns": [],
                "x_axis": "variables",
                "y_axis": "numeric_value",
                "group_by": "",
                "view_type": "numeric_overview",
                "depends_on": {
                    "model_objects": [],
                    "formulas": [],
                    "data_files": data_files,
                    "result_ids": [],
                },
                "required_data": data_files,
                "linked_result_ids": [],
                "expected_content": [caption],
            }
        )
        existing_ids.add(req_id)
        break
    return _collapse_result_overview_requests(requests)


def _collapse_result_overview_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    overview: dict[str, Any] | None = None
    linked_ids: list[str] = []
    source_data: list[str] = []
    has_required_specific = any(
        isinstance(item, dict)
        and normalize_figure_role(item.get("semantic_role") or item.get("role")) != RESULT_OVERVIEW_ROLE
        and bool(item.get("required", True))
        and str(item.get("status") or "").strip() != "blocked"
        and not str(item.get("blocked_reason") or "").strip()
        for item in requests
    )
    for item in requests:
        if not isinstance(item, dict):
            continue
        raw_role = _norm_text(item.get("semantic_role") or item.get("role")).lower()
        role = "data_overview" if raw_role == "data_overview" else normalize_figure_role(raw_role)
        if role != RESULT_OVERVIEW_ROLE:
            output.append(item)
            continue
        if overview is None:
            overview = dict(item)
        for rid in item.get("linked_result_ids", []) or []:
            text = str(rid).strip()
            if text:
                linked_ids.append(text)
        for src in _coerce_text_list(item.get("required_data") or item.get("source_data")):
            text = str(src).strip()
            if text:
                source_data.append(text)
    if overview is not None:
        overview["id"] = f"fig_{RESULT_OVERVIEW_ROLE}"
        overview["problem_section"] = "paper"
        overview["subproblem_id"] = "paper"
        overview["required"] = not has_required_specific
        overview["must_include_in_paper"] = not has_required_specific
        if linked_ids:
            overview["linked_result_ids"] = list(dict.fromkeys(linked_ids))[:12]
            overview.setdefault("depends_on", {})["result_ids"] = list(overview["linked_result_ids"])
        if source_data:
            overview["required_data"] = list(dict.fromkeys(source_data))
            overview.setdefault("depends_on", {})["data_files"] = list(overview["required_data"])
        output.append(overview)
    return output


def _coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []
