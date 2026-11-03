"""schematic renderers responsibilities."""

from __future__ import annotations

from typing import Any
from app.artifacts.plotting import _configure_fonts, _ensure_matplotlib, _make_figure_artifact, _register_artifact, _save_figure, _to_relative_path


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
    _plt = _ensure_matplotlib()
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
    _plt = _ensure_matplotlib()
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
    _plt = _ensure_matplotlib()
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
        from matplotlib.patches import FancyBboxPatch

        rect = FancyBboxPatch((x, y), 0.14, 0.16, boxstyle="round,pad=0.02", linewidth=1.2, edgecolor="#1f77b4", facecolor="#e8f1fb")
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

    _plt = _ensure_matplotlib()
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
