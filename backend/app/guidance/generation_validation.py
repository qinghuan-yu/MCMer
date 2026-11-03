"""Guidance 职责边界：generation_validation。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.guidance.contracts import ProblemSpec
from app.guidance.evidence_consistency import validate_model_evidence_consistency
from app.guidance.indexed_linear import (
    validate_indexed_linear_core,
    validate_indexed_linear_model_structure,
)
from app.guidance.model_ir import (
    IndexedLinearExecutionComponent,
    ModelIR,
    OperatorGraphExecutionComponent,
    SolvableModelIR,
)
from app.guidance.model_ir_drafts import (
    ConstraintGenerationContext,
    IndexedLinearConstraintsDraft,
    IndexedLinearCoreDraft,
    ModelIRGenerationDraft,
    SolvableModelIRDraft,
)
from app.guidance.model_ir_semantics import (
    ModelIRSemanticValidator,
    validate_model_fact_coverage,
)
from app.guidance.modeling_context import _remaining_locked_facts


def _validate_indexed_core_stage(
    core: IndexedLinearCoreDraft,
    locked_facts: list[dict[str, Any]],
    *,
    evidence_profile: Mapping[str, Any] | None = None,
) -> None:
    _validate_indexed_core_draft(
        core,
        locked_facts=locked_facts,
        evidence_profile=evidence_profile,
    )
    _remaining_locked_facts(
        locked_facts,
        [
            fact_id
            for parameter in core.parameters
            for fact_id in parameter.source_fact_ids
        ],
    )

def _validate_indexed_constraint_stage(
    *,
    core: IndexedLinearCoreDraft,
    constraints: IndexedLinearConstraintsDraft,
    context: ConstraintGenerationContext,
    locked_facts: list[dict[str, Any]],
) -> None:
    _validate_indexed_parts(core, constraints, context)
    remaining = _remaining_locked_facts(
        locked_facts,
        [
            fact_id
            for constraint in constraints.constraints
            for fact_id in constraint.source_fact_ids
        ],
    )
    if remaining:
        raise ValueError(
            "fact coverage mismatch in indexed constraints: "
            f"uncovered={[fact['fact_id'] for fact in remaining]!r}"
        )

def _validate_generation_draft_slice(
    draft: ModelIRGenerationDraft,
    *,
    expected_id: str,
    available_operator_ids: list[str],
    problem_spec: ProblemSpec,
) -> None:
    identifiers = [item.subproblem_id for item in draft.subproblems]
    if identifiers != [expected_id]:
        raise ValueError(
            f"subproblem slice mismatch: expected={[expected_id]!r}; modeled={identifiers!r}"
        )
    subproblem = draft.subproblems[0]
    if not isinstance(subproblem, SolvableModelIRDraft):
        return
    facts_by_id = {
        fact.fact_id: fact.model_dump(mode="json")
        for fact in problem_spec.locked_facts
    }
    target_spec = next(
        (item for item in problem_spec.subproblems if item.id == expected_id),
        None,
    )
    expected_facts = [
        facts_by_id[fact_id]
        for fact_id in (
            target_spec.required_fact_ids if target_spec is not None else []
        )
        if fact_id in facts_by_id
    ]
    remaining_facts = _remaining_locked_facts(
        expected_facts,
        [
            fact_id
            for operator in subproblem.operators
            for fact_id in operator.source_fact_ids
        ],
    )
    if (
        "indexed_linear_model" not in subproblem.execution_components
        and remaining_facts
    ):
        raise ValueError(
            f"fact coverage mismatch for {expected_id!r}: "
            f"uncovered={[fact['fact_id'] for fact in remaining_facts]!r}"
        )
    components = set(subproblem.execution_components)
    if "indexed_linear_model" in components:
        if "optimization.milp" not in available_operator_ids:
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
        if item.operator_id not in set(available_operator_ids)
    })
    if unsupported:
        raise ValueError(f"operator_id is not available: {unsupported!r}")

def _validate_indexed_parts(
    core: IndexedLinearCoreDraft,
    constraints: IndexedLinearConstraintsDraft,
    context: ConstraintGenerationContext,
) -> None:
    _validate_indexed_constraints_compactness(core, constraints)
    indexed_model = core.promote(
        constraints.promote_constraints(),
        constraints.additional_index_sets,
    )
    issues = validate_indexed_linear_model_structure(indexed_model)
    if issues:
        details = "; ".join(f"{item.code}: {item.message}" for item in issues)
        parameter_ids = [item.id for item in context.parameters]
        variable_ids = [item.id for item in context.variables]
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
) -> None:
    _validate_indexed_core_compactness(core)
    issues = validate_indexed_linear_core(
        core.promote_core(),
        locked_facts=locked_facts,
        evidence_profile=evidence_profile,
    )
    if issues:
        details = "; ".join(f"{item.code}: {item.message}" for item in issues)
        raise ValueError(f"indexed linear core validation failed: {details}")

def _validate_indexed_core_compactness(core: IndexedLinearCoreDraft) -> None:
    _validate_compact_literal_references(core, [core.objective.terms])

def _validate_indexed_constraints_compactness(
    core: IndexedLinearCoreDraft,
    constraints: IndexedLinearConstraintsDraft,
) -> None:
    _validate_compact_literal_references(
        core,
        [
            constraint.terms
            for constraint in constraints.constraints
            if not constraint.for_each
        ],
    )

def _validate_compact_literal_references(
    core: IndexedLinearCoreDraft,
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
    available_operator_ids: list[str],
) -> None:
    identifiers = [item.subproblem_id for item in model_ir.subproblems]
    if identifiers != [expected_id]:
        raise ValueError(
            f"subproblem slice mismatch: expected={[expected_id]!r}; modeled={identifiers!r}"
        )
    validate_model_evidence_consistency(problem_spec, model_ir, evidence_profile)
    validate_model_fact_coverage(problem_spec, model_ir)
    if available_operator_ids:
        supported = set(available_operator_ids)
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
