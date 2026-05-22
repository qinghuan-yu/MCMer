"""Typed visualization protocol objects shared by planner, renderer, and writer.

The workflow still accepts legacy dictionaries, but new result-driven figure
planning should normalize through these small protocol helpers first.  This
keeps language, result binding, view type, and caption rules out of prompts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role


def _coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


@dataclass(frozen=True)
class VisualizationContract:
    result_id: str
    candidate_views: list[str] = field(default_factory=list)
    required_views: list[str] = field(default_factory=list)
    fallback_views: list[str] = field(default_factory=lambda: [RESULT_OVERVIEW_ROLE])
    visualization_exemption: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerifiedResult:
    id: str
    section: str = ""
    verified: bool = True
    result_type: str = "unknown"
    claim_text: str = ""
    data_artifacts: list[str] = field(default_factory=list)
    columns: dict[str, Any] = field(default_factory=dict)
    visualization_contract: VisualizationContract | None = None

    @classmethod
    def from_legacy(cls, payload: dict[str, Any]) -> "VerifiedResult":
        rid = str(payload.get("id") or "").strip()
        raw_contract = payload.get("visualization_contract")
        contract = None
        if isinstance(raw_contract, dict):
            contract = VisualizationContract(
                result_id=str(raw_contract.get("result_id") or rid).strip(),
                candidate_views=[
                    normalize_figure_role(item)
                    for item in raw_contract.get("candidate_views", []) or []
                    if str(item).strip()
                ],
                required_views=[
                    normalize_figure_role(item)
                    for item in raw_contract.get("required_views", []) or []
                    if str(item).strip()
                ],
                fallback_views=[
                    normalize_figure_role(item)
                    for item in raw_contract.get("fallback_views", []) or []
                    if str(item).strip()
                ] or [RESULT_OVERVIEW_ROLE],
                visualization_exemption=str(raw_contract.get("visualization_exemption") or "").strip(),
            )
        return cls(
            id=rid,
            section=str(payload.get("section") or payload.get("problem_section") or "").strip(),
            verified=payload.get("verified", True) is not False and str(payload.get("status") or "verified").lower() != "blocked",
            result_type=str(payload.get("result_type") or payload.get("type") or "unknown").strip() or "unknown",
            claim_text=str(payload.get("claim_text") or payload.get("name") or "").strip(),
            data_artifacts=_coerce_text_list(payload.get("data_artifacts") or payload.get("source_data")),
            columns=payload.get("columns") if isinstance(payload.get("columns"), dict) else {},
            visualization_contract=contract,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.visualization_contract:
            payload["visualization_contract"] = self.visualization_contract.to_dict()
        return payload


def normalize_verified_results(result_registry: dict[str, Any] | None) -> list[VerifiedResult]:
    verified = result_registry.get("verified_results", []) if isinstance(result_registry, dict) else []
    if not isinstance(verified, list):
        return []
    results: list[VerifiedResult] = []
    for item in verified:
        if not isinstance(item, dict):
            continue
        result = VerifiedResult.from_legacy(item)
        if result.id and result.verified:
            results.append(result)
    return results


def result_overview_request(
    result: VerifiedResult,
    chart_language: str,
    data_files: list[str] | None = None,
) -> dict[str, Any]:
    caption = canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language)
    section = result.section or "problem_1"
    request_id = f"fig_{section}_{RESULT_OVERVIEW_ROLE}"
    source_data = data_files or result.data_artifacts or ["result_registry.json"]
    return {
        "id": request_id,
        "problem_section": section,
        "subproblem_id": section,
        "figure_kind": "result_figure",
        "semantic_role": RESULT_OVERVIEW_ROLE,
        "role": RESULT_OVERVIEW_ROLE,
        "canonical_caption": caption,
        "purpose": caption,
        "chart_intent": "numeric_overview",
        "view_type": "numeric_overview",
        "required": True,
        "must_include_in_paper": True,
        "chart_language": chart_language,
        "language": chart_language,
        "required_columns": [],
        "required_data": source_data,
        "depends_on": {
            "model_objects": [],
            "formulas": [],
            "data_files": source_data,
            "result_ids": [result.id],
        },
        "linked_result_ids": [result.id],
        "expected_content": [caption],
    }
