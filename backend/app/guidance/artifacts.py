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


class ResultRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified_results: list[VerifiedResult]
    blocked_results: list[BlockedResult] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


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
