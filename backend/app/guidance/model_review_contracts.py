"""逐子题审核、修订与可执行投影合同。"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.guidance.model_ir import (
    IRModel,
    ModelIR,
    SolvableModelIR,
)


class ModelReviewSubmission(IRModel):
    schema_version: Literal["1.0"] = "1.0"
    model_ir_ref: str = Field(min_length=1, max_length=300)
    revision_request_ref: str | None = Field(default=None, min_length=1, max_length=300)


ReviewDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ModelReviewBinding(IRModel):
    model_sha256: ReviewDigest
    approved_model_sha256: ReviewDigest
    problem_spec_sha256: ReviewDigest
    evidence_profile_sha256: ReviewDigest
    input_catalog_sha256: ReviewDigest | None
    review_sha256: ReviewDigest
    revision_request_sha256: ReviewDigest | None


class ApprovedModelIR(IRModel):
    schema_version: Literal["1.0"] = "1.0"
    review_status: Literal["approved", "partial", "blocked"]
    model_id: str = Field(min_length=1, max_length=120)
    model_ir_ref: str = Field(default="model_ir.json", min_length=1, max_length=300)
    review_ref: str = Field(default="model_review.json", min_length=1, max_length=300)
    revision_request_ref: str | None = Field(default=None, min_length=1, max_length=300)
    executable_subproblem_ids: list[str] = Field(max_length=50)
    approved_model_ir: ModelIR
    review_binding: ModelReviewBinding | None = None

    @model_validator(mode="after")
    def require_consistent_execution_projection(self) -> ApprovedModelIR:
        identifiers = [
            subproblem.subproblem_id
            for subproblem in self.approved_model_ir.subproblems
        ]
        executable = self.executable_subproblem_ids
        if len(executable) != len(set(executable)):
            raise ValueError("executable_subproblem_ids must be unique")
        unknown = sorted(set(executable) - set(identifiers))
        if unknown:
            raise ValueError(f"unknown executable subproblem ids: {unknown!r}")
        solvable = [
            subproblem.subproblem_id
            for subproblem in self.approved_model_ir.subproblems
            if isinstance(subproblem, SolvableModelIR)
        ]
        if executable != solvable:
            raise ValueError(
                "executable subproblem ids must exactly match solvable subproblems "
                "in approved_model_ir"
            )
        blocked_count = len(identifiers) - len(solvable)
        expected_status = (
            "approved"
            if solvable and not blocked_count
            else "partial"
            if solvable
            else "blocked"
        )
        if self.review_status != expected_status:
            raise ValueError(
                f"review_status must be {expected_status!r} for the execution projection"
            )
        return self


class AssumptionReviewSpec(IRModel):
    assumption_id: str = Field(min_length=1, max_length=100)
    status: Literal["accepted", "revision_required", "rejected"]
    rationale: str = Field(min_length=1, max_length=600)
    alternative_scenario_coverage: str = Field(min_length=1, max_length=600)
    sensitivity_check_coverage: str = Field(min_length=1, max_length=600)


class RevisionRequirementSpec(IRModel):
    requirement_id: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=600)
    acceptance_criteria: str = Field(min_length=1, max_length=600)


class RevisionCheckSpec(IRModel):
    requirement_id: str = Field(min_length=1, max_length=100)
    status: Literal["accepted", "revision_required", "rejected"]
    model_paths: list[str] = Field(
        min_length=1, max_length=30,
        description="JSON Pointers relative to the current subproblem, never the whole ModelIR.",
    )
    rationale: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def require_json_pointer_paths(self) -> RevisionCheckSpec:
        invalid = [path for path in self.model_paths if not path.startswith("/")]
        if invalid:
            raise ValueError(f"model_paths must be JSON Pointers: {invalid!r}")
        if any(path == "/subproblems" or path.startswith("/subproblems/") for path in self.model_paths):
            raise ValueError("revision model_paths must be relative to the current subproblem")
        return self


class MathematicalCorrespondenceCheck(IRModel):
    requirement_id: str = Field(min_length=1, max_length=160)
    status: Literal["passed", "failed", "not_assessed"]
    model_paths: list[str] = Field(default_factory=list, max_length=30)
    rationale: str = Field(min_length=1, max_length=1600)

    @model_validator(mode="after")
    def require_mathematical_evidence(self) -> MathematicalCorrespondenceCheck:
        if self.status == "passed" and not self.model_paths:
            raise ValueError("passed mathematical check requires model_paths")
        if len(self.model_paths) != len(set(self.model_paths)):
            raise ValueError("mathematical model_paths must be unique")
        return self


class SubproblemReviewSpec(IRModel):
    subproblem_id: str = Field(min_length=1, max_length=100)
    status: Literal["approved", "revision_required", "blocked"]
    assessment_status: Literal["reviewed", "not_assessed"] = "reviewed"
    assumption_checks: list[AssumptionReviewSpec] = Field(default_factory=list, max_length=30)
    blocking_issues: list[str] = Field(default_factory=list, max_length=50)
    required_revisions: list[RevisionRequirementSpec] = Field(
        default_factory=list,
        max_length=50,
    )
    revision_checks: list[RevisionCheckSpec] = Field(default_factory=list, max_length=50)
    mathematical_checks: list[MathematicalCorrespondenceCheck] = Field(
        default_factory=list, max_length=250,
    )

    @model_validator(mode="after")
    def require_consistent_decision(self) -> SubproblemReviewSpec:
        if self.assessment_status == "not_assessed":
            if self.status != "blocked":
                raise ValueError("not_assessed review must be blocked")
            if self.assumption_checks or self.revision_checks or self.mathematical_checks or self.required_revisions:
                raise ValueError("not_assessed review cannot contain assessment content")
        assumption_ids = [item.assumption_id for item in self.assumption_checks]
        if len(assumption_ids) != len(set(assumption_ids)):
            raise ValueError("assumption check ids must be unique within a subproblem")
        mathematical_ids = [item.requirement_id for item in self.mathematical_checks]
        if len(mathematical_ids) != len(set(mathematical_ids)):
            raise ValueError("mathematical requirement ids must be unique within a subproblem")
        if self.status == "approved" and any(item.status == "failed" for item in self.mathematical_checks):
            raise ValueError("approved subproblem contains failed mathematical checks")
        requirement_ids = [item.requirement_id for item in self.required_revisions]
        check_ids = [item.requirement_id for item in self.revision_checks]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("revision requirement ids must be unique within a subproblem")
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("revision check ids must be unique within a subproblem")
        if self.status == "approved":
            if self.blocking_issues or self.required_revisions:
                raise ValueError(
                    "approved subproblem review cannot contain blocking issues or revisions"
                )
        elif self.status == "revision_required":
            if not self.required_revisions:
                raise ValueError(
                    "revision_required subproblem review must provide required_revisions"
                )
        elif not self.blocking_issues:
            raise ValueError("blocked subproblem review must provide blocking_issues")
        return self


class ModelIRReview(IRModel):
    schema_version: Literal["1.0"] = "1.0"
    model_id: str = Field(min_length=1, max_length=120)
    status: Literal["approved", "partial", "revision_required", "blocked"] | None = None
    executable_subproblem_ids: list[str] = Field(default_factory=list, max_length=50)
    problem_spec_ref: str = Field(default="problem_spec.json", min_length=1, max_length=300)
    model_ir_ref: str = Field(default="model_ir.json", min_length=1, max_length=300)
    identifiability: str = "unknown"
    dimensional_consistency: str = "unknown"
    data_sufficiency: str = "unknown"
    computational_feasibility: str = "unknown"
    subproblem_reviews: list[SubproblemReviewSpec] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def require_derived_review_summary(self) -> ModelIRReview:
        identifiers = [item.subproblem_id for item in self.subproblem_reviews]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("subproblem review ids must be unique")
        statuses = [item.status for item in self.subproblem_reviews]
        if "revision_required" in statuses:
            derived_status = "revision_required"
        else:
            approved_count = statuses.count("approved")
            derived_status = (
                "approved"
                if approved_count == len(statuses)
                else "partial"
                if approved_count
                else "blocked"
            )
        derived_executable = [
            item.subproblem_id
            for item in self.subproblem_reviews
            if item.status == "approved"
        ]
        if self.status is not None and self.status != derived_status:
            raise ValueError("review status does not match per-subproblem decisions")
        if (
            self.executable_subproblem_ids
            and self.executable_subproblem_ids != derived_executable
        ):
            raise ValueError(
                "executable_subproblem_ids do not match approved subproblem decisions"
            )
        self.status = derived_status
        self.executable_subproblem_ids = derived_executable
        return self
