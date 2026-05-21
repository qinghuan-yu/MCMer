"""Shared artifact/workflow protocol models.

This module centralizes protocol-level enums and registries so planning,
rendering, and gating do not drift on duplicated string rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


RESULT_OVERVIEW_ROLE = "result_overview"

FIGURE_ROLE_ALIASES = {
    "data_overview": RESULT_OVERVIEW_ROLE,
}


def normalize_figure_role(role: object) -> str:
    """Return the canonical semantic role used by figure planning/gating."""
    normalized = str(role or "").strip().lower()
    return FIGURE_ROLE_ALIASES.get(normalized, normalized)


def canonical_caption_for_role(role: object, chart_language: str = "Simplified Chinese") -> str:
    """Caption prefix controlled by protocol, not by the writer."""
    normalized = normalize_figure_role(role)
    if normalized == RESULT_OVERVIEW_ROLE:
        return "核心结果概览图" if chart_language == "Simplified Chinese" else "Core result overview"
    return str(role or "").strip()


class FigureRequestState(str, Enum):
    planned = "planned"
    pending_result_binding = "pending_result_binding"
    blocked = "blocked"
    required = "required"
    recommended = "recommended"
    rendered = "rendered"
    artifact_verified = "artifact_verified"
    bundled = "bundled"
    referenced = "referenced"
    exported = "exported"


@dataclass
class WorkflowIssue:
    code: str
    severity: str
    stage: str
    reason: str
    request_id: str = ""
    action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "stage": self.stage,
            "reason": self.reason,
            "request_id": self.request_id,
            "action": self.action,
        }


@dataclass(frozen=True)
class FigureCapability:
    role: str
    renderer: str
    requires_data: bool
    requires_verified_result: bool
    supports_languages: tuple[str, ...]
    domain_applicability: tuple[str, ...] = ()


DEFAULT_FIGURE_CAPABILITIES: dict[str, FigureCapability] = {
    "trajectory_shielding": FigureCapability(
        role="trajectory_shielding",
        renderer="render_trajectory_shielding_2d",
        requires_data=False,
        requires_verified_result=True,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "distance_time_curve": FigureCapability(
        role="distance_time_curve",
        renderer="render_distance_time_curve",
        requires_data=False,
        requires_verified_result=True,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "geometry_schematic": FigureCapability(
        role="geometry_schematic",
        renderer="render_geometry_schematic",
        requires_data=False,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "physical_principle_schematic": FigureCapability(
        role="physical_principle_schematic",
        renderer="render_geometry_schematic",
        requires_data=False,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "optical_path_diagram": FigureCapability(
        role="optical_path_diagram",
        renderer="render_geometry_schematic",
        requires_data=False,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "timeline_schematic": FigureCapability(
        role="timeline_schematic",
        renderer="render_timeline_schematic",
        requires_data=False,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "trajectory_overview": FigureCapability(
        role="trajectory_overview",
        renderer="render_geometry_schematic",
        requires_data=False,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "optimization_variable_diagram": FigureCapability(
        role="optimization_variable_diagram",
        renderer="render_geometry_schematic",
        requires_data=False,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "algorithm_flowchart": FigureCapability(
        role="algorithm_flowchart",
        renderer="render_algorithm_flowchart",
        requires_data=False,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "spectrum_curve": FigureCapability(
        role="spectrum_curve",
        renderer="render_spectrum_curve",
        requires_data=True,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
        domain_applicability=(
            "spectrum",
            "wavenumber",
            "reflectance",
            "intensity",
            "光谱",
            "波数",
            "反射率",
            "强度",
            "频谱",
            "signal",
        ),
    ),
    "peak_valley_annotation": FigureCapability(
        role="peak_valley_annotation",
        renderer="render_peak_valley_annotation",
        requires_data=True,
        requires_verified_result=False,
        supports_languages=("Simplified Chinese", "English"),
        domain_applicability=(
            "spectrum",
            "wavenumber",
            "reflectance",
            "intensity",
            "光谱",
            "波数",
            "反射率",
            "强度",
            "峰",
            "谷",
        ),
    ),
    "fitting_curve": FigureCapability(
        role="fitting_curve",
        renderer="render_fitting_curve",
        requires_data=True,
        requires_verified_result=True,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "residual_plot": FigureCapability(
        role="residual_plot",
        renderer="render_residual_plot",
        requires_data=True,
        requires_verified_result=True,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "comparison_bar": FigureCapability(
        role="comparison_bar",
        renderer="render_comparison_bar",
        requires_data=True,
        requires_verified_result=True,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "result_overview": FigureCapability(
        role="result_overview",
        renderer="render_data_overview",
        requires_data=False,
        requires_verified_result=True,
        supports_languages=("Simplified Chinese", "English"),
    ),
    "data_overview": FigureCapability(
        role="result_overview",
        renderer="render_data_overview",
        requires_data=False,
        requires_verified_result=True,
        supports_languages=("Simplified Chinese", "English"),
    ),
}
