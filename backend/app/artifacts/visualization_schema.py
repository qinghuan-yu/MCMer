"""Visualization contract schema and result-type view policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, normalize_figure_role


VIEW_TYPE_ALIASES: dict[str, str] = {
    "scatter_with_fit": "fitting_curve",
}

RESULT_TYPE_VIEW_MAP: dict[str, list[str]] = {
    "regression": ["scatter_with_fit", "residual_plot", "coefficient_plot"],
    "regression_model": ["scatter_with_fit", "residual_plot", "coefficient_plot"],
    "classification": ["confusion_matrix", "feature_importance", "roc_curve"],
    "classification_model": ["confusion_matrix", "feature_importance", "roc_curve"],
    "optimization": ["convergence_curve", "sensitivity_curve", "decision_variable_bar"],
    "optimization_result": ["convergence_curve", "sensitivity_curve", "decision_variable_bar"],
    "grouped_decision_result": ["group_boxplot", "group_bar", "risk_curve"],
    "descriptive_statistics": ["histogram", "boxplot", "correlation_heatmap"],
    "statistical_result": ["histogram", "boxplot", "correlation_heatmap"],
    "unknown": [RESULT_OVERVIEW_ROLE],
}

SUPPORTED_RENDERED_VIEWS: set[str] = {
    RESULT_OVERVIEW_ROLE,
    "comparison_bar",
    "fitting_curve",
    "peak_valley_annotation",
    "residual_plot",
    "spectrum_curve",
}


def normalize_view_type(view_type: object) -> str:
    role = normalize_figure_role(view_type)
    return VIEW_TYPE_ALIASES.get(role, role)


def supported_views_for_result_type(result_type: str) -> list[str]:
    raw_views = RESULT_TYPE_VIEW_MAP.get(result_type, RESULT_TYPE_VIEW_MAP["unknown"])
    views: list[str] = []
    for view in raw_views:
        normalized = normalize_view_type(view)
        if normalized in SUPPORTED_RENDERED_VIEWS and normalized not in views:
            views.append(normalized)
    if RESULT_OVERVIEW_ROLE not in views:
        views.append(RESULT_OVERVIEW_ROLE)
    return views


@dataclass(frozen=True)
class VisualizationContract:
    result_id: str
    candidate_views: list[str] = field(default_factory=list)
    required_views: list[str] = field(default_factory=list)
    fallback_views: list[str] = field(default_factory=lambda: [RESULT_OVERVIEW_ROLE])
    visualization_exemption: str = ""

    @classmethod
    def from_legacy(cls, payload: dict[str, Any], result_id: str = "") -> "VisualizationContract":
        return cls(
            result_id=str(payload.get("result_id") or result_id).strip(),
            candidate_views=[
                normalize_view_type(item)
                for item in payload.get("candidate_views", []) or []
                if str(item).strip()
            ],
            required_views=[
                normalize_view_type(item)
                for item in payload.get("required_views", []) or []
                if str(item).strip()
            ],
            fallback_views=[
                normalize_view_type(item)
                for item in payload.get("fallback_views", []) or []
                if str(item).strip()
            ] or [RESULT_OVERVIEW_ROLE],
            visualization_exemption=str(payload.get("visualization_exemption") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
