"""chart renderers responsibilities."""

from __future__ import annotations

import logging
from typing import Any
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role
from app.artifacts.plotting import _configure_fonts, _ensure_matplotlib, _make_figure_artifact, _register_artifact, _save_figure, _to_relative_path


logger = logging.getLogger(__name__)


def render_bar_summary(
    data: dict[str, float],
    title: str,
    x_label: str,
    y_label: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render a bar chart summary of key results.

    Returns a FigureArtifact dict that passes the gate.
    """
    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    fig, ax = _plt.subplots(figsize=(10, 6))
    labels = list(data.keys())
    values = list(data.values())

    bars = ax.bar(range(len(labels)), values, color='#4472C4', edgecolor='white')
    ax.set_xlabel(x_label, fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha='right')

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f'{val:.4g}', ha='center', va='bottom', fontsize=10)

    ax.grid(axis='y', alpha=0.3)
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label, *labels[:3]],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or [],
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_spectrum_curve(
    x_values: list[float],
    y_values: list[float],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "spectrum_curve",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    source_columns: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render deterministic spectrum curve (result figure)."""
    if not x_values or not y_values or len(x_values) != len(y_values):
        return {}
    columns = [str(item).strip().lower() for item in (source_columns or []) if str(item).strip()]
    if columns:
        spectral_tokens = {
            "wavenumber", "reflectance", "spectrum", "intensity",
            "波数", "反射率", "光谱", "强度",
        }
        if not any(any(token in col for token in spectral_tokens) for col in columns):
            return {}
    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        x_label, y_label = "波数", "反射率"
    else:
        x_label, y_label = "Wavenumber", "Reflectance"

    fig, ax = _plt.subplots(figsize=(9, 5.2))
    ax.plot(x_values, y_values, color="#1f77b4", linewidth=2.0)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="result_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "verified_result",
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_data_overview(
    numeric_columns: dict[str, list[float]],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = RESULT_OVERVIEW_ROLE,
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    view_type: str = "numeric_overview",
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render deterministic result-overview figure from numeric columns or scalar results."""
    semantic_role = normalize_figure_role(semantic_role)
    if semantic_role == RESULT_OVERVIEW_ROLE:
        title = canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language)
    if not isinstance(numeric_columns, dict):
        return {}

    scalar_items: list[tuple[str, float]] = []
    for name, values in numeric_columns.items():
        if not isinstance(values, list):
            continue
        cleaned = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if cleaned:
            scalar_items.append((str(name).strip() or "value", sum(cleaned) / len(cleaned)))

    if not scalar_items:
        return {}

    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        scalar_x_label, scalar_y_label = "核心结果", "数值"
    else:
        scalar_x_label, scalar_y_label = "Core Result", "Value"

    selected_scalars = scalar_items[:12]
    labels = [item[0] for item in selected_scalars]
    values = [item[1] for item in selected_scalars]
    fig, ax = _plt.subplots(figsize=(9.2, 5.4))
    bars = ax.bar(range(len(values)), values, color="#4472C4", edgecolor="white")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_xlabel(scalar_x_label)
    ax.set_ylabel(scalar_y_label)
    ax.set_title(title, fontsize=14, fontweight="bold")
    for idx, bar in enumerate(bars):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{values[idx]:.4g}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    _save_figure(fig, output_path)
    audit_labels = [title, scalar_x_label, scalar_y_label, *labels[:4]]

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=audit_labels,
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="result_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "view_type": view_type,
            "depicts": depicts or [semantic_role, "numeric_overview"],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "source_data",
            "caption": canonical_caption_for_role(semantic_role, chart_language),
            "canonical_caption": canonical_caption_for_role(semantic_role, chart_language),
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_peak_valley_annotation(
    x_values: list[float],
    y_values: list[float],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "peak_valley_annotation",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render deterministic peak/valley annotation figure (result figure)."""
    if not x_values or not y_values or len(x_values) != len(y_values):
        return {}
    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        x_label, y_label = "波数", "反射率"
        peak_text, valley_text = "峰值", "谷值"
    else:
        x_label, y_label = "Wavenumber", "Reflectance"
        peak_text, valley_text = "Peak", "Valley"

    max_idx = max(range(len(y_values)), key=lambda i: y_values[i])
    min_idx = min(range(len(y_values)), key=lambda i: y_values[i])

    fig, ax = _plt.subplots(figsize=(9, 5.2))
    ax.plot(x_values, y_values, color="#1f77b4", linewidth=2.0)
    ax.scatter([x_values[max_idx], x_values[min_idx]], [y_values[max_idx], y_values[min_idx]], color=["#d62728", "#2ca02c"], s=50)
    ax.text(x_values[max_idx], y_values[max_idx], peak_text, fontsize=9)
    ax.text(x_values[min_idx], y_values[min_idx], valley_text, fontsize=9)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label, peak_text, valley_text],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="result_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "verified_result",
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_fitting_curve(
    x_values: list[float],
    observed_values: list[float],
    fitted_values: list[float],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "fitting_curve",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render deterministic observed-vs-fitted curve (result figure)."""
    if (
        not x_values
        or not observed_values
        or not fitted_values
        or len(x_values) != len(observed_values)
        or len(x_values) != len(fitted_values)
    ):
        return {}

    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        x_label, y_label = "自变量", "观测/拟合值"
        obs_label, fit_label = "观测值", "拟合值"
    else:
        x_label, y_label = "X", "Observed/Fitted"
        obs_label, fit_label = "Observed", "Fitted"

    fig, ax = _plt.subplots(figsize=(9, 5.2))
    ax.scatter(x_values, observed_values, color="#1f77b4", s=28, label=obs_label)
    ax.plot(x_values, fitted_values, color="#d62728", linewidth=2.0, label=fit_label)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label, obs_label, fit_label],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="result_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "verified_result",
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_residual_plot(
    x_values: list[float],
    residual_values: list[float],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "residual_plot",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render deterministic residual plot with y=0 baseline."""
    if not x_values or not residual_values or len(x_values) != len(residual_values):
        return {}
    if all((val != val) for val in residual_values):
        return {}

    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        x_label, y_label = "自变量", "残差"
        baseline_label = "零参考线"
    else:
        x_label, y_label = "X", "Residual"
        baseline_label = "Zero baseline"

    fig, ax = _plt.subplots(figsize=(9, 5.2))
    ax.scatter(x_values, residual_values, color="#1f77b4", s=24, label=y_label)
    ax.axhline(0.0, color="#d62728", linestyle="--", linewidth=1.6, label=baseline_label)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label, baseline_label],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="result_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "verified_result",
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_comparison_bar(
    categories: list[str],
    values: list[float],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "comparison_bar",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render deterministic comparison bar chart."""
    if not categories or not values or len(categories) != len(values):
        return {}

    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        x_label, y_label = "方法/类别", "指标值"
    else:
        x_label, y_label = "Method/Category", "Metric Value"

    fig, ax = _plt.subplots(figsize=(9.2, 5.4))
    x = list(range(len(categories)))
    bars = ax.bar(x, values, color="#4472C4", edgecolor="white")
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_xticks(x)
    ax.set_xticklabels(categories, rotation=30, ha="right")
    for idx, bar in enumerate(bars):
        val = values[idx]
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.4g}", ha="center", va="bottom", fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label, *categories[:5]],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="result_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "verified_result",
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_line_chart(
    x_data: list[float],
    y_data: list[float] | list[list[float]],
    title: str,
    x_label: str,
    y_label: str,
    output_path: str,
    legend_labels: list[str] | None = None,
    chart_language: str = "Simplified Chinese",
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
    figure_role: str = "line_chart",
) -> dict[str, Any]:
    """Render a line chart.

    Parameters
    ----------
    x_data : list[float]
        X-axis values.
    y_data : list[float] or list[list[float]]
        Y-axis values. If list of lists, multiple lines are plotted.
    title, x_label, y_label : str
        Chart labels.
    output_path : str
        Where to save.
    legend_labels : list[str], optional
        Labels for each line.
    chart_language : str
        Language for font configuration.

    Returns a FigureArtifact dict.
    """
    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    fig, ax = _plt.subplots(figsize=(10, 6))

    if isinstance(y_data[0], list):
        for i, y in enumerate(y_data):
            label = legend_labels[i] if legend_labels and i < len(legend_labels) else None
            ax.plot(x_data, y, label=label, linewidth=2)
        if legend_labels:
            ax.legend(fontsize=10)
    else:
        ax.plot(x_data, y_data, linewidth=2, color='#4472C4')

    ax.set_xlabel(x_label, fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save_figure(fig, output_path)
    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label, *(legend_labels or [])[:3]],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or [],
        figure_role=figure_role,
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_scatter_plot(
    x_data: list[float],
    y_data: list[float],
    title: str,
    x_label: str,
    y_label: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
    figure_role: str = "scatter_plot",
) -> dict[str, Any]:
    """Render a scatter plot."""
    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    fig, ax = _plt.subplots(figsize=(10, 6))
    ax.scatter(x_data, y_data, alpha=0.6, s=30, color='#4472C4')
    ax.set_xlabel(x_label, fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save_figure(fig, output_path)
    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or [],
        figure_role=figure_role,
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_distance_time_curve(
    times: list[float],
    distances: list[float],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "distance_time_curve",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render a distance-time curve with semantic bindings."""
    if not times or not distances or len(times) != len(distances) or len(times) < 2:
        return {}

    _plt = _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        x_label = "时间"
        y_label = "距离"
    else:
        x_label = "Time"
        y_label = "Distance"

    fig, ax = _plt.subplots(figsize=(9, 5.8))
    ax.plot(times, distances, linewidth=2.2, color="#2ca02c")
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(x_label, fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    ax.grid(True, alpha=0.28)
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or [],
        figure_role=semantic_role,
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or [],
            "semantic_verified": True,
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact
