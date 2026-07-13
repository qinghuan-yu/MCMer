"""File contracts for the high-trust guidance lifecycle."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str
    status: Literal["available", "missing"]
    matched_files: list[str] = Field(default_factory=list)
    note: str


class ProblemSpec(BaseModel):
    schema_version: str = "1.0"
    task_summary: str
    subproblems: list[dict[str, Any]] = Field(default_factory=list)
    data_sources: list[dict[str, Any]] = Field(default_factory=list)
    source_requirements: list[SourceRequirement] = Field(default_factory=list)
    locked_facts: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def assign_stable_subproblem_ids(self) -> ProblemSpec:
        normalized: list[dict[str, Any]] = []
        for index, subproblem in enumerate(self.subproblems, start=1):
            item = dict(subproblem)
            subproblem_id = str(item.get("id") or "").strip()
            item["id"] = canonical_subproblem_id(subproblem_id) or f"problem{index}"
            normalized.append(item)
        self.subproblems = normalized
        return self


def canonical_subproblem_id(value: Any) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(?i)(?:problem|prob|p|subproblem|subproblem_)?\s*[_-]?\s*(\d+)", text)
    if match:
        return f"problem{int(match.group(1))}"
    return text


VerificationLayerStatus = Literal["passed", "failed", "blocked", "not_assessed"]


class VerificationLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerificationLayerStatus
    reasons: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class ModelAdequacyLayer(VerificationLayer):
    candidate_roles: list[Literal["baseline", "recommended", "alternative"]] = Field(
        min_length=1
    )
    checks: dict[str, VerificationLayerStatus] = Field(default_factory=dict)

    @model_validator(mode="after")
    def prevent_unsupported_pass(self) -> ModelAdequacyLayer:
        if self.status != "passed":
            return self
        roles = set(self.candidate_roles)
        if "baseline" not in roles:
            raise ValueError("model_adequate requires a baseline candidate")
        if "recommended" not in roles:
            raise ValueError("model_adequate requires a recommended candidate")
        failed_checks = [
            name
            for name, status in self.checks.items()
            if status in {"failed", "blocked", "not_assessed"}
        ]
        if failed_checks:
            raise ValueError(
                "model_adequate cannot pass while checks fail: "
                + ", ".join(sorted(failed_checks))
            )
        return self


class CounterevidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subproblem_id: str = Field(min_length=1)
    challenged_candidate_id: str = Field(min_length=1)
    status: Literal["excluded", "not_excluded", "not_assessed"]
    exclusion_reason: str = Field(min_length=1)
    check_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    applicability_boundary: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_evidence_for_exclusion(self) -> CounterevidenceRecord:
        if self.status != "excluded":
            return self
        if not self.check_refs:
            raise ValueError("excluded route requires check_refs")
        if not self.evidence_refs:
            raise ValueError("excluded route requires evidence_refs")
        return self


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    model_id: str
    status: Literal["verified", "partial", "blocked"] = "partial"
    execution_verified: VerificationLayer
    evidence_supported: VerificationLayer
    model_adequate: ModelAdequacyLayer
    counterevidence_records: list[CounterevidenceRecord] = Field(default_factory=list)
    input_checks: list[dict[str, Any]] = Field(default_factory=list)
    equation_checks: list[dict[str, Any]] = Field(default_factory=list)
    unit_checks: list[dict[str, Any]] = Field(default_factory=list)
    constraint_checks: list[dict[str, Any]] = Field(default_factory=list)
    residual_checks: list[dict[str, Any]] = Field(default_factory=list)
    sensitivity_checks: list[dict[str, Any]] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    artifact_refs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def aggregate_layer_statuses(self) -> VerificationReport:
        if self.model_adequate.status == "passed" and not any(
            record.status == "excluded" for record in self.counterevidence_records
        ):
            raise ValueError(
                "model_adequate requires counterevidence for at least one excluded route"
            )
        if self.execution_verified.status != "passed":
            self.status = "blocked"
        elif (
            self.evidence_supported.status == "passed"
            and self.model_adequate.status == "passed"
        ):
            self.status = "verified"
        else:
            self.status = "partial"
        return self
