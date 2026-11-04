"""结构需求义务检查：ProblemSpec 的封闭类型需求必须由索引约束按结构履行。

引用事实只证明“提到过”；这里检查履行约束的结构本身：守恒要有相邻状态的
反号等式与边界，滞后要有声明步长的跨期联动与边界，容量/需求要有方向正确且
引用证据参数的不等式。范围限于 indexed_linear 执行组件。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.guidance.contracts import ModelingRequirementSpec, ProblemSpec
from app.guidance.model_ir import (
    ConstantLinearValue,
    IndexedLinearConstraint,
    IndexedLinearCoreSpec,
    IndexedLinearExecutionComponent,
    IndexedLinearTerm,
    ModelIR,
    ParameterLinearValue,
    SolvableModelIR,
)


def _constant(term: IndexedLinearTerm) -> float | None:
    coefficient = term.coefficient
    if isinstance(coefficient, ConstantLinearValue) and coefficient.value != 0.0:
        return coefficient.value
    return None


def _binder_offset(term: IndexedLinearTerm, position: int, binder: str) -> int | None:
    selectors = term.variable.selectors
    if position >= len(selectors) or selectors[position].binder != binder:
        return None
    return selectors[position].offset


def _same_except(left: IndexedLinearTerm, right: IndexedLinearTerm, position: int) -> bool:
    left_selectors = left.variable.selectors
    right_selectors = right.variable.selectors
    return len(left_selectors) == len(right_selectors) and all(
        a == b for index, (a, b) in enumerate(zip(left_selectors, right_selectors))
        if index != position
    )


def _has_literal_boundary(
    constraints: Sequence[IndexedLinearConstraint], variable_id: str, position: int,
) -> bool:
    return any(
        term.variable.variable_id == variable_id
        and position < len(term.variable.selectors)
        and term.variable.selectors[position].value is not None
        for constraint in constraints
        for term in constraint.terms
    )


def _conservation_problem(constraints: Sequence[IndexedLinearConstraint]) -> str | None:
    for constraint in constraints:
        if constraint.relation != "equal":
            continue
        for binder in constraint.for_each:
            for current in constraint.terms:
                for position in range(len(current.variable.selectors)):
                    if _binder_offset(current, position, binder) != 0:
                        continue
                    for previous in constraint.terms:
                        offset = _binder_offset(previous, position, binder)
                        a, b = _constant(current), _constant(previous)
                        if (
                            previous.variable.variable_id == current.variable.variable_id
                            and offset is not None and offset < 0
                            and a is not None and b is not None and a * b < 0
                            and _same_except(current, previous, position)
                        ):
                            if _has_literal_boundary(
                                constraints, current.variable.variable_id, position,
                            ):
                                return None
                            return (
                                f"state {current.variable.variable_id!r} has a transition "
                                "but no fulfilling boundary constraint with a literal index"
                            )
    return (
        "no fulfilling equality links a state at t and an earlier period with "
        "opposite-sign constant coefficients"
    )


def _lag_problem(constraints: Sequence[IndexedLinearConstraint], lag: int) -> str | None:
    for constraint in constraints:
        for binder in constraint.for_each:
            for target in constraint.terms:
                for position in range(len(target.variable.selectors)):
                    if _binder_offset(target, position, binder) != 0:
                        continue
                    for source in constraint.terms:
                        if source.variable.variable_id == target.variable.variable_id:
                            continue
                        if any(
                            _binder_offset(source, index, binder) == -lag
                            for index in range(len(source.variable.selectors))
                        ):
                            if _has_literal_boundary(
                                constraints, target.variable.variable_id, position,
                            ):
                                return None
                            return (
                                f"lagged target {target.variable.variable_id!r} has no "
                                f"fulfilling boundary constraint for the first {lag} period(s)"
                            )
    return f"no fulfilling constraint links two variables with offset -{lag}"


def _referenced_parameters(constraint: IndexedLinearConstraint) -> set[str]:
    values = [constraint.rhs, *(term.coefficient for term in constraint.terms)]
    return {
        value.parameter_id
        for value in values
        if isinstance(value, ParameterLinearValue) and value.multiplier != 0.0
    }


def _bound_problem(
    constraints: Sequence[IndexedLinearConstraint],
    requirement: ModelingRequirementSpec,
    model: IndexedLinearCoreSpec,
    relations: set[str],
) -> str | None:
    if requirement.source_fact_ids:
        allowed = {
            parameter.id
            for parameter in model.parameters
            if set(parameter.source_fact_ids) & set(requirement.source_fact_ids)
        }
    else:
        allowed = {parameter.id for parameter in model.parameters}
    if any(
        constraint.relation in relations and _referenced_parameters(constraint) & allowed
        for constraint in constraints
    ):
        return None
    facts = (
        f" bound to facts {requirement.source_fact_ids!r}"
        if requirement.source_fact_ids else ""
    )
    return (
        f"no fulfilling {'/'.join(sorted(relations))} constraint references a "
        f"parameter{facts}"
    )


def indexed_requirement_errors(
    requirements: Sequence[ModelingRequirementSpec],
    model: IndexedLinearCoreSpec,
    constraints: Sequence[IndexedLinearConstraint],
) -> list[str]:
    """返回未履行或结构不符的需求，以及引用未知需求的约束。"""
    known = {requirement.requirement_id for requirement in requirements}
    errors = [
        f"constraint {constraint.id!r} fulfills unknown or out-of-scope requirement "
        f"{requirement_id!r}"
        for constraint in constraints
        for requirement_id in constraint.fulfills_requirement_ids
        if requirement_id not in known
    ]
    for requirement in requirements:
        fulfilling = [
            constraint for constraint in constraints
            if requirement.requirement_id in constraint.fulfills_requirement_ids
        ]
        if not fulfilling:
            problem: str | None = "no constraint declares fulfills_requirement_ids for it"
        elif requirement.kind == "state_conservation":
            problem = _conservation_problem(fulfilling)
        elif requirement.kind == "lagged_link":
            problem = _lag_problem(fulfilling, int(requirement.lag or 0))
        elif requirement.kind == "upper_capacity":
            problem = _bound_problem(fulfilling, requirement, model, {"less_equal"})
        else:
            problem = _bound_problem(
                fulfilling, requirement, model, {"equal", "greater_equal"},
            )
        if problem is not None:
            errors.append(
                f"requirement {requirement.requirement_id!r} ({requirement.kind}): {problem}"
            )
    return errors


def validate_requirement_coverage(problem_spec: ProblemSpec, model_ir: ModelIR) -> None:
    for subproblem in model_ir.subproblems:
        if not isinstance(subproblem, SolvableModelIR):
            continue
        components = [
            component for component in subproblem.execution_components
            if isinstance(component, IndexedLinearExecutionComponent)
        ]
        if not components:
            continue
        requirements = [
            requirement for requirement in problem_spec.modeling_requirements
            if subproblem.subproblem_id in requirement.subproblem_ids
        ]
        errors = [
            error
            for component in components
            for error in indexed_requirement_errors(
                requirements, component.model, component.model.constraints,
            )
        ]
        if errors:
            raise ValueError(
                f"requirement coverage mismatch for {subproblem.subproblem_id!r}: "
                + "; ".join(errors)
            )
