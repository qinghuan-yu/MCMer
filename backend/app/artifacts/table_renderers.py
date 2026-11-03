"""table renderers responsibilities."""

from __future__ import annotations

from typing import Any
from app.artifacts.plotting import _configure_fonts, _ensure_matplotlib, _make_figure_artifact, _register_artifact, _save_figure, _to_relative_path


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
    _plt = _ensure_matplotlib()
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

    _plt = _ensure_matplotlib()
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
