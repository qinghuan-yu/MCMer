"""Unified artifact protocol for MCMer.

Provides ArtifactRegistry, FigureArtifact validation, DocumentFinalizer,
ProblemContract, FigureRequest, deterministic renderers, and diagnostics.
"""

from app.artifacts.registry import ArtifactRegistry
from app.artifacts.contracts import ProblemContract
from app.artifacts.exporters import DocumentFinalizer
from app.artifacts.figure_requests import FigureRequest, FigureRequestPlanner
from app.artifacts.renderers import (
    render_bar_summary,
    render_distance_time_curve,
    render_line_chart,
    render_scatter_plot,
    render_table_figure,
    render_trajectory_shielding_2d,
    render_verified_summary,
)
from app.artifacts.diagnostics import GateReport, FigureGateReport, QualityGateReport, BudgetReport, WorkflowTrace

__all__ = [
    "ArtifactRegistry",
    "ProblemContract",
    "DocumentFinalizer",
    "FigureRequest",
    "FigureRequestPlanner",
    "render_bar_summary",
    "render_distance_time_curve",
    "render_line_chart",
    "render_scatter_plot",
    "render_table_figure",
    "render_trajectory_shielding_2d",
    "render_verified_summary",
    "GateReport",
    "FigureGateReport",
    "QualityGateReport",
    "BudgetReport",
    "WorkflowTrace",
]
