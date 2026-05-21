"""Deterministic renderer skill.

This skill is the stable boundary for turning FigureRequest artifacts into
FigureArtifact records.  It currently delegates to the existing FigureStage
implementation while the per-view renderers are migrated behind this facade.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role
from app.artifacts.renderers import render_data_overview


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
        overview_requests: list[dict[str, Any]] = []
        legacy_requests: list[dict[str, Any]] = []
        for request in required_requests:
            if not isinstance(request, dict):
                continue
            role = normalize_figure_role(request.get("semantic_role") or request.get("role"))
            if role == RESULT_OVERVIEW_ROLE:
                overview_requests.append(request)
            else:
                legacy_requests.append(request)

        created_images: list[str] = []
        satisfied: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []

        for request in overview_requests:
            rendered = RendererSkill._render_result_overview(
                work_dir=work_dir,
                result_registry=result_registry,
                request=request,
                chart_language=chart_language,
            )
            created_images.extend(rendered.created_images)
            satisfied.extend(rendered.satisfied)
            missing.extend(rendered.missing)

        if legacy_requests:
            legacy = RendererSkill._render_legacy_requests(
                work_dir=work_dir,
                result_registry=result_registry,
                required_requests=legacy_requests,
                chart_language=chart_language,
            )
            created_images.extend(legacy.created_images)
            satisfied.extend(legacy.satisfied)
            missing.extend(legacy.missing)

        return RendererResult(
            created_images=list(dict.fromkeys(created_images)),
            satisfied=satisfied,
            missing=missing,
        )

    @staticmethod
    def _render_legacy_requests(
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

    @staticmethod
    def _render_result_overview(
        work_dir: str,
        result_registry: dict[str, Any],
        request: dict[str, Any],
        chart_language: str,
    ) -> RendererResult:
        from app.core.figure_stage import FigureStage

        req_id = str(request.get("id") or "").strip()
        if not req_id:
            return RendererResult(
                missing=[{"figure_request_id": "", "required": True, "satisfied": False, "reason": "missing_request_id"}]
            )

        required_data = request.get("required_data", [])
        required_data_list = [
            str(item).strip()
            for item in required_data
            if str(item).strip()
        ] if isinstance(required_data, list) else []

        columns, matched_sources = FigureStage.deterministic_named_columns_from_data_files(
            work_dir,
            required_data_list,
        )
        overview_columns: dict[str, list[float]] = {}
        for name, values in columns.items():
            if not isinstance(values, list):
                continue
            clean = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            if clean:
                overview_columns[str(name)] = clean

        registry_linked_ids: list[str] = []
        registry_sources: list[str] = []
        if not overview_columns:
            overview_columns, registry_linked_ids, registry_sources = FigureStage.deterministic_overview_columns_from_registry(result_registry)

        if not overview_columns:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": f"insufficient_numeric_columns:{RESULT_OVERVIEW_ROLE}",
                    }
                ]
            )

        request_linked_ids = [
            str(item).strip()
            for item in (request.get("linked_result_ids", []) if isinstance(request.get("linked_result_ids"), list) else [])
            if str(item).strip()
        ] or registry_linked_ids
        if not request_linked_ids:
            request_linked_ids = [
                str(item.get("id") or "").strip()
                for item in (result_registry.get("verified_results", []) if isinstance(result_registry, dict) else [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ][:12]
        if not request_linked_ids:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "missing_verified_result_binding",
                    }
                ]
            )

        subproblem_id = str(request.get("subproblem_id") or request.get("problem_section") or "").strip()
        output_path = str(Path(work_dir) / "output" / f"{req_id}.png")
        artifact = render_data_overview(
            numeric_columns=overview_columns,
            title=canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language),
            output_path=output_path,
            chart_language=chart_language,
            figure_request_id=req_id,
            subproblem_id=subproblem_id,
            semantic_role=RESULT_OVERVIEW_ROLE,
            depicts=[RESULT_OVERVIEW_ROLE, "numeric_overview"],
            linked_result_ids=request_linked_ids[:12],
            source_data=matched_sources or registry_sources or required_data_list or ["result_registry.json"],
            view_type=str(request.get("view_type") or "numeric_overview"),
            work_dir=work_dir,
        )
        artifact_path = str(artifact.get("path") or "").strip() if isinstance(artifact, dict) else ""
        if not artifact_path:
            return RendererResult(
                missing=[
                    {
                        "figure_request_id": req_id,
                        "required": True,
                        "satisfied": False,
                        "reason": "renderer_failed",
                    }
                ]
            )
        return RendererResult(
            created_images=[artifact_path],
            satisfied=[{"figure_request_id": req_id, "required": True, "satisfied": True}],
        )
