"""Deterministic renderer skill.

This skill is the stable boundary for turning FigureRequest artifacts into
FigureArtifact records.  It currently delegates to the existing FigureStage
implementation while the per-view renderers are migrated behind this facade.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RendererResult:
    created_images: list[str] = field(default_factory=list)
    satisfied: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    renderer: str = "RendererSkill"

    @classmethod
    def from_legacy(cls, payload: dict[str, Any]) -> "RendererResult":
        return cls(
            created_images=[
                str(item).strip()
                for item in (payload.get("created_images", []) if isinstance(payload, dict) else [])
                if str(item).strip()
            ],
            satisfied=[
                item for item in (payload.get("satisfied", []) if isinstance(payload, dict) else [])
                if isinstance(item, dict)
            ],
            missing=[
                item for item in (payload.get("missing", []) if isinstance(payload, dict) else [])
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RendererSkill:
    """Render required figure requests through deterministic view renderers."""

    @staticmethod
    def render_required_requests(
        work_dir: str,
        result_registry: dict[str, Any],
        required_requests: list[dict[str, Any]],
        chart_language: str,
    ) -> RendererResult:
        # Imported lazily to avoid a circular import while FigureStage still
        # owns the legacy renderer implementation.
        from app.core.figure_stage import FigureStage

        return RendererResult.from_legacy(
            FigureStage.render_required_requests_deterministically(
                work_dir=work_dir,
                result_registry=result_registry,
                required_requests=required_requests,
                chart_language=chart_language,
            )
        )
