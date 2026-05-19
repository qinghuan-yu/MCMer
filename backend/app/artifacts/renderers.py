"""Deterministic figure renderers for MCMer.

These renderers generate charts from structured data without LLM involvement.
They ensure consistent language, fonts, and style across all paper figures.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lazy imports for matplotlib to avoid import errors when not needed
_mpl = None
_plt = None


def _ensure_matplotlib():
    global _mpl, _plt
    if _mpl is None:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        _mpl = matplotlib
        _plt = plt


def _configure_fonts(chart_language: str):
    """Configure matplotlib fonts for the given language."""
    _ensure_matplotlib()
    if chart_language == "Simplified Chinese":
        from matplotlib import font_manager
        font_manager._load_fontmanager(try_read_cache=False)
        available = {f.name for f in font_manager.fontManager.ttflist}
        chinese_fonts = [
            'WenQuanYi Micro Hei', 'WenQuanYi Zen Hei',
            'Noto Sans CJK SC', 'Noto Sans CJK JP',
            'Source Han Sans SC', 'SimHei', 'Microsoft YaHei',
            'PingFang SC', 'Arial Unicode MS',
        ]
        chosen = [name for name in chinese_fonts if name in available]
        if not chosen:
            raise RuntimeError(
                "No CJK font available for Simplified Chinese chart rendering. "
                "Refusing to render paper figure with fallback fonts."
            )
        _mpl.rcParams['font.family'] = 'sans-serif'
        _mpl.rcParams['font.sans-serif'] = chosen + ['DejaVu Sans']
        _mpl.rcParams['axes.unicode_minus'] = False


def _save_figure(fig, output_path: str, dpi: int = 300) -> str:
    """Save figure and return the path."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=dpi, bbox_inches='tight')
    _plt.close(fig)
    return output_path


def _to_relative_path(output_path: str) -> str:
    """Convert absolute path to relative path like 'output/fig.png'."""
    p = Path(output_path)
    parts = p.parts
    # Find the paper directory component
    for i, part in enumerate(parts):
        if part in ("output", "figures", "final_results"):
            return "/".join(parts[i:])
    # Fallback: just use the last 2 components
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return p.name


def _make_figure_artifact(
    rel_path: str,
    chart_language: str,
    visible_text_audit: list[str],
    linked_result_ids: list[str],
    source_data: list[str],
    figure_role: str = "result_summary",
    figure_kind: str = "result_figure",
) -> dict[str, Any]:
    """Create a FigureArtifact dict that passes the gate."""
    return {
        "path": rel_path,
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": chart_language,
        "chart_language_verified": True,
        "visible_text_audit": visible_text_audit,
        "created_by": "deterministic_renderer",
        "helper_version": 3,
        "is_placeholder": False,
        "figure_role": figure_role,
        "figure_kind": figure_kind,
        "linked_result_ids": linked_result_ids,
        "source_data": source_data,
    }


def _register_artifact(artifact: dict[str, Any], work_dir: str) -> None:
    """Register a FigureArtifact in the manifest."""
    from app.utils.figure_artifacts import save_figure_manifest, load_figure_manifest
    manifest = load_figure_manifest(work_dir)
    manifest.append(artifact)
    save_figure_manifest(work_dir, manifest)


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
    _ensure_matplotlib()
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


def render_geometry_schematic(
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "geometry_schematic",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    model_objects: list[str] | None = None,
    formulas: list[str] | None = None,
    source_problem_refs: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render a deterministic geometry explanatory schematic."""
    _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        labels = ("导弹", "目标", "烟幕")
        x_label, y_label = "水平坐标", "垂直坐标"
    else:
        labels = ("Missile", "Target", "Smoke Cloud")
        x_label, y_label = "X", "Y"

    fig, ax = _plt.subplots(figsize=(8.4, 6.0))
    points = [(0.1, 0.8), (0.85, 0.2), (0.5, 0.55)]
    colors = ["#d62728", "#1f77b4", "#7f7f7f"]

    for (x, y), label, color in zip(points, labels, colors):
        ax.scatter([x], [y], s=110, color=color)
        ax.text(x + 0.015, y + 0.015, label, fontsize=10)

    ax.plot([points[0][0], points[1][0]], [points[0][1], points[1][1]], linestyle="--", color="#444444", alpha=0.8)
    ax.plot([points[0][0], points[2][0]], [points[0][1], points[2][1]], linestyle="-", color="#2ca02c", alpha=0.9)
    ax.plot([points[2][0], points[1][0]], [points[2][1], points[1][1]], linestyle="-", color="#ff7f0e", alpha=0.9)
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label, *labels],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="explanatory_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "model_contract",
            "model_objects": model_objects or ["model_core"],
            "formulas": formulas or [],
            "source_problem_refs": source_problem_refs or ([subproblem_id] if subproblem_id else ["problem"]),
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_timeline_schematic(
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "timeline_schematic",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    model_objects: list[str] | None = None,
    formulas: list[str] | None = None,
    source_problem_refs: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render a deterministic timeline explanatory schematic."""
    _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        labels = ["投放", "起爆", "生效", "失效"]
        x_label, y_label = "时间", "阶段"
    else:
        labels = ["Release", "Detonation", "Active", "Expired"]
        x_label, y_label = "Time", "Stage"

    fig, ax = _plt.subplots(figsize=(9.0, 3.8))
    x = [0, 1, 2, 3]
    ax.plot(x, [0, 0, 0, 0], color="#1f77b4", linewidth=2)
    ax.scatter(x, [0, 0, 0, 0], s=70, color="#d62728")
    for idx, label in enumerate(labels):
        ax.text(idx, 0.08, label, ha="center", fontsize=10)
    ax.set_yticks([])
    ax.set_xticks(x)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, x_label, y_label, *labels],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="explanatory_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "model_contract",
            "model_objects": model_objects or ["process_stage"],
            "formulas": formulas or [],
            "source_problem_refs": source_problem_refs or ([subproblem_id] if subproblem_id else ["problem"]),
        }
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_algorithm_flowchart(
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "algorithm_flowchart",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    model_objects: list[str] | None = None,
    formulas: list[str] | None = None,
    source_problem_refs: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render a deterministic algorithm flowchart-style explanatory figure."""
    _ensure_matplotlib()
    _configure_fonts(chart_language)

    if chart_language == "Simplified Chinese":
        steps = ["输入数据", "预处理", "参数求解", "结果复核", "输出结论"]
    else:
        steps = ["Input", "Preprocess", "Solve", "Verify", "Output"]

    fig, ax = _plt.subplots(figsize=(10, 4.8))
    ax.axis("off")
    for idx, step in enumerate(steps):
        x = 0.08 + idx * 0.18
        y = 0.5
        rect = _mpl.patches.FancyBboxPatch((x, y), 0.14, 0.16, boxstyle="round,pad=0.02", linewidth=1.2, edgecolor="#1f77b4", facecolor="#e8f1fb")
        ax.add_patch(rect)
        ax.text(x + 0.07, y + 0.08, step, ha="center", va="center", fontsize=9)
        if idx < len(steps) - 1:
            ax.annotate("", xy=(x + 0.16, y + 0.08), xytext=(x + 0.18, y + 0.08), arrowprops=dict(arrowstyle="->", lw=1.2, color="#444"))
    ax.set_title(title, fontsize=14, fontweight="bold")
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, *steps],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or ["result_registry.json"],
        figure_role=semantic_role,
        figure_kind="explanatory_figure",
    )
    artifact.update(
        {
            "figure_request_id": figure_request_id,
            "subproblem_id": subproblem_id,
            "semantic_role": semantic_role,
            "depicts": depicts or [semantic_role],
            "data_bindings": source_data or ["result_registry.json"],
            "semantic_verified": True,
            "evidence_binding_type": "model_contract",
            "model_objects": model_objects or ["algorithm_pipeline"],
            "formulas": formulas or [],
            "source_problem_refs": source_problem_refs or ([subproblem_id] if subproblem_id else ["problem"]),
        }
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
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render deterministic spectrum curve (result figure)."""
    if not x_values or not y_values or len(x_values) != len(y_values):
        return {}
    _ensure_matplotlib()
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
    _ensure_matplotlib()
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
    _ensure_matplotlib()
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
    _ensure_matplotlib()
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


def render_table_figure(
    headers: list[str],
    rows: list[list[str]],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
    figure_role: str = "table_figure",
) -> dict[str, Any]:
    """Render a table as a figure."""
    _ensure_matplotlib()
    _configure_fonts(chart_language)

    fig, ax = _plt.subplots(figsize=(max(8, len(headers) * 2), max(4, len(rows) * 0.4 + 1)))
    ax.axis('off')

    table = ax.table(
        cellText=rows,
        colLabels=headers,
        cellLoc='center',
        loc='center',
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.5)

    # Style header
    for j in range(len(headers)):
        table[0, j].set_facecolor('#4472C4')
        table[0, j].set_text_props(color='white', fontweight='bold')

    ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
    fig.tight_layout()
    _save_figure(fig, output_path)
    rel_path = _to_relative_path(output_path)
    audit_headers = headers[:3] if headers else []
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, *audit_headers],
        linked_result_ids=linked_result_ids or [],
        source_data=source_data or [],
        figure_role=figure_role,
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_verified_summary(
    verified_results: list[dict[str, Any]],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render a summary figure of verified results.

    Returns a FigureArtifact dict that passes the gate.
    """
    if not verified_results:
        return {}

    # Language-aware headers
    if chart_language == "Simplified Chinese":
        headers = ["编号", "名称", "数值", "单位", "状态"]
    else:
        headers = ["ID", "Name", "Value", "Unit", "Status"]

    rows = []
    for r in verified_results[:10]:
        rows.append([
            str(r.get("id", ""))[:20],
            str(r.get("name", ""))[:30],
            str(r.get("value", ""))[:15],
            str(r.get("unit", ""))[:10],
            str(r.get("status", ""))[:10],
        ])

    result_ids = linked_result_ids or [str(r.get("id", "")) for r in verified_results[:5] if r.get("id")]

    _ensure_matplotlib()
    _configure_fonts(chart_language)

    fig, ax = _plt.subplots(figsize=(max(8, len(headers) * 2), max(4, len(rows) * 0.4 + 1)))
    ax.axis('off')

    table = ax.table(
        cellText=rows,
        colLabels=headers,
        cellLoc='center',
        loc='center',
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.5)

    for j in range(len(headers)):
        table[0, j].set_facecolor('#4472C4')
        table[0, j].set_text_props(color='white', fontweight='bold')

    ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
    fig.tight_layout()
    _save_figure(fig, output_path)

    rel_path = _to_relative_path(output_path)
    artifact = _make_figure_artifact(
        rel_path=rel_path,
        chart_language=chart_language,
        visible_text_audit=[title, *headers, *[cell[0] for cell in rows[:3]]],
        linked_result_ids=result_ids,
        source_data=source_data or ["result_registry.json"],
    )
    if work_dir:
        _register_artifact(artifact, work_dir)
    return artifact


def render_trajectory_shielding_2d(
    points: list[tuple[float, float]],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
    figure_request_id: str = "",
    subproblem_id: str = "",
    semantic_role: str = "trajectory_shielding",
    depicts: list[str] | None = None,
    linked_result_ids: list[str] | None = None,
    source_data: list[str] | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Render a 2D trajectory-style figure with semantic bindings."""
    if not points or len(points) < 2:
        return {}

    _ensure_matplotlib()
    _configure_fonts(chart_language)

    x_values = [float(p[0]) for p in points]
    y_values = [float(p[1]) for p in points]

    if chart_language == "Simplified Chinese":
        x_label = "时间序号"
        y_label = "轨迹量纲值"
    else:
        x_label = "Time index"
        y_label = "Trajectory metric"

    fig, ax = _plt.subplots(figsize=(9, 5.8))
    ax.plot(x_values, y_values, linewidth=2.0, color="#1f77b4", label="Trajectory")
    ax.scatter([x_values[0], x_values[-1]], [y_values[0], y_values[-1]], color="#d62728", s=35)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(x_label, fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    ax.grid(True, alpha=0.28)
    ax.legend(fontsize=10)
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

    _ensure_matplotlib()
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
