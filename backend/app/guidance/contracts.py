"""File contracts for the high-trust guidance lifecycle."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CORE_EXECUTION_OUTPUTS = (
    "solver_results.json",
    "parameter_registry.json",
    "result_registry.json",
    "figure_registry.json",
)


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


class ParameterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_id: str = Field(max_length=80)
    symbol: str = Field(max_length=40)
    meaning: str = Field(max_length=160)
    unit: str = Field(max_length=80)
    source_type: Literal["given", "data", "estimated", "assumption"]
    source_detail: str = Field(max_length=160)
    estimation_method: str = Field(max_length=120)
    constraints: list[str] = Field(min_length=1, max_length=4)


class CandidateMethodSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=80)
    selected: bool
    reason: str = Field(max_length=180)


class BaseSubproblemModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subproblem_id: str
    objective: str = Field(max_length=240)
    expected_results: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def canonicalize_subproblem_id(self) -> BaseSubproblemModelSpec:
        self.subproblem_id = canonical_subproblem_id(self.subproblem_id)
        return self


class SolvableSubproblemModelSpec(BaseSubproblemModelSpec):
    execution_status: Literal["solvable"]
    selected_method: str = Field(max_length=120)
    rationale: str = Field(max_length=260)
    equations: list[str] = Field(min_length=1, max_length=5)
    parameters: list[ParameterDefinition] = Field(min_length=1, max_length=8)
    constraints: list[str] = Field(min_length=1, max_length=8)
    solve_steps: list[str] = Field(min_length=1, max_length=8)


class BlockedSubproblemModelSpec(BaseSubproblemModelSpec):
    execution_status: Literal["blocked"]
    blocking_reason: str = Field(max_length=320)
    missing_inputs: list[str] = Field(min_length=1, max_length=8)
    recovery_action: str = Field(max_length=240)


SubproblemModelSpec = Annotated[
    SolvableSubproblemModelSpec | BlockedSubproblemModelSpec,
    Field(discriminator="execution_status"),
]


class ReviewResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str
    resolution: str
    affected_subproblem_ids: list[str] = Field(min_length=1)
    changed_fields: list[str] = Field(min_length=1)


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    model_id: str
    problem_spec_ref: str = "problem_spec.json"
    selected_method: str
    candidate_methods: list[CandidateMethodSpec] = Field(default_factory=list, max_length=4)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    symbols: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    subproblem_models: list[SubproblemModelSpec] = Field(min_length=1)
    coverage_status: Literal["complete", "partial", "blocked"] = "complete"
    figure_requests: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    review_resolutions: list[ReviewResolution] = Field(default_factory=list)

    @model_validator(mode="after")
    def derive_coverage_status(self) -> ModelSpec:
        statuses = {item.execution_status for item in self.subproblem_models}
        if statuses == {"solvable"}:
            self.coverage_status = "complete"
        elif statuses == {"blocked"}:
            self.coverage_status = "blocked"
        else:
            self.coverage_status = "partial"
        return self


class RevisedModelSpec(ModelSpec):
    review_resolutions: list[ReviewResolution] = Field(min_length=1)


def execution_required_outputs(model_spec: ModelSpec) -> list[str]:
    return list(CORE_EXECUTION_OUTPUTS)


def canonical_subproblem_id(value: Any) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(?i)(?:problem|prob|p|subproblem|subproblem_)?\s*[_-]?\s*(\d+)", text)
    if match:
        return f"problem{int(match.group(1))}"
    return text


def validate_review_resolutions(model_spec: ModelSpec, review: ModelReview) -> None:
    expected = set(review.required_revisions)
    resolved = {item.requirement for item in model_spec.review_resolutions}
    if resolved != expected:
        unresolved = sorted(expected - resolved)
        unexpected = sorted(resolved - expected)
        raise ValueError(
            f"unresolved review requirements: {unresolved}; unexpected resolutions: {unexpected}"
        )


def validate_subproblem_coverage(problem_spec: ProblemSpec, model_spec: ModelSpec) -> None:
    expected = [str(item.get("id") or "").strip() for item in problem_spec.subproblems]
    modeled = [item.subproblem_id.strip() for item in model_spec.subproblem_models]
    invalid_ids = not all(expected) or not all(modeled)
    duplicate_ids = len(expected) != len(set(expected)) or len(modeled) != len(set(modeled))
    if invalid_ids or duplicate_ids or set(expected) != set(modeled):
        raise ValueError(
            f"subproblem coverage mismatch: expected={expected}; modeled={modeled}"
        )


class ModelReview(BaseModel):
    schema_version: str = "1.0"
    model_id: str
    status: Literal["approved", "revision_required", "blocked"]
    problem_spec_ref: str = "problem_spec.json"
    model_spec_ref: str = "model_spec.json"
    identifiability: str = "unknown"
    dimensional_consistency: str = "unknown"
    data_sufficiency: str = "unknown"
    computational_feasibility: str = "unknown"
    blocking_issues: list[str] = Field(default_factory=list)
    required_revisions: list[str] = Field(default_factory=list)


class ApprovedModelSpec(BaseModel):
    schema_version: str = "1.0"
    review_status: Literal["approved", "revision_required", "blocked"]
    model_id: str
    model_spec_ref: str = "model_spec.json"
    review_ref: str = "model_review.json"
    approved_model: dict[str, Any] = Field(default_factory=dict)
    required_outputs: list[str] = Field(default_factory=list)


class ExecutionManifest(BaseModel):
    schema_version: str = "1.0"
    task_id: str
    model_id: str
    status: Literal["passed", "failed", "partial"]
    entrypoint: str
    program_sha256: str
    execution_count: int
    elapsed_seconds: float
    stdout: str = ""
    error_message: str = ""
    generated_files: list[str] = Field(default_factory=list)
    missing_required_outputs: list[str] = Field(default_factory=list)


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
