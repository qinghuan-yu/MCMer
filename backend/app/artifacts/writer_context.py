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


def _section_of(item: dict[str, Any]) -> str:
    return str(item.get("section") or item.get("problem_section") or "unknown").strip() or "unknown"


def _result_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("name") or "").strip()


def _build_coverage_diagnostics(
    *,
    result_registry: dict[str, Any],
    verified_results: list[dict[str, Any]],
    blocked_results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = result_registry.get("summary", {}) if isinstance(result_registry, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    verified_count = int(summary.get("verified_count", len(verified_results)) or 0)
    blocked_count = int(summary.get("blocked_count", len(blocked_results)) or 0)
    coverage_status = str(summary.get("coverage_status") or ("partial" if blocked_count else "ready")).strip()

    provisional_results = [
        item for item in verified_results
        if str(item.get("confidence_level") or "").strip() == "salvaged_pending_review"
        or any(
            "requires_formula_and_unit_verification" in str(warning)
            for warning in (item.get("warnings", []) if isinstance(item.get("warnings"), list) else [])
        )
    ]
    blocked_sections: dict[str, int] = {}
    blocked_examples: list[dict[str, str]] = []
    for item in blocked_results:
        section = _section_of(item)
        blocked_sections[section] = blocked_sections.get(section, 0) + 1
        if len(blocked_examples) < 8:
            warnings = item.get("warnings", [])
            warning_text = (
                "; ".join(str(w) for w in warnings if str(w).strip())
                if isinstance(warnings, list)
                else ""
            )
            reason = str(item.get("evidence") or warning_text or item.get("status") or "blocked").strip()
            blocked_examples.append(
                {
                    "id": _result_id(item),
                    "section": section,
                    "reason": reason[:240],
                }
            )

    top_blocked_sections = [
        {"section": section, "count": count}
        for section, count in sorted(blocked_sections.items(), key=lambda kv: kv[1], reverse=True)[:8]
    ]
    must_disclose = bool(blocked_count or coverage_status != "ready" or provisional_results)
    return {
        "coverage_status": coverage_status,
        "verified_count": verified_count,
        "blocked_count": blocked_count,
        "provisional_verified_count": len(provisional_results),
        "top_blocked_reasons": summary.get("top_blocked_reasons", []) if isinstance(summary.get("top_blocked_reasons"), list) else [],
        "top_blocked_sections": top_blocked_sections,
        "blocked_examples": blocked_examples,
        "must_disclose_partial": must_disclose,
        "paper_disclosure_requirements": [
            "If must_disclose_partial is true, include a limitations or result-coverage paragraph in the paper.",
            "Do not present blocked sections as fully solved; state which results are missing or provisional.",
            "Results with confidence_level=salvaged_pending_review must be described as recovered/provisional unless independently reviewed.",
            "When a subproblem has blocked execution status, avoid claiming complete numerical answers for that subproblem.",
        ] if must_disclose else [],
    }


@dataclass(frozen=True)
class WriterContext:
    coverage_diagnostics: dict[str, Any]
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
        verified_results = [
            item for item in result_registry.get("verified_results", [])
            if isinstance(item, dict)
        ]
        blocked_results = [
            item for item in result_registry.get("blocked_results", [])
            if isinstance(item, dict)
        ]
        coverage_diagnostics = _build_coverage_diagnostics(
            result_registry=result_registry,
            verified_results=verified_results,
            blocked_results=blocked_results,
        )
        return cls(
            coverage_diagnostics=coverage_diagnostics,
            verified_results=verified_results,
            blocked_results=blocked_results,
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
                "writer_must_disclose_partial_coverage": bool(
                    coverage_diagnostics.get("must_disclose_partial")
                ),
                "result_overview_description_scope": (
                    "Describe only the labeled numeric values visible in the overview; "
                    "do not interpret it as a fitted curve, residual plot, mechanism diagram, or workflow."
                ),
                "result_overview_placement": (
                    "Place result overview figures next to the result table, result analysis, "
                    "or limitations paragraph they support."
                ),
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
            "coverage_diagnostics": self.coverage_diagnostics,
            "rules": self.rules,
            "available_figures": [item.to_dict() for item in self.available_figures],
            "result_figures": [item.to_dict() for item in self.result_figures],
            "explanatory_figures": [item.to_dict() for item in self.explanatory_figures],
            "required_images": self.required_images,
            "allowed_image_paths": self.allowed_image_paths,
            "forbidden_image_paths": self.forbidden_image_paths,
            "verified_results": self.verified_results,
            "blocked_results": self.blocked_results,
        }

    def save(self, work_dir: str | Path) -> Path:
        path = Path(work_dir) / "writer_context.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path
