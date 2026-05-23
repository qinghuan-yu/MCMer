"""Figure artifact schema shared by renderer, gate, and writer context."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.artifacts.protocols import normalize_figure_role


@dataclass(frozen=True)
class FigureArtifact:
    path: str
    figure_request_id: str = ""
    semantic_role: str = ""
    figure_kind: str = "result_figure"
    view_type: str = ""
    caption: str = ""
    canonical_caption: str = ""
    linked_result_ids: list[str] = field(default_factory=list)
    source_data: list[str] = field(default_factory=list)
    chart_language_verified: bool = False
    semantic_verified: bool = False
    paper_ready: bool = False

    @classmethod
    def from_legacy(cls, payload: dict[str, Any]) -> "FigureArtifact":
        role = normalize_figure_role(
            payload.get("semantic_role") or payload.get("role") or payload.get("view_type")
        )
        return cls(
            path=str(payload.get("path") or payload.get("filename") or "").strip(),
            figure_request_id=str(payload.get("figure_request_id") or payload.get("request_id") or "").strip(),
            semantic_role=role,
            figure_kind=str(payload.get("figure_kind") or "result_figure").strip(),
            view_type=str(payload.get("view_type") or role).strip(),
            caption=str(payload.get("caption") or "").strip(),
            canonical_caption=str(payload.get("canonical_caption") or "").strip(),
            linked_result_ids=[
                str(item).strip()
                for item in payload.get("linked_result_ids", []) or []
                if str(item).strip()
            ],
            source_data=[
                str(item).strip()
                for item in payload.get("source_data", []) or []
                if str(item).strip()
            ],
            chart_language_verified=bool(payload.get("chart_language_verified")),
            semantic_verified=bool(payload.get("semantic_verified")),
            paper_ready=bool(payload.get("paper_ready")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
