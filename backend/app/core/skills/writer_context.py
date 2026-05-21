"""Writer context skill.

WriterAgent should consume this compact context rather than scan output
folders or infer image semantics by filename.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.artifacts.registry import FigureBundle
from app.artifacts.writer_context import WriterContext


class WriterContextSkill:
    """Assemble the only figure payload the writer is allowed to use."""

    @staticmethod
    def build(
        result_registry: dict[str, Any],
        figure_bundle: FigureBundle,
        chart_language: str = "Simplified Chinese",
    ) -> dict[str, Any]:
        return WriterContext.build(result_registry, figure_bundle, chart_language).to_dict()

    @staticmethod
    def build_context(
        result_registry: dict[str, Any],
        figure_bundle: FigureBundle,
        chart_language: str = "Simplified Chinese",
    ) -> WriterContext:
        return WriterContext.build(result_registry, figure_bundle, chart_language)

    @staticmethod
    def save(
        work_dir: str | Path,
        result_registry: dict[str, Any],
        figure_bundle: FigureBundle,
        chart_language: str = "Simplified Chinese",
    ) -> Path:
        return WriterContextSkill.build_context(
            result_registry=result_registry,
            figure_bundle=figure_bundle,
            chart_language=chart_language,
        ).save(work_dir)
