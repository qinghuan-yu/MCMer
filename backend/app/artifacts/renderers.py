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
            logger.warning("No CJK font available, falling back to DejaVu Sans")
            chosen = ['DejaVu Sans']
        _mpl.rcParams['font.family'] = 'sans-serif'
        _mpl.rcParams['font.sans-serif'] = chosen + ['DejaVu Sans']
        _mpl.rcParams['axes.unicode_minus'] = False


def _save_figure(fig, output_path: str, dpi: int = 300) -> str:
    """Save figure and return the path."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=dpi, bbox_inches='tight')
    _plt.close(fig)
    return output_path


def render_bar_summary(
    data: dict[str, float],
    title: str,
    x_label: str,
    y_label: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
) -> str:
    """Render a bar chart summary of key results.

    Parameters
    ----------
    data : dict
        {label: value} pairs to plot.
    title, x_label, y_label : str
        Chart labels in the required language.
    output_path : str
        Where to save the figure.
    chart_language : str
        "Simplified Chinese" or "English".

    Returns
    -------
    str : path to saved figure
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

    # Add value labels on bars
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f'{val:.4g}', ha='center', va='bottom', fontsize=10)

    ax.grid(axis='y', alpha=0.3)
    fig.tight_layout()
    return _save_figure(fig, output_path)


def render_line_chart(
    x_data: list[float],
    y_data: list[float] | list[list[float]],
    title: str,
    x_label: str,
    y_label: str,
    output_path: str,
    legend_labels: list[str] | None = None,
    chart_language: str = "Simplified Chinese",
) -> str:
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

    Returns
    -------
    str : path to saved figure
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
    return _save_figure(fig, output_path)


def render_scatter_plot(
    x_data: list[float],
    y_data: list[float],
    title: str,
    x_label: str,
    y_label: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
) -> str:
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
    return _save_figure(fig, output_path)


def render_table_figure(
    headers: list[str],
    rows: list[list[str]],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
) -> str:
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
    return _save_figure(fig, output_path)


def render_verified_summary(
    verified_results: list[dict[str, Any]],
    title: str,
    output_path: str,
    chart_language: str = "Simplified Chinese",
) -> str:
    """Render a summary figure of verified results.

    Shows a table-like visualization of key results.
    """
    if not verified_results:
        return ""

    # Extract data for display
    headers = ["ID", "Name", "Value", "Unit", "Status"]
    rows = []
    for r in verified_results[:10]:  # limit to 10
        rows.append([
            str(r.get("id", ""))[:20],
            str(r.get("name", ""))[:30],
            str(r.get("value", ""))[:15],
            str(r.get("unit", ""))[:10],
            str(r.get("status", ""))[:10],
        ])

    return render_table_figure(headers, rows, title, output_path, chart_language)
