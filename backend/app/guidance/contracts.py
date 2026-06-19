"""File contracts for the high-trust guidance lifecycle."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProblemSpec(BaseModel):
    schema_version: str = "1.0"
    task_summary: str
    subproblems: list[dict[str, Any]] = Field(default_factory=list)
    data_sources: list[dict[str, Any]] = Field(default_factory=list)
    locked_facts: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class ModelSpec(BaseModel):
    schema_version: str = "1.0"
    model_id: str
    problem_spec_ref: str = "problem_spec.json"
    selected_method: str
    candidate_methods: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    symbols: list[dict[str, Any]] = Field(default_factory=list)
    equations: list[str] = Field(default_factory=list)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    solve_steps: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    figure_requests: list[dict[str, Any]] = Field(default_factory=list)


class ModelReview(BaseModel):
    schema_version: str = "1.0"
    model_id: str
    status: Literal["approved", "revision_required", "blocked"]
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
    schema_version: str = "1.0"
    language: Literal["python"] = "python"
    entrypoint: str = "solve.py"
    source_code: str


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
