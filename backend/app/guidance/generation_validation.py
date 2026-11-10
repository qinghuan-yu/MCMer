"""Guidance 职责边界：generation_validation。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.guidance.contracts import ProblemSpec
from app.guidance.operators import OperatorRegistry
from app.guidance.evidence_consistency import validate_model_evidence_consistency
from app.guidance.fact_coverage import FactCoverage, FactScope
from app.guidance.indexed_linear import (
    validate_indexed_linear_data,
    validate_indexed_linear_model_structure,
)
from app.guidance.model_ir import (
    IndexedLinearDataSpec,
    IndexedLinearModelSpec,
    IndexedLinearExecutionComponent,
    ModelIR,
    OperatorGraphExecutionComponent,
    SolvableModelIR,
    declared_artifact_ids,
)
from app.guidance.model_ir_drafts import (
    FormulationContext,
    IndexedLinearFormulationDraft,
    IndexedLinearCoreDraft,
    ModelIRGenerationDraft,
    SolvableModelIRDraft,
)
from app.guidance.model_ir_semantics import (
    ModelIRSemanticValidator,
    validate_model_fact_coverage,
)
from app.guidance.model_validators import build_default_model_validator_registry


def _validate_indexed_core_stage(
    core: IndexedLinearCoreDraft,
    coverage: FactCoverage,
    *,
    evidence_profile: Mapping[str, Any] | None = None,
) -> IndexedLinearDataSpec:
    coverage.with_stage(
        "indexed_core",
        ((parameter.id, parameter.source_fact_ids) for parameter in core.parameters),
    )
    materialized = _validate_indexed_core_draft(
        core,
        locked_facts=coverage.scope.payload(),
        evidence_profile=evidence_profile,
    )
    coverage.with_stage(
        "indexed_core",
        ((parameter.id, parameter.source_fact_ids) for parameter in materialized.parameters),
    )
    return materialized

def _validate_indexed_constraint_stage(
    *,
    core: IndexedLinearDataSpec,
    constraints: IndexedLinearFormulationDraft,
    context: FormulationContext,
    coverage: FactCoverage,
) -> FactCoverage:
    updated = coverage.with_stage(
        "indexed_constraints",
        ((constraint.id, constraint.source_fact_ids) for constraint in constraints.constraints),
    )
    _validate_indexed_parts(core, constraints, context)
    updated.require_complete()
    return updated

def _validate_generation_draft_slice(
    draft: ModelIRGenerationDraft,
    *,
    expected_id: str,
    registry: OperatorRegistry,
    problem_spec: ProblemSpec,
) -> FactCoverage:
    identifiers = [item.subproblem_id for item in draft.subproblems]
    if identifiers != [expected_id]:
        raise ValueError(
            f"subproblem slice mismatch: expected={[expected_id]!r}; modeled={identifiers!r}"
        )
    subproblem = draft.subproblems[0]
    coverage = FactCoverage(FactScope.from_problem_spec(problem_spec, expected_id))
    if not isinstance(subproblem, SolvableModelIRDraft):
        return coverage
    coverage = coverage.with_stage(
        "architecture",
        ((operator.node_id, operator.source_fact_ids) for operator in subproblem.operators),
    )
    if "indexed_linear_model" not in subproblem.execution_components:
        coverage.require_complete()
    components = set(subproblem.execution_components)
    if "indexed_linear_model" in components:
        if registry.get("optimization.milp") is None:
            raise ValueError("optimization.milp is not available")
    if any(
        item.operator_id == "optimization.milp"
        for item in subproblem.operators
    ):
        raise ValueError(
            "optimization.milp is system-managed for indexed_linear_model"
        )
    unsupported = sorted({
        item.operator_id for item in subproblem.operators
        if registry.get(item.operator_id) is None
    })
    if unsupported:
        raise ValueError(f"operator_id is not available: {unsupported!r}")
    declared_outputs = declared_artifact_ids(
        variables=subproblem.variables, final_answers=subproblem.final_answers,
    )
    for invocation in subproblem.operators:
        definition = registry.get(invocation.operator_id)
        definition.validate_call(inputs=invocation.inputs, parameters=invocation.parameters, outputs=invocation.outputs)
        undeclared = set(invocation.outputs) - declared_outputs
        if undeclared:
            raise ValueError(
                f"operator output is not declared by Model IR: {sorted(undeclared)!r}; "
                "outputs must use declared artifact aliases, not registered port names"
            )
    operator_ids = {item.operator_id for item in subproblem.operators}
    if "indexed_linear_model" in components:
        operator_ids.add("optimization.milp")
    issues = build_default_model_validator_registry().method_consistency_issues(
        families=subproblem.validation.model_families, operator_ids=operator_ids,
    )
    if issues:
        raise ValueError("validation_family_method_mismatch: " + "; ".join(issues))
    return coverage

def _validate_indexed_parts(
    core: IndexedLinearDataSpec,
    constraints: IndexedLinearFormulationDraft,
    context: FormulationContext,
) -> None:
    _validate_indexed_formulation_compactness(core, constraints)
    indexed_model = constraints.promote(core)
    issues = validate_indexed_linear_model_structure(indexed_model)
    if issues:
        details = "; ".join(f"{item.code}: {item.message}" for item in issues)
        parameter_ids = [item.id for item in context.parameters]
        variable_ids = [item.id for item in constraints.variables]
        index_set_ids = [item.id for item in context.index_sets]
        raise ValueError(
            f"indexed linear expansion failed: {details}; "
            f"allowed parameter ids={parameter_ids!r}; "
            f"allowed variable ids={variable_ids!r}; "
            f"allowed index set ids={index_set_ids!r}"
        )

def _validate_indexed_core_draft(
    core: IndexedLinearCoreDraft,
    *,
    locked_facts: list[dict[str, Any]] | None = None,
    evidence_profile: Mapping[str, Any] | None = None,
) -> IndexedLinearDataSpec:
    materialized = core.promote_core(locked_facts=locked_facts, evidence_profile=evidence_profile)
    issues = validate_indexed_linear_data(
        materialized,
        locked_facts=locked_facts,
        evidence_profile=evidence_profile,
    )
    if issues:
        details = "; ".join(f"{item.code}: {item.message}" for item in issues)
        raise ValueError(f"indexed linear core validation failed: {details}")
    return materialized

def _validate_indexed_formulation_compactness(
    core: IndexedLinearDataSpec,
    constraints: IndexedLinearFormulationDraft,
) -> None:
    _validate_compact_literal_references(
        constraints.promote(core),
        [constraints.objective.terms, *[
            constraint.terms
            for constraint in constraints.constraints
            if not constraint.for_each
        ]],
    )

def _validate_compact_literal_references(
    core: IndexedLinearModelSpec,
    term_groups: list[list[Any]],
) -> None:
    index_sets = {item.id: item for item in core.index_sets}
    variables = {item.id: item for item in core.variables}
    literal_values: dict[tuple[str, str], set[Any]] = {}

    for terms in term_groups:
        for term in terms:
            if term.for_each:
                continue
            variable = variables.get(term.variable.variable_id)
            if variable is None:
                continue
            for index_set_id, selector in zip(
                variable.index_sets,
                term.variable.selectors,
            ):
                index_set = index_sets.get(index_set_id)
                if (
                    index_set is not None
                    and len(index_set.values) > 8
                    and selector.value is not None
                ):
                    literal_values.setdefault(
                        (variable.id, index_set_id),
                        set(),
                    ).add(selector.value)

    expanded = {
        key: values
        for key, values in literal_values.items()
        if len(values) > 4
    }
    if expanded:
        details = ", ".join(
            f"{variable_id}.{index_set_id}={len(values)} literal values"
            for (variable_id, index_set_id), values in sorted(expanded.items())
        )
        raise ValueError(
            "large indexed families must use for_each and binder; " + details
        )

def _validate_model_ir_slice(
    model_ir: ModelIR,
    *,
    expected_id: str,
    problem_spec: ProblemSpec,
    evidence_profile: dict,
    registry: OperatorRegistry,
) -> None:
    identifiers = [item.subproblem_id for item in model_ir.subproblems]
    if identifiers != [expected_id]:
        raise ValueError(
            f"subproblem slice mismatch: expected={[expected_id]!r}; modeled={identifiers!r}"
        )
    validate_model_evidence_consistency(problem_spec, model_ir, evidence_profile)
    validate_model_fact_coverage(problem_spec, model_ir)
    supported = {item.operator_id for item in registry.definitions()}
    unsupported = sorted({
        operator_id
        for subproblem in model_ir.subproblems
        if isinstance(subproblem, SolvableModelIR)
        for component in subproblem.execution_components
        for operator_id in (
            [operator.operator_id for operator in component.operators]
            if isinstance(component, OperatorGraphExecutionComponent)
            else ["optimization.milp"]
            if isinstance(component, IndexedLinearExecutionComponent)
            else []
        )
        if operator_id not in supported
    })
    if unsupported:
        raise ValueError(f"operator_id is not available: {unsupported!r}")
    _validate_model_ir_semantics(model_ir)

def _validate_model_ir_contract(
    problem_spec: ProblemSpec,
    model_ir: ModelIR,
    evidence_profile: dict,
) -> None:
    expected = [item.id for item in problem_spec.subproblems]
    modeled = [item.subproblem_id for item in model_ir.subproblems]
    if modeled != expected:
        raise ValueError(
            f"subproblem coverage mismatch: expected={expected!r}; modeled={modeled!r}"
        )
    validate_model_evidence_consistency(problem_spec, model_ir, evidence_profile)
    validate_model_fact_coverage(problem_spec, model_ir)
    _validate_model_ir_semantics(model_ir)

def _validate_model_ir_semantics(model_ir: ModelIR) -> None:
    diagnostics = ModelIRSemanticValidator().validate(model_ir)
    if diagnostics:
        details = "; ".join(
            f"{item.code} at {item.path}: {item.message}" for item in diagnostics
        )
        raise ValueError(f"Model IR semantic validation failed: {details}")
