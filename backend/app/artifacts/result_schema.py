"""Verified result schema shared by solver, review, and visualization stages.

This module is the protocol boundary for solver outputs.  It accepts legacy
dicts, but normalizes them into a small stable shape before downstream skills
plan figures or writer context.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.artifacts.visualization_schema import VisualizationContract


def coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


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
        contract = (
            VisualizationContract.from_legacy(raw_contract, result_id=rid)
            if isinstance(raw_contract, dict)
            else None
        )
        return cls(
            id=rid,
            section=str(payload.get("section") or payload.get("problem_section") or "").strip(),
            verified=payload.get("verified", True) is not False
            and str(payload.get("status") or "verified").lower() != "blocked",
            result_type=str(payload.get("result_type") or payload.get("type") or "unknown").strip() or "unknown",
            claim_text=str(payload.get("claim_text") or payload.get("name") or "").strip(),
            data_artifacts=coerce_text_list(payload.get("data_artifacts") or payload.get("source_data")),
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
