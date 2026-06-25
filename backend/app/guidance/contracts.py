"""File contracts for the high-trust guidance lifecycle."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CORE_EXECUTION_OUTPUTS = (
    "solver_results.json",
    "parameter_registry.json",
    "result_registry.json",
    "figure_registry.json",
)


class ProblemSpec(BaseModel):
    schema_version: str = "1.0"
    task_summary: str
    subproblems: list[dict[str, Any]] = Field(default_factory=list)
    data_sources: list[dict[str, Any]] = Field(default_factory=list)
    locked_facts: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class ParameterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_id: str
    symbol: str
    meaning: str
    unit: str
    source_type: Literal["given", "data", "estimated", "assumption"]
    source_detail: str
    estimation_method: str
    constraints: list[str] = Field(min_length=1)


class CandidateMethodSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    selected: bool
    reason: str


class SubproblemModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subproblem_id: str
    objective: str
    selected_method: str
    rationale: str
    equations: list[str] = Field(min_length=1)
    parameters: list[ParameterDefinition] = Field(min_length=1)
    constraints: list[str] = Field(min_length=1)
    solve_steps: list[str] = Field(min_length=1)
    expected_results: list[str] = Field(min_length=1)


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
    candidate_methods: list[CandidateMethodSpec] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    symbols: list[dict[str, Any]] = Field(default_factory=list)
    subproblem_models: list[SubproblemModelSpec] = Field(min_length=1)
    figure_requests: list[dict[str, Any]] = Field(default_factory=list)
    review_resolutions: list[ReviewResolution] = Field(default_factory=list)


class RevisedModelSpec(ModelSpec):
    review_resolutions: list[ReviewResolution] = Field(min_length=1)


def execution_required_outputs(model_spec: ModelSpec) -> list[str]:
    figure_paths = [str(request["path"]) for request in model_spec.figure_requests]
    return [*CORE_EXECUTION_OUTPUTS, *figure_paths]


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


class GeneratedProgram(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    language: Literal["python"] = "python"
    entrypoint: str = "solve.py"
    source_code: str
    declared_outputs: list[str] = Field(min_length=1)


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


class VerificationReport(BaseModel):
    schema_version: str = "1.0"
    model_id: str
    status: Literal["verified", "partial", "blocked"]
    input_checks: list[dict[str, Any]] = Field(default_factory=list)
    equation_checks: list[dict[str, Any]] = Field(default_factory=list)
    unit_checks: list[dict[str, Any]] = Field(default_factory=list)
    constraint_checks: list[dict[str, Any]] = Field(default_factory=list)
    residual_checks: list[dict[str, Any]] = Field(default_factory=list)
    sensitivity_checks: list[dict[str, Any]] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    artifact_refs: dict[str, Any] = Field(default_factory=dict)
