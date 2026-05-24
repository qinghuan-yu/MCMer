"""Writer-facing artifact protocol.

The writer should receive this context instead of scanning output folders or
inferring figure meaning from filenames.  The context is deterministic: it is
assembled from ResultRegistry and FigureBundle after figure gates pass.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.artifacts.figure_artifact_schema import FigureArtifact
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role
from app.artifacts.registry import FigureBundle


@dataclass(frozen=True)
class WriterFigure:
    path: str
    caption: str
    canonical_caption: str
    figure_request_id: str
    semantic_role: str
    figure_kind: str
    view_type: str = ""
    linked_result_ids: list[str] = field(default_factory=list)
    source_data: list[str] = field(default_factory=list)
    semantic_verified: bool = True
    chart_language_verified: bool = True
    paper_ready: bool = True

    @classmethod
    def from_bundle_item(cls, item: dict[str, Any], chart_language: str) -> "WriterFigure":
        artifact = FigureArtifact.from_legacy(item)
        semantic_role = normalize_figure_role(artifact.semantic_role or item.get("figure_role") or "")
        if semantic_role == RESULT_OVERVIEW_ROLE:
            caption = canonical_caption_for_role(semantic_role, chart_language)
        else:
            caption = (
                artifact.canonical_caption
                or artifact.caption
                or canonical_caption_for_role(semantic_role, chart_language)
            )
        return cls(
            path=artifact.path,
            caption=caption,
            canonical_caption=caption,
            figure_request_id=artifact.figure_request_id,
            semantic_role=semantic_role,
            figure_kind=artifact.figure_kind or "result_figure",
            view_type=artifact.view_type,
            linked_result_ids=artifact.linked_result_ids,
            source_data=artifact.source_data,
            semantic_verified=artifact.semantic_verified,
            chart_language_verified=artifact.chart_language_verified,
            paper_ready=artifact.paper_ready,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WriterContext:
    verified_results: list[dict[str, Any]]
    blocked_results: list[dict[str, Any]]
    available_figures: list[WriterFigure]
    result_figures: list[WriterFigure]
    explanatory_figures: list[WriterFigure]
    required_images: list[str]
    allowed_image_paths: list[str]
    forbidden_image_paths: list[str]
    rules: dict[str, Any]

    @classmethod
    def build(
        cls,
        result_registry: dict[str, Any],
        figure_bundle: FigureBundle,
        chart_language: str = "Simplified Chinese",
    ) -> "WriterContext":
        def _figures(items: list[dict[str, Any]]) -> list[WriterFigure]:
            return [
                WriterFigure.from_bundle_item(item, chart_language)
                for item in items
                if isinstance(item, dict) and str(item.get("path") or "").strip()
            ]

        overview_caption = canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language)
        available_figures = _figures(figure_bundle.available_figures)
        result_figures = _figures(figure_bundle.result_figures)
        explanatory_figures = _figures(figure_bundle.explanatory_figures)
        allowed_image_paths = list(dict.fromkeys(item.path for item in available_figures if item.path))
        forbidden_image_paths = list(
            dict.fromkeys(
                str(item.get("path") or "").strip()
                for item in figure_bundle.diagnostic_figures
                if isinstance(item, dict) and str(item.get("path") or "").strip()
            )
        )
        return cls(
            verified_results=[
                item for item in result_registry.get("verified_results", [])
                if isinstance(item, dict)
            ],
            blocked_results=[
                item for item in result_registry.get("blocked_results", [])
                if isinstance(item, dict)
            ],
            available_figures=available_figures,
            result_figures=result_figures,
            explanatory_figures=explanatory_figures,
            required_images=list(figure_bundle.required_images),
            allowed_image_paths=allowed_image_paths,
            forbidden_image_paths=forbidden_image_paths,
            rules={
                "writer_may_reference_only_available_figures": True,
                "writer_must_use_canonical_caption": True,
                "result_overview_caption": overview_caption,
                "result_overview_aliases": ["result_overview", "data_overview"],
                "do_not_relabel_result_overview": [
                    "fitting_curve",
                    "residual_plot",
                    "comparison_bar",
                    "mechanism",
                    "workflow",
                ],
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified_results": self.verified_results,
            "blocked_results": self.blocked_results,
            "available_figures": [item.to_dict() for item in self.available_figures],
            "result_figures": [item.to_dict() for item in self.result_figures],
            "explanatory_figures": [item.to_dict() for item in self.explanatory_figures],
            "required_images": self.required_images,
            "allowed_image_paths": self.allowed_image_paths,
            "forbidden_image_paths": self.forbidden_image_paths,
            "rules": self.rules,
        }

    def save(self, work_dir: str | Path) -> Path:
        path = Path(work_dir) / "writer_context.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path
