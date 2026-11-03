"""plotting responsibilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any


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
    return _plt


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
