"""生成草稿与单向提升合同。"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.guidance.model_ir import (
    BlockedModelIR,
    CandidateModel,
    ConstantLinearValue,
    DataReference,
    ExecutionComponent,
    FinalAnswerSpec,
    IRModel,
    IndexSelector,
    IndexSetSpec,
    IndexValue,
    IndexedLinearConstraint,
    IndexedLinearCoreSpec,
    IndexedLinearExecutionComponent,
    IndexedLinearModelSpec,
    IndexedLinearObjective,
    IndexedLinearTerm,
    IndexedParameterSpec,
    IndexedVariableReference,
    IndexedVariableSpec,
    ModelIR,
    ModelingAssumptionSpec,
    OperatorGraphExecutionComponent,
    OperatorInvocation,
    ParameterLinearValue,
    RuntimeIndexedParameterSource,
    SolvableModelIR,
    SubproblemIR,
    ValidationPlan,
    VariableSpec,
    _build_fact_bindings,
    _require_unique_source_fact_ids,
)


class CandidateModelDraft(CandidateModel):
    operator_node_ids: list[str] = Field(default_factory=list, max_length=20)


class SolvableModelIRDraft(IRModel):
    """Compact LLM contract promoted into canonical executable ModelIR."""

    execution_status: Literal["solvable"]
    execution_components: list[
        Literal["indexed_linear_model", "operator_graph"]
    ] = Field(min_length=1, max_length=2)
    subproblem_id: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=500)
    variables: list[VariableSpec] = Field(default_factory=list, max_length=100)
    data: list[DataReference] = Field(min_length=1, max_length=30)
    operators: list[OperatorInvocation] = Field(default_factory=list, max_length=100)
    assumptions: list[ModelingAssumptionSpec] = Field(max_length=30)
    candidate_models: list[CandidateModelDraft] = Field(min_length=2, max_length=12)
    validation: ValidationPlan
    final_answers: list[FinalAnswerSpec] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def require_baseline_and_recommended_models(self) -> SolvableModelIRDraft:
        roles = {candidate.role for candidate in self.candidate_models}
        missing = {"baseline", "recommended"} - roles
        if missing:
            raise ValueError(f"candidate_models missing roles: {sorted(missing)}")
        assumption_ids = [assumption.id for assumption in self.assumptions]
        if len(assumption_ids) != len(set(assumption_ids)):
            raise ValueError("modeling assumption ids must be unique")
        if len(self.execution_components) != len(set(self.execution_components)):
            raise ValueError("execution_components must be unique")
        has_operator_graph = "operator_graph" in self.execution_components
        has_indexed_model = "indexed_linear_model" in self.execution_components
        if has_operator_graph and not self.operators:
            raise ValueError("operator_graph architecture requires operators")
        if not has_operator_graph and self.operators:
            raise ValueError(
                "operators require the operator_graph execution component"
            )
        if has_operator_graph and not has_indexed_model and any(
            not candidate.operator_node_ids for candidate in self.candidate_models
        ):
            raise ValueError(
                "operator_graph candidates require operator node references"
            )
        if not has_operator_graph and any(
            candidate.operator_node_ids for candidate in self.candidate_models
        ):
            raise ValueError(
                "candidate operator_node_ids require operator_graph"
            )
        return self


SubproblemIRDraft = Annotated[
    SolvableModelIRDraft | BlockedModelIR,
    Field(discriminator="execution_status"),
]


class ModelIRGenerationDraft(IRModel):
    schema_version: Literal["1.0"] = "1.0"
    model_id: str = Field(min_length=1, max_length=120)
    subproblems: list[SubproblemIRDraft] = Field(min_length=1, max_length=1)

    def promote(
        self,
        indexed_linear_model: IndexedLinearModelSpec | None = None,
    ) -> ModelIR:
        source = (
            self.normalize_indexed_execution()
            if indexed_linear_model is not None
            else self
        )
        promoted: list[SubproblemIR] = []
        for subproblem in source.subproblems:
            if isinstance(subproblem, BlockedModelIR):
                promoted.append(subproblem)
                continue
            component_kinds = set(subproblem.execution_components)
            if (
                "indexed_linear_model" in component_kinds
                and indexed_linear_model is None
            ):
                raise ValueError(
                    "indexed_linear_model execution component requires an indexed model"
                )
            execution_components: list[ExecutionComponent] = []
            if "operator_graph" in component_kinds:
                execution_components.append(OperatorGraphExecutionComponent(
                    kind="operator_graph",
                    component_id="operator_graph",
                    variables=subproblem.variables,
                    operators=subproblem.operators,
                ))
            if indexed_linear_model is not None:
                execution_components.append(IndexedLinearExecutionComponent(
                    kind="indexed_linear",
                    component_id="indexed_linear_model",
                    model=indexed_linear_model,
                    solver_node_id="solve_indexed_model",
                    outputs=["solution"],
                ))
            payload = subproblem.model_dump(
                mode="python",
                exclude={
                    "execution_components",
                    "variables",
                    "operators",
                },
            )
            promoted.append(SolvableModelIR(
                **payload,
                execution_components=execution_components,
                fact_bindings=_build_fact_bindings(execution_components),
                equations=[],
                constraints=[],
            ))
        return ModelIR(model_id=source.model_id, subproblems=promoted)

    def normalize_indexed_execution(self) -> ModelIRGenerationDraft:
        subproblem = self.subproblems[0]
        if isinstance(subproblem, BlockedModelIR):
            return self
        if "indexed_linear_model" not in subproblem.execution_components:
            raise ValueError(
                "indexed execution requires the indexed_linear_model component"
            )
        node_id = "solve_indexed_model"
        normalized = subproblem.model_copy(update={
            "candidate_models": [
                item.model_copy(update={
                    "operator_node_ids": [*item.operator_node_ids, node_id],
                })
                for item in subproblem.candidate_models
            ],
            "final_answers": [
                item.model_copy(update={"value_ref": "solution"})
                for item in subproblem.final_answers
            ],
        })
        return self.model_copy(update={"subproblems": [normalized]})


class StaticIndexedParameterDraft(IRModel):
    source_kind: Literal["static"] = "static"
    id: str = Field(min_length=1, max_length=100)
    index_sets: list[str] = Field(default_factory=list, max_length=4)
    source_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    dense_values: list[float] = Field(min_length=1, max_length=10000)
    dense_source_ref: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def require_unique_source_fact_ids(self) -> StaticIndexedParameterDraft:
        _require_unique_source_fact_ids(self.source_fact_ids)
        return self

    def promote(self) -> IndexedParameterSpec:
        return IndexedParameterSpec(
            id=self.id,
            index_sets=self.index_sets,
            source_fact_ids=self.source_fact_ids,
            dense_values=self.dense_values,
            dense_source_ref=self.dense_source_ref,
        )


class RuntimeIndexedParameterDraft(IRModel):
    source_kind: Literal["runtime_artifact"] = "runtime_artifact"
    id: str = Field(min_length=1, max_length=100)
    index_sets: list[str] = Field(default_factory=list, max_length=4)
    source_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    runtime_source: RuntimeIndexedParameterSource

    @model_validator(mode="after")
    def require_unique_source_fact_ids(self) -> RuntimeIndexedParameterDraft:
        _require_unique_source_fact_ids(self.source_fact_ids)
        return self

    def promote(self) -> IndexedParameterSpec:
        return IndexedParameterSpec(
            id=self.id,
            index_sets=self.index_sets,
            source_fact_ids=self.source_fact_ids,
            runtime_source=self.runtime_source,
        )


IndexedParameterDraft = Annotated[
    StaticIndexedParameterDraft | RuntimeIndexedParameterDraft,
    Field(discriminator="source_kind"),
]


class BinderIndexSelectorDraft(IRModel):
    binder: str = Field(min_length=1, max_length=100)
    value: Literal[None] = None
    offset: int = Field(default=0, ge=-10000, le=10000)

    def promote(self) -> IndexSelector:
        return IndexSelector(
            binder=self.binder,
            value=None,
            offset=self.offset,
        )


class LiteralIndexSelectorDraft(IRModel):
    binder: Literal[None] = None
    value: IndexValue
    offset: int = Field(default=0, ge=-10000, le=10000)

    def promote(self) -> IndexSelector:
        return IndexSelector(
            binder=None,
            value=self.value,
            offset=self.offset,
        )


IndexSelectorDraft = BinderIndexSelectorDraft | LiteralIndexSelectorDraft


class ConstantLinearValueDraft(IRModel):
    kind: Literal["constant"] = "constant"
    value: float

    def promote(self) -> ConstantLinearValue:
        return ConstantLinearValue(value=self.value)


class ParameterLinearValueDraft(IRModel):
    kind: Literal["parameter"] = "parameter"
    parameter_id: str = Field(min_length=1, max_length=100)
    selectors: list[IndexSelectorDraft] = Field(default_factory=list, max_length=4)
    multiplier: float = 1.0

    def promote(self) -> ParameterLinearValue:
        return ParameterLinearValue(
            parameter_id=self.parameter_id,
            selectors=[item.promote() for item in self.selectors],
            multiplier=self.multiplier,
        )


LinearValueDraft = Annotated[
    ConstantLinearValueDraft | ParameterLinearValueDraft,
    Field(discriminator="kind"),
]


class IndexedVariableReferenceDraft(IRModel):
    variable_id: str = Field(min_length=1, max_length=100)
    selectors: list[IndexSelectorDraft] = Field(default_factory=list, max_length=4)

    def promote(self) -> IndexedVariableReference:
        return IndexedVariableReference(
            variable_id=self.variable_id,
            selectors=[item.promote() for item in self.selectors],
        )


class IndexedLinearTermDraft(IRModel):
    for_each: dict[str, str] | None = Field(default=None, max_length=4)
    coefficient: LinearValueDraft
    variable: IndexedVariableReferenceDraft

    def promote(self) -> IndexedLinearTerm:
        return IndexedLinearTerm(
            for_each=self.for_each or {},
            coefficient=self.coefficient.promote(),
            variable=self.variable.promote(),
        )


class IndexedLinearObjectiveDraft(IRModel):
    sense: Literal["minimize", "maximize"]
    terms: list[IndexedLinearTermDraft] = Field(min_length=1, max_length=50)

    def promote(self) -> IndexedLinearObjective:
        return IndexedLinearObjective(
            sense=self.sense,
            terms=[item.promote() for item in self.terms],
        )


class IndexedLinearConstraintDraft(IRModel):
    id: str = Field(min_length=1, max_length=100)
    source_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    fulfills_requirement_ids: list[str] = Field(default_factory=list, max_length=20)
    for_each: dict[str, str] | None = Field(default=None, max_length=4)
    terms: list[IndexedLinearTermDraft] = Field(min_length=1, max_length=1000)
    relation: Literal["equal", "less_equal", "greater_equal"]
    rhs: LinearValueDraft

    @model_validator(mode="after")
    def require_unique_source_fact_ids(self) -> IndexedLinearConstraintDraft:
        _require_unique_source_fact_ids(self.source_fact_ids)
        return self

    def promote(self) -> IndexedLinearConstraint:
        return IndexedLinearConstraint(
            id=self.id,
            source_fact_ids=self.source_fact_ids,
            fulfills_requirement_ids=self.fulfills_requirement_ids,
            for_each=self.for_each or {},
            terms=[item.promote() for item in self.terms],
            relation=self.relation,
            rhs=self.rhs.promote(),
        )


class IndexedLinearCoreDraft(IRModel):
    index_sets: list[IndexSetSpec] = Field(min_length=1, max_length=20)
    parameters: list[IndexedParameterDraft] = Field(default_factory=list, max_length=100)
    variables: list[IndexedVariableSpec] = Field(min_length=1, max_length=100)
    objective: IndexedLinearObjectiveDraft

    def promote_core(self) -> IndexedLinearCoreSpec:
        return IndexedLinearCoreSpec(
            index_sets=self.index_sets,
            parameters=[item.promote() for item in self.parameters],
            variables=self.variables,
            objective=self.objective.promote(),
        )

    def promote(
        self,
        constraints: list[IndexedLinearConstraint],
        additional_index_sets: list[IndexSetSpec] | None = None,
    ) -> IndexedLinearModelSpec:
        core = self.promote_core()
        return IndexedLinearModelSpec(
            index_sets=[*core.index_sets, *(additional_index_sets or [])],
            parameters=core.parameters,
            variables=core.variables,
            objective=core.objective,
            constraints=constraints,
        )


class ConstraintIndexSetSymbol(IRModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    id: str = Field(min_length=1, max_length=100)
    values: tuple[IndexValue, ...] = Field(min_length=1, max_length=1000)


class ConstraintParameterSymbol(IRModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    id: str = Field(min_length=1, max_length=100)
    index_sets: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    source_ref: str = Field(min_length=1, max_length=300)


class ConstraintVariableSymbol(IRModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    id: str = Field(min_length=1, max_length=100)
    index_sets: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    domain: Literal["continuous", "integer", "binary"]


def _runtime_parameter_source_ref(
    source: RuntimeIndexedParameterSource,
) -> str:
    pointer = "/".join(
        item.replace("~", "~0").replace("/", "~1")
        for item in source.field_path
    )
    return f"artifact:{source.artifact_ref}#/{pointer}"


class ConstraintGenerationContext(IRModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    index_sets: tuple[ConstraintIndexSetSymbol, ...] = Field(min_length=1, max_length=20)
    parameters: tuple[ConstraintParameterSymbol, ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    variables: tuple[ConstraintVariableSymbol, ...] = Field(min_length=1, max_length=100)

    @classmethod
    def from_core(cls, core: IndexedLinearCoreDraft) -> ConstraintGenerationContext:
        return cls(
            index_sets=tuple(
                ConstraintIndexSetSymbol(id=item.id, values=tuple(item.values))
                for item in core.index_sets
            ),
            parameters=tuple(
                ConstraintParameterSymbol(
                    id=item.id,
                    index_sets=tuple(item.index_sets),
                    source_ref=(
                        item.dense_source_ref
                        if isinstance(item, StaticIndexedParameterDraft)
                        else _runtime_parameter_source_ref(item.runtime_source)
                    ),
                )
                for item in core.parameters
            ),
            variables=tuple(
                ConstraintVariableSymbol(
                    id=item.id,
                    index_sets=tuple(item.index_sets),
                    domain=item.domain,
                )
                for item in core.variables
            ),
        )


class IndexedLinearConstraintsDraft(IRModel):
    additional_index_sets: list[IndexSetSpec] = Field(default_factory=list, max_length=20)
    constraints: list[IndexedLinearConstraintDraft] = Field(min_length=1, max_length=50)

    def promote_constraints(self) -> list[IndexedLinearConstraint]:
        return [item.promote() for item in self.constraints]
