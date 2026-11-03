"""renderer series responsibilities."""

from __future__ import annotations

from typing import Any
from app.core.skills.renderer_contracts import RendererResult
from app.core.skills.renderer_requests import _request_id_and_data
from app.core.figure_data import deterministic_named_columns_from_data_files, deterministic_series_from_data_files, deterministic_series_from_registry


def _load_spectral_series(
    work_dir: str,
    result_registry: dict[str, Any],
    request: dict[str, Any],
) -> tuple[str, list[str], dict[str, list[float]], list[str], list[float], list[float], list[str]] | RendererResult:

    req_id, required_data_list, columns, matched_sources = _load_request_columns(work_dir, request)
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

    x_values, y_values, series_sources = deterministic_series_from_data_files(
        work_dir,
        required_data_list,
    )
    if len(x_values) < 2:
        x_values, y_values, series_sources = deterministic_series_from_registry(
            result_registry,
            required_data_list,
        )
    if len(x_values) < 2:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "insufficient_data_series"}])

    return req_id, required_data_list, columns, matched_sources, x_values, y_values, series_sources


def _load_request_columns(work_dir: str, request: dict[str, Any]) -> tuple[str, list[str], dict[str, list[float]], list[str]]:

    req_id, required_data_list = _request_id_and_data(request)
    columns, matched_sources = deterministic_named_columns_from_data_files(
        work_dir,
        required_data_list,
    )
    return req_id, required_data_list, columns, matched_sources
