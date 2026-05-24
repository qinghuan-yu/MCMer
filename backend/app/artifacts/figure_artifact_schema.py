"""Figure artifact schema shared by renderer, gate, and writer context."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.artifacts.protocols import normalize_figure_role


def coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


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
            source_data=coerce_text_list(payload.get("source_data") or payload.get("data_bindings")),
            chart_language_verified=bool(payload.get("chart_language_verified")),
            semantic_verified=bool(payload.get("semantic_verified")),
            paper_ready=bool(payload.get("paper_ready")),
        )

    @classmethod
    def from_gate_inputs(
        cls,
        *,
        path: str,
        artifact: dict[str, Any],
        request: dict[str, Any],
        semantic_role: str,
        figure_kind: str,
        canonical_caption: str,
    ) -> "FigureArtifact":
        source_data = (
            coerce_text_list(artifact.get("source_data"))
            or coerce_text_list(artifact.get("data_bindings"))
            or coerce_text_list(request.get("required_data"))
        )
        linked_result_ids = (
            coerce_text_list(artifact.get("linked_result_ids"))
            or coerce_text_list(request.get("linked_result_ids"))
        )
        caption = canonical_caption or str(
            artifact.get("caption")
            or artifact.get("figure_caption")
            or semantic_role
            or path
        ).strip()
        return cls(
            path=path,
            caption=caption,
            canonical_caption=canonical_caption,
            figure_request_id=str(artifact.get("figure_request_id") or "").strip(),
            semantic_role=normalize_figure_role(semantic_role),
            semantic_verified=bool(artifact.get("semantic_verified")),
            figure_kind=figure_kind,
            view_type=str(artifact.get("view_type") or request.get("view_type") or "").strip(),
            linked_result_ids=linked_result_ids,
            source_data=source_data,
            chart_language_verified=bool(artifact.get("chart_language_verified", True)),
            paper_ready=bool(artifact.get("paper_ready", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
