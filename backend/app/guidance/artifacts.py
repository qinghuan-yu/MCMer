"""Strong file contracts emitted by a generated guidance solver."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParameterRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    symbol: str
    name: str
    value: float | int | str | bool
    unit: str
    source_type: str
    source_ref: str
    trust_status: str


class ParameterRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameters: list[ParameterRecord]
    summary: dict = Field(default_factory=dict)


class VerifiedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    result_type: str
    claim_text: str
    metrics: dict[str, float] = Field(min_length=1)
    source_data: list[str] = Field(min_length=1)


class BlockedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    message: str
    reason: str
    source_data: list[str] = Field(min_length=1)


ReaderDecisionTopic = Literal[
    "problem_context",
    "identifiability",
    "route_choice",
    "final_answers",
    "claim_sources",
]
ClaimStatus = Literal[
    "source_locked",
    "evidence_verified",
    "chosen_under_prior",
    "candidate_design",
    "blocked",
]


class ReaderDecisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: ReaderDecisionTopic
    question: str = Field(min_length=1)
    judgment: str = Field(min_length=1)
    action: str = Field(min_length=1)


class MethodRouteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str = Field(min_length=1)
    status: Literal["recommended", "rejected"]
    reason: str = Field(min_length=1)
    boundary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class IdentifiabilityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subproblem_id: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    unknowns: str = Field(min_length=1)
    equation: str = Field(min_length=1)
    rank_condition: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    missing_information: str = Field(min_length=1)
    selection_rule: str = Field(min_length=1)
    guidance: str = Field(min_length=1)


class ClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    status: ClaimStatus
    claim_text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    risk: str = Field(min_length=1)


class FinalAnswerRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subproblem_id: str = Field(min_length=1)
    answer_item: str = Field(min_length=1)
    answer: str | float | int | bool | None = None
    status: ClaimStatus
    evidence_ids: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None

    @model_validator(mode="after")
    def require_answer_or_blocking_reason(self):
        if self.status == "blocked":
            if not str(self.blocked_reason or "").strip():
                raise ValueError("blocked final answer requires blocked_reason")
            return self
        if self.answer is None or not str(self.answer).strip():
            raise ValueError("final answer row requires a concrete answer")
        if not self.evidence_ids:
            raise ValueError("final answer row requires evidence_ids")
        return self


class FinalAnswerTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    rows: list[FinalAnswerRow] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)


class ReaderGuidanceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    coverage_status: Literal["verified", "partial", "blocked"]
    guidance_notes: list[str] = Field(default_factory=list)
    reader_decision_guide: list[ReaderDecisionItem] = Field(min_length=5, max_length=5)
    method_route_comparison: list[MethodRouteItem] = Field(min_length=2)
    identifiability_analysis: list[IdentifiabilityItem] = Field(min_length=1)
    claim_registry: list[ClaimRecord] = Field(min_length=1)
    final_answer_tables: list[FinalAnswerTable] = Field(min_length=1)

    @model_validator(mode="after")
    def require_complete_reader_decisions_and_route_choices(self):
        topics = [item.topic for item in self.reader_decision_guide]
        expected_topics = {
            "problem_context",
            "identifiability",
            "route_choice",
            "final_answers",
            "claim_sources",
        }
        if set(topics) != expected_topics or len(topics) != len(set(topics)):
            raise ValueError("reader_decision_guide must cover each required topic exactly once")
        route_statuses = {item.status for item in self.method_route_comparison}
        if route_statuses != {"recommended", "rejected"}:
            raise ValueError("method_route_comparison requires recommended and rejected routes")
        return self


class ResultRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified_results: list[VerifiedResult]
    blocked_results: list[BlockedResult] = Field(default_factory=list)
    summary: ReaderGuidanceSummary | None = None

    @model_validator(mode="after")
    def require_unique_result_ids(self):
        verified_ids = [item.id for item in self.verified_results]
        blocked_ids = [item.id for item in self.blocked_results]
        duplicates = _duplicates([*verified_ids, *blocked_ids])
        if duplicates:
            raise ValueError(f"duplicate result id: {duplicates[0]}")
        if self.verified_results and self.summary is None:
            raise ValueError("verified results require ReaderGuidanceSummary")
        if self.summary is not None:
            if self.summary.verified_count != len(self.verified_results):
                raise ValueError("summary verified_count does not match verified_results")
            if self.summary.blocked_count != len(self.blocked_results):
                raise ValueError("summary blocked_count does not match blocked_results")
        return self


def reader_guidance_reference_issues(
    result_registry: ResultRegistry,
    parameter_registry: ParameterRegistry,
) -> list[str]:
    summary = result_registry.summary
    if summary is None:
        return ["result_registry missing ReaderGuidanceSummary"]

    artifact_ids = {
        *(item.id for item in result_registry.verified_results),
        *(item.id for item in result_registry.blocked_results),
        *(item.id for item in parameter_registry.parameters),
    }
    claim_ids = {item.id for item in summary.claim_registry}
    issues: list[str] = []

    for claim in summary.claim_registry:
        unknown = sorted(set(claim.evidence_ids) - artifact_ids)
        if unknown:
            issues.append(
                f"claim {claim.id} references unknown result/parameter evidence: {', '.join(unknown)}"
            )

    route_evidence_ids = artifact_ids | claim_ids
    for route in summary.method_route_comparison:
        unknown = sorted(set(route.evidence_ids) - route_evidence_ids)
        if unknown:
            issues.append(
                f"route {route.route} references unknown evidence: {', '.join(unknown)}"
            )

    for table in summary.final_answer_tables:
        for row in table.rows:
            unknown = sorted(set(row.evidence_ids) - artifact_ids)
            if unknown:
                issues.append(
                    f"final answer {table.id}/{row.answer_item} references unknown evidence: "
                    + ", ".join(unknown)
                )
    return issues


class FigureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    role: Literal[
        "data_overview",
        "model_explanation",
        "result_fit",
        "verification_diagnostic",
    ]
    source_data: list[str] = Field(min_length=1)
    linked_result_ids: list[str] = Field(min_length=1)


class FigureRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figures: list[FigureRecord]


class StabilityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    max_relative_change: float = Field(ge=0, allow_inf_nan=False)
    threshold: float = Field(ge=0, allow_inf_nan=False)


class RegressionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["regression"]
    subproblem_id: str
    result_id: str
    observed: list[float] = Field(min_length=1)
    predicted: list[float] = Field(min_length=1)
    residuals: list[float] = Field(min_length=1)
    constraint_values: list[float] = Field(min_length=1)
    parameter_stability: StabilityEvidence


class ConstraintEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float = Field(allow_inf_nan=False)
    lower_bound: float | None = Field(default=None, allow_inf_nan=False)
    upper_bound: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_bound(self):
        if self.lower_bound is None and self.upper_bound is None:
            raise ValueError("constraint evidence requires a lower or upper bound")
        return self


class OptimizationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["optimization"]
    subproblem_id: str
    result_id: str
    objective_value: float = Field(allow_inf_nan=False)
    baseline_objective: float = Field(allow_inf_nan=False)
    constraints: list[ConstraintEvidence] = Field(min_length=1)
    sensitivity: StabilityEvidence


class ForecastEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["forecast"]
    subproblem_id: str
    result_id: str
    train_observed: list[float] = Field(min_length=1)
    train_predicted: list[float] = Field(min_length=1)
    holdout_observed: list[float] = Field(min_length=1)
    holdout_predicted: list[float] = Field(min_length=1)
    forecast_periods: list[float] = Field(min_length=1)
    forecast_values: list[float] = Field(min_length=1)
    holdout_tolerance: float = Field(ge=0, allow_inf_nan=False)
    stability: StabilityEvidence


SolverEvidence = Annotated[
    RegressionEvidence | OptimizationEvidence | ForecastEvidence,
    Field(discriminator="kind"),
]


class SolverResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: list[SolverEvidence] = Field(min_length=1)


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates
