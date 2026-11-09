"""Typed, executable model semantics shared by every guidance problem."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IRModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _require_unique_source_fact_ids(source_fact_ids: list[str]) -> list[str]:
    if len(source_fact_ids) != len(set(source_fact_ids)):
        raise ValueError("source_fact_ids must be unique")
    return source_fact_ids


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


IndexValue = str | int


class IndexSetSpec(IRModel):
    id: str = Field(min_length=1, max_length=100)
    values: list[IndexValue] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_unique_values(self) -> IndexSetSpec:
        if any(isinstance(value, bool) for value in self.values):
            raise ValueError("index values cannot be booleans")
        if len(self.values) != len(set(self.values)):
            raise ValueError(f"index set {self.id!r} contains duplicate values")
        return self


class IndexedParameterValue(IRModel):
    coordinates: list[IndexValue] = Field(default_factory=list, max_length=4)
    value: float
    source_ref: str = Field(min_length=1, max_length=300)
    source_unit: str | None = Field(default=None, min_length=1, max_length=80)


class RuntimeIndexedParameterSource(IRModel):
    artifact_ref: str = Field(min_length=1, max_length=100)
    field_path: list[str] = Field(min_length=1, max_length=8)


class IndexedParameterSpec(IRModel):
    id: str = Field(min_length=1, max_length=100)
    index_sets: list[str] = Field(default_factory=list, max_length=4)
    source_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    values: list[IndexedParameterValue] = Field(default_factory=list, max_length=10000)
    dense_values: list[float] = Field(default_factory=list, max_length=10000)
    dense_source_ref: str | None = Field(default=None, min_length=1, max_length=300)
    runtime_source: RuntimeIndexedParameterSource | None = None

    @model_validator(mode="after")
    def require_one_value_encoding(self) -> IndexedParameterSpec:
        _require_unique_source_fact_ids(self.source_fact_ids)
        source_count = sum((
            bool(self.values),
            bool(self.dense_values),
            self.runtime_source is not None,
        ))
        if source_count != 1:
            raise ValueError(
                "parameter requires exactly one of values, dense_values, or runtime_source"
            )
        if self.dense_values and self.dense_source_ref is None:
            raise ValueError("dense_values require dense_source_ref")
        if not self.dense_values and self.dense_source_ref is not None:
            raise ValueError("dense_source_ref is only valid with dense_values")
        return self


class IndexedVariableSpec(IRModel):
    id: str = Field(min_length=1, max_length=100)
    index_sets: list[str] = Field(default_factory=list, max_length=4)
    domain: Literal["continuous", "integer", "binary"]
    lower_bound: float | None = 0.0
    upper_bound: float | None = None

    @model_validator(mode="after")
    def require_ordered_bounds(self) -> IndexedVariableSpec:
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound > self.upper_bound
        ):
            raise ValueError(f"variable {self.id!r} lower bound exceeds upper bound")
        if self.domain == "binary" and (
            self.lower_bound not in (None, 0.0)
            or self.upper_bound not in (None, 1.0)
        ):
            raise ValueError("binary variable bounds must be zero and one when specified")
        return self


class IndexSelector(IRModel):
    binder: str | None = Field(default=None, min_length=1, max_length=100)
    value: IndexValue | None = None
    offset: int = Field(default=0, ge=-10000, le=10000)

    @model_validator(mode="after")
    def require_one_selector_source(self) -> IndexSelector:
        if (self.binder is None) == (self.value is None):
            raise ValueError("index selector requires exactly one of binder or value")
        return self


class ConstantLinearValue(IRModel):
    kind: Literal["constant"] = "constant"
    value: float


class ParameterLinearValue(IRModel):
    kind: Literal["parameter"] = "parameter"
    parameter_id: str = Field(min_length=1, max_length=100)
    selectors: list[IndexSelector] = Field(default_factory=list, max_length=4)
    multiplier: float = 1.0


LinearValue = Annotated[
    ConstantLinearValue | ParameterLinearValue,
    Field(discriminator="kind"),
]


class IndexedVariableReference(IRModel):
    variable_id: str = Field(min_length=1, max_length=100)
    selectors: list[IndexSelector] = Field(default_factory=list, max_length=4)


class IndexedLinearTerm(IRModel):
    for_each: dict[str, str] = Field(default_factory=dict, max_length=4)
    coefficient: LinearValue
    variable: IndexedVariableReference


class IndexedLinearObjective(IRModel):
    sense: Literal["minimize", "maximize"]
    terms: list[IndexedLinearTerm] = Field(min_length=1, max_length=1000)


class IndexedLinearConstraint(IRModel):
    id: str = Field(min_length=1, max_length=100)
    source_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    for_each: dict[str, str] = Field(default_factory=dict, max_length=4)
    terms: list[IndexedLinearTerm] = Field(min_length=1, max_length=1000)
    relation: Literal["equal", "less_equal", "greater_equal"]
    rhs: LinearValue

    @model_validator(mode="after")
    def require_unique_source_fact_ids(self) -> IndexedLinearConstraint:
        _require_unique_source_fact_ids(self.source_fact_ids)
        return self


class IndexedLinearDataSpec(IRModel):
    index_sets: list[IndexSetSpec] = Field(min_length=1, max_length=20)
    parameters: list[IndexedParameterSpec] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_unique_identifiers(self) -> IndexedLinearDataSpec:
        for label, identifiers in (
            ("index set", [item.id for item in self.index_sets]),
            ("parameter", [item.id for item in self.parameters]),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} ids must be unique")
        return self


class IndexedLinearCoreSpec(IndexedLinearDataSpec):
    variables: list[IndexedVariableSpec] = Field(min_length=1, max_length=100)
    objective: IndexedLinearObjective

    @model_validator(mode="after")
    def require_unique_variables(self) -> IndexedLinearCoreSpec:
        identifiers = [item.id for item in self.variables]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("indexed variable ids must be unique")
        return self


class IndexedLinearModelSpec(IndexedLinearCoreSpec):
    constraints: list[IndexedLinearConstraint] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_unique_constraint_ids(self) -> IndexedLinearModelSpec:
        identifiers = [item.id for item in self.constraints]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("indexed constraint ids must be unique")
        return self


class OperatorInvocation(IRModel):
    node_id: str = Field(min_length=1, max_length=100)
    operator_id: str = Field(min_length=1, max_length=160)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: list[str] = Field(min_length=1, max_length=20)
    parameters: dict[str, Any] = Field(default_factory=dict)
    random_seed: int | None = None
    source_fact_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_unique_source_fact_ids(self) -> OperatorInvocation:
        _require_unique_source_fact_ids(self.source_fact_ids)
        return self


class OperatorGraphExecutionComponent(IRModel):
    kind: Literal["operator_graph"]
    component_id: str = Field(min_length=1, max_length=100)
    variables: list[VariableSpec] = Field(min_length=1, max_length=100)
    operators: list[OperatorInvocation] = Field(min_length=1, max_length=100)


class IndexedLinearExecutionComponent(IRModel):
    kind: Literal["indexed_linear"]
    component_id: str = Field(min_length=1, max_length=100)
    model: IndexedLinearModelSpec
    solver_node_id: str = Field(min_length=1, max_length=100)
    outputs: list[str] = Field(min_length=1, max_length=20)


ExecutionComponent = Annotated[
    OperatorGraphExecutionComponent | IndexedLinearExecutionComponent,
    Field(discriminator="kind"),
]


class CandidateModel(IRModel):
    id: str = Field(min_length=1, max_length=100)
    role: Literal["baseline", "recommended", "alternative"]
    operator_node_ids: list[str] = Field(min_length=1, max_length=20)
    selection_metric: str = Field(min_length=1, max_length=100)
    applicability_boundary: str = Field(min_length=1, max_length=500)




class ModelingAssumptionSpec(IRModel):
    id: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=600)
    basis: Literal[
        "problem_ambiguity",
        "standard_modeling_convention",
        "scenario_choice",
    ]
    source_refs: list[str] = Field(min_length=1, max_length=20)
    affected_model_elements: list[str] = Field(min_length=1, max_length=30)
    alternative_scenarios: list[str] = Field(min_length=1, max_length=12)
    sensitivity_checks: list[str] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def require_uncertainty_source_for_ambiguity(self) -> ModelingAssumptionSpec:
        if self.basis == "problem_ambiguity" and not all(
            re.fullmatch(
                r"problem_spec\.json#uncertainties/[A-Za-z][A-Za-z0-9_.-]*",
                reference,
            )
            for reference in self.source_refs
        ):
            raise ValueError(
                "problem_ambiguity assumptions must reference stable problem_spec uncertainty ids"
            )
        return self


class ValidationPlan(IRModel):
    model_families: list[
        Literal[
            "regression",
            "forecast",
            "future_forecast",
            "optimization",
            "mixed_integer_linear_optimization",
            "evaluation",
            "simulation",
            "inverse_problem",
            "graph",
            "differential_equation",
        ]
    ] = Field(
        min_length=1, max_length=8,
        description=(
            "Declare the validation families of the actual execution methods. "
            "optimization requires optimization.constrained_quadratic_multistart; "
            "optimization.milp and indexed_linear_model require mixed_integer_linear_optimization. "
            "Declare both only when both methods are present, and never omit the family of a present method."
        ),
    )
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
    evidence_ids: list[str] = Field(
        min_length=1, max_length=20,
        description=(
            "Executed node IDs or registered EvidenceProfile evidence_id values whose source_ref "
            "is declared in this subproblem's data. Preserve source IDs; never invent identifiers."
        ),
    )


class FactBindingSpec(IRModel):
    fact_id: str = Field(min_length=1, max_length=120)
    model_paths: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def require_json_pointers(self) -> FactBindingSpec:
        invalid = [path for path in self.model_paths if not path.startswith("/")]
        if invalid:
            raise ValueError("fact binding model_paths must be JSON pointers")
        if len(self.model_paths) != len(set(self.model_paths)):
            raise ValueError("fact binding model_paths must be unique")
        return self


def _build_fact_bindings(
    execution_components: list[ExecutionComponent],
) -> list[FactBindingSpec]:
    paths_by_fact: dict[str, list[str]] = {}
    for component_index, component in enumerate(execution_components):
        component_path = f"/execution_components/{component_index}"
        if isinstance(component, OperatorGraphExecutionComponent):
            nodes = (
                (operator.source_fact_ids, f"{component_path}/operators/{operator_index}")
                for operator_index, operator in enumerate(component.operators)
            )
        else:
            nodes = (
                *(
                    (
                        parameter.source_fact_ids,
                        f"{component_path}/model/parameters/{parameter_index}",
                    )
                    for parameter_index, parameter in enumerate(
                        component.model.parameters
                    )
                ),
                *(
                    (
                        constraint.source_fact_ids,
                        f"{component_path}/model/constraints/{constraint_index}",
                    )
                    for constraint_index, constraint in enumerate(
                        component.model.constraints
                    )
                ),
            )
        for source_fact_ids, model_path in nodes:
            for fact_id in source_fact_ids:
                paths_by_fact.setdefault(fact_id, []).append(model_path)
    return [
        FactBindingSpec(fact_id=fact_id, model_paths=model_paths)
        for fact_id, model_paths in paths_by_fact.items()
    ]


class SolvableModelIR(IRModel):
    execution_status: Literal["solvable"]
    subproblem_id: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=500)
    execution_components: list[ExecutionComponent] = Field(min_length=1, max_length=2)
    data: list[DataReference] = Field(min_length=1, max_length=30)
    equations: list[EquationSpec] = Field(default_factory=list, max_length=50)
    constraints: list[ConstraintSpec] = Field(default_factory=list, max_length=50)
    assumptions: list[ModelingAssumptionSpec] = Field(max_length=30)
    fact_bindings: list[FactBindingSpec] = Field(max_length=100)
    candidate_models: list[CandidateModel] = Field(min_length=2, max_length=12)
    validation: ValidationPlan
    final_answers: list[FinalAnswerSpec] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def require_baseline_and_recommended_models(self) -> SolvableModelIR:
        roles = {candidate.role for candidate in self.candidate_models}
        missing = {"baseline", "recommended"} - roles
        if missing:
            raise ValueError(f"candidate_models missing roles: {sorted(missing)}")
        component_ids = [component.component_id for component in self.execution_components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("execution component ids must be unique")
        component_kinds = [component.kind for component in self.execution_components]
        if len(component_kinds) != len(set(component_kinds)):
            raise ValueError("execution component kinds must be unique")
        node_ids = [
            node_id
            for component in self.execution_components
            for node_id in (
                [operator.node_id for operator in component.operators]
                if isinstance(component, OperatorGraphExecutionComponent)
                else [component.solver_node_id]
            )
        ]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("execution node ids must be unique across components")
        unknown_nodes = sorted({
            node_id
            for candidate in self.candidate_models
            for node_id in candidate.operator_node_ids
            if node_id not in node_ids
        })
        if unknown_nodes:
            raise ValueError(
                f"candidate_models reference unknown execution nodes: {unknown_nodes}"
            )
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
        assumption_ids = [
            assumption.id
            for item in self.subproblems
            if isinstance(item, SolvableModelIR)
            for assumption in item.assumptions
        ]
        if len(assumption_ids) != len(set(assumption_ids)):
            raise ValueError("modeling assumption ids must be globally unique")
        return self
