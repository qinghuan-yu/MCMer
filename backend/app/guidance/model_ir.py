"""Typed, executable model semantics shared by every guidance problem."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IRModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SymbolExpression(IRModel):
    kind: Literal["symbol"] = "symbol"
    symbol: str = Field(min_length=1, max_length=100)


class LiteralExpression(IRModel):
    kind: Literal["literal"] = "literal"
    value: float
    unit: str | None = Field(default=None, max_length=80)


class BinaryExpression(IRModel):
    kind: Literal["binary"] = "binary"
    operator: Literal["add", "subtract", "multiply", "divide", "power"]
    left: Expression
    right: Expression


class FunctionExpression(IRModel):
    kind: Literal["function"] = "function"
    function: Literal["abs", "exp", "log", "sin", "cos", "min", "max"]
    arguments: list[Expression] = Field(min_length=1, max_length=8)


Expression = Annotated[
    SymbolExpression | LiteralExpression | BinaryExpression | FunctionExpression,
    Field(discriminator="kind"),
]

BinaryExpression.model_rebuild(_types_namespace={"Expression": Expression})
FunctionExpression.model_rebuild(_types_namespace={"Expression": Expression})


class VariableSpec(IRModel):
    id: str = Field(min_length=1, max_length=100)
    role: Literal["observed", "unknown_parameter", "decision", "state", "derived"]
    symbol: str = Field(min_length=1, max_length=80)
    unit: str = Field(min_length=1, max_length=80)
    shape: list[str] = Field(default_factory=list, max_length=4)


class DataReference(IRModel):
    id: str = Field(min_length=1, max_length=100)
    source_ref: str = Field(min_length=1, max_length=300)
    fields: dict[str, str] = Field(min_length=1)
    unit_overrides: dict[str, str] = Field(default_factory=dict)
    group_by: list[str] = Field(default_factory=list, max_length=8)


class EquationSpec(IRModel):
    id: str = Field(min_length=1, max_length=100)
    left: Expression
    right: Expression


class ConstraintSpec(IRModel):
    id: str = Field(min_length=1, max_length=100)
    relation: Literal["equal", "less_equal", "greater_equal"]
    left: Expression
    right: Expression


class OperatorInvocation(IRModel):
    node_id: str = Field(min_length=1, max_length=100)
    operator_id: str = Field(min_length=1, max_length=160)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: list[str] = Field(min_length=1, max_length=20)
    parameters: dict[str, Any] = Field(default_factory=dict)
    random_seed: int | None = None


class CandidateModel(IRModel):
    id: str = Field(min_length=1, max_length=100)
    role: Literal["baseline", "recommended", "alternative"]
    operator_node_ids: list[str] = Field(min_length=1, max_length=20)
    selection_metric: str = Field(min_length=1, max_length=100)
    applicability_boundary: str = Field(min_length=1, max_length=500)


class ValidationPlan(IRModel):
    model_families: list[
        Literal[
            "regression",
            "forecast",
            "optimization",
            "evaluation",
            "simulation",
            "inverse_problem",
            "graph",
            "differential_equation",
        ]
    ] = Field(min_length=1, max_length=8)
    identifiability_checks: list[str] = Field(min_length=1, max_length=12)
    evidence_checks: list[str] = Field(min_length=1, max_length=12)
    robustness_checks: list[str] = Field(min_length=1, max_length=12)


class FinalAnswerSpec(IRModel):
    id: str = Field(min_length=1, max_length=100)
    item: str = Field(min_length=1, max_length=200)
    value_ref: str = Field(min_length=1, max_length=160)
    status: Literal[
        "source_locked",
        "evidence_verified",
        "chosen_under_prior",
        "candidate_design",
    ]
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class SolvableModelIR(IRModel):
    execution_status: Literal["solvable"]
    subproblem_id: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=500)
    variables: list[VariableSpec] = Field(min_length=1, max_length=100)
    data: list[DataReference] = Field(min_length=1, max_length=30)
    equations: list[EquationSpec] = Field(min_length=1, max_length=50)
    constraints: list[ConstraintSpec] = Field(default_factory=list, max_length=50)
    operators: list[OperatorInvocation] = Field(min_length=1, max_length=100)
    candidate_models: list[CandidateModel] = Field(min_length=2, max_length=12)
    validation: ValidationPlan
    final_answers: list[FinalAnswerSpec] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def require_baseline_and_recommended_models(self) -> SolvableModelIR:
        roles = {candidate.role for candidate in self.candidate_models}
        missing = {"baseline", "recommended"} - roles
        if missing:
            raise ValueError(f"candidate_models missing roles: {sorted(missing)}")
        return self


class BlockedModelIR(IRModel):
    execution_status: Literal["blocked"]
    subproblem_id: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=500)
    blocking_reason: str = Field(min_length=1, max_length=600)
    missing_inputs: list[str] = Field(min_length=1, max_length=30)
    recovery_action: str = Field(min_length=1, max_length=500)
    expected_outputs: list[str] = Field(min_length=1, max_length=30)


SubproblemIR = Annotated[
    SolvableModelIR | BlockedModelIR,
    Field(discriminator="execution_status"),
]


class ModelIR(IRModel):
    schema_version: Literal["1.0"] = "1.0"
    model_id: str = Field(min_length=1, max_length=120)
    subproblems: list[SubproblemIR] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def require_unique_subproblem_ids(self) -> ModelIR:
        identifiers = [item.subproblem_id for item in self.subproblems]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("subproblem ids must be unique")
        return self


class ApprovedModelIR(IRModel):
    schema_version: Literal["1.0"] = "1.0"
    review_status: Literal["approved", "revision_required", "blocked"]
    model_id: str = Field(min_length=1, max_length=120)
    model_ir_ref: str = Field(default="model_ir.json", min_length=1, max_length=300)
    review_ref: str = Field(default="model_review.json", min_length=1, max_length=300)
    approved_model_ir: ModelIR


class ModelIRReview(IRModel):
    schema_version: Literal["1.0"] = "1.0"
    model_id: str = Field(min_length=1, max_length=120)
    status: Literal["approved", "revision_required", "blocked"]
    problem_spec_ref: str = Field(default="problem_spec.json", min_length=1, max_length=300)
    model_ir_ref: str = Field(default="model_ir.json", min_length=1, max_length=300)
    identifiability: str = "unknown"
    dimensional_consistency: str = "unknown"
    data_sufficiency: str = "unknown"
    computational_feasibility: str = "unknown"
    blocking_issues: list[str] = Field(default_factory=list)
    required_revisions: list[str] = Field(default_factory=list)
