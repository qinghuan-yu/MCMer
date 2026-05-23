"""Unified artifact protocol for MCMer.

The package exposes the historical convenience imports, but resolves them
lazy-on-access so lightweight schema/planner imports do not pull exporter,
settings, or optional document dependencies into every stage.
"""

from __future__ import annotations

from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "ArtifactRegistry": ("app.artifacts.registry", "ArtifactRegistry"),
    "ProblemContract": ("app.artifacts.contracts", "ProblemContract"),
    "DocumentFinalizer": ("app.artifacts.exporters", "DocumentFinalizer"),
    "FigureRequest": ("app.artifacts.figure_requests", "FigureRequest"),
    "FigureRequestPlanner": ("app.artifacts.figure_requests", "FigureRequestPlanner"),
    "render_bar_summary": ("app.artifacts.renderers", "render_bar_summary"),
    "render_distance_time_curve": ("app.artifacts.renderers", "render_distance_time_curve"),
    "render_line_chart": ("app.artifacts.renderers", "render_line_chart"),
    "render_scatter_plot": ("app.artifacts.renderers", "render_scatter_plot"),
    "render_table_figure": ("app.artifacts.renderers", "render_table_figure"),
    "render_trajectory_shielding_2d": ("app.artifacts.renderers", "render_trajectory_shielding_2d"),
    "render_verified_summary": ("app.artifacts.renderers", "render_verified_summary"),
    "GateReport": ("app.artifacts.diagnostics", "GateReport"),
    "FigureGateReport": ("app.artifacts.diagnostics", "FigureGateReport"),
    "QualityGateReport": ("app.artifacts.diagnostics", "QualityGateReport"),
    "BudgetReport": ("app.artifacts.diagnostics", "BudgetReport"),
    "WorkflowTrace": ("app.artifacts.diagnostics", "WorkflowTrace"),
    "FigureRequestState": ("app.artifacts.protocols", "FigureRequestState"),
    "WorkflowIssue": ("app.artifacts.protocols", "WorkflowIssue"),
    "FigureCapability": ("app.artifacts.protocols", "FigureCapability"),
    "DEFAULT_FIGURE_CAPABILITIES": ("app.artifacts.protocols", "DEFAULT_FIGURE_CAPABILITIES"),
    "VerifiedResult": ("app.artifacts.result_schema", "VerifiedResult"),
    "VisualizationContract": ("app.artifacts.visualization_schema", "VisualizationContract"),
    "FigureArtifact": ("app.artifacts.figure_artifact_schema", "FigureArtifact"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
