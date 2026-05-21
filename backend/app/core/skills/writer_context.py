"""Writer context skill.

WriterAgent should consume this compact context rather than scan output
folders or infer image semantics by filename.
"""

from __future__ import annotations

from typing import Any

from app.artifacts.registry import FigureBundle


class WriterContextSkill:
    """Assemble the only figure payload the writer is allowed to use."""

    @staticmethod
    def build(result_registry: dict[str, Any], figure_bundle: FigureBundle) -> dict[str, Any]:
        return {
            "verified_results": [
                item for item in result_registry.get("verified_results", [])
                if isinstance(item, dict)
            ],
            "blocked_results": [
                item for item in result_registry.get("blocked_results", [])
                if isinstance(item, dict)
            ],
            "available_figures": figure_bundle.available_figures,
            "result_figures": figure_bundle.result_figures,
            "explanatory_figures": figure_bundle.explanatory_figures,
            "required_images": figure_bundle.required_images,
            "rules": {
                "writer_may_reference_only_available_figures": True,
                "result_overview_caption": "核心结果概览图",
                "do_not_relabel_result_overview": [
                    "fitting_curve",
                    "residual_plot",
                    "comparison_bar",
                    "mechanism",
                    "workflow",
                ],
            },
        }
