"""Expand finite indexed linear semantics into an auditable matrix contract."""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from app.guidance.parameter_evidence import validate_static_parameter_evidence

from app.guidance.model_ir import (
    ConstantLinearValue,
    IndexSelector,
    IndexedLinearCoreSpec,
    IndexedLinearModelSpec,
    IndexedLinearTerm,
    IndexedParameterSpec,
    IndexedParameterValue,
    IndexedVariableReference,
    ParameterLinearValue,
)


_IndexedCore = TypeVar("_IndexedCore", bound=IndexedLinearCoreSpec)


@dataclass(frozen=True)
class IndexedLinearIssue:
    code: str
    message: str


@dataclass
class _ExpandedIndexedCore:
    index_sets: dict[str, list]
    parameters: dict[str, IndexedParameterSpec]
    variables: dict[str, Any]
    parameter_values: dict[tuple[str, tuple], tuple[float, str]]
    variable_keys: list[tuple[str, tuple]]
    variable_labels: list[str]
    variable_positions: dict[tuple[str, tuple], int]
    integrality: list[int]
    lower_bounds: list[float | None]
    upper_bounds: list[float | None]
    objective: list[float]
    provenance: list[dict[str, Any]]


def validate_indexed_linear_core(
    core: IndexedLinearCoreSpec,
    *,
    locked_facts: Sequence[Any] | None = None,
    evidence_profile: Mapping[str, Any] | Any | None = None,
) -> list[IndexedLinearIssue]:
    issues = _evidence_issues(core, locked_facts, evidence_profile)
    _expand_indexed_linear_core(
        _with_runtime_validation_values(core),
        issues,
    )
    return _unique_issues(issues)


def validate_indexed_linear_model_structure(
    model: IndexedLinearModelSpec,
    *,
    locked_facts: Sequence[Any] | None = None,
    evidence_profile: Mapping[str, Any] | Any | None = None,
) -> list[IndexedLinearIssue]:
    _, issues = expand_indexed_linear_model(
        _with_runtime_validation_values(model),
    )
    return _unique_issues([*issues, *_evidence_issues(model, locked_facts, evidence_profile)])


def expand_indexed_linear_model(
    model: IndexedLinearModelSpec,
    *,
    locked_facts: Sequence[Any] | None = None,
    evidence_profile: Mapping[str, Any] | Any | None = None,
) -> tuple[dict[str, Any] | None, list[IndexedLinearIssue]]:
    issues = _evidence_issues(model, locked_facts, evidence_profile)
    core = _expand_indexed_linear_core(model, issues)

    matrix: list[list[float]] = []
    constraint_lower: list[float | None] = []
    constraint_upper: list[float | None] = []
    constraint_labels: list[str] = []
    for constraint in model.constraints:
        for binding in _bindings(
            constraint.for_each,
            core.index_sets,
            issues,
            f"constraint {constraint.id!r}",
        ):
            row = [0.0] * len(core.variable_keys)
            for term_index, term in enumerate(constraint.terms):
                term_bindings = _extend_bindings(
                    binding,
                    term.for_each,
                    core.index_sets,
                    issues,
                    f"constraint {constraint.id!r} term {term_index}",
                )
                for term_binding in term_bindings:
                    resolved = _resolve_term(
                        term,
                        binding=term_binding,
                        index_sets=core.index_sets,
                        variables=core.variables,
                        parameters=core.parameters,
                        parameter_values=core.parameter_values,
                        issues=issues,
                        context=f"constraint {constraint.id!r} term {term_index}",
                        provenance=core.provenance,
                    )
                    if resolved is not None:
                        key, coefficient = resolved
                        position = core.variable_positions.get(key)
                        if position is not None:
                            row[position] += coefficient
            rhs = _resolve_linear_value(
                constraint.rhs,
                binding=binding,
                index_sets=core.index_sets,
                parameters=core.parameters,
                parameter_values=core.parameter_values,
                issues=issues,
                context=f"constraint {constraint.id!r} rhs",
                provenance=core.provenance,
            )
            if rhs is None:
                rhs = 0.0
            matrix.append(row)
            constraint_labels.append(_binding_label(constraint.id, binding))
            constraint_lower.append(rhs if constraint.relation != "less_equal" else None)
            constraint_upper.append(rhs if constraint.relation != "greater_equal" else None)

    if issues:
        return None, _unique_issues(issues)
    return {
        "objective_sense": model.objective.sense,
        "variable_labels": core.variable_labels,
        "objective": core.objective,
        "integrality": core.integrality,
        "lower_bounds": core.lower_bounds,
        "upper_bounds": core.upper_bounds,
        "constraint_labels": constraint_labels,
        "matrix": matrix,
        "constraint_lower": constraint_lower,
        "constraint_upper": constraint_upper,
        "provenance": core.provenance,
    }, []


def _evidence_issues(core, locked_facts, evidence_profile) -> list[IndexedLinearIssue]:
    return [
        IndexedLinearIssue("indexed_parameter_evidence_mismatch", message)
        for message in validate_static_parameter_evidence(
            core, locked_facts=locked_facts, evidence_profile=evidence_profile,
        )
    ]


def _with_runtime_validation_values(
    model: _IndexedCore,
) -> _IndexedCore:
    index_sizes = {
        index_set.id: len(index_set.values)
        for index_set in model.index_sets
    }
    parameters = []
    for parameter in model.parameters:
        source = parameter.runtime_source
        if source is None:
            parameters.append(parameter)
            continue
        value_count = math.prod(
            index_sizes[index_set_id]
            for index_set_id in parameter.index_sets
        )
        pointer = "/".join(
            token.replace("~", "~0").replace("/", "~1")
            for token in source.field_path
        )
        parameters.append(parameter.model_copy(update={
            "dense_values": [0.0] * value_count,
            "dense_source_ref": f"artifact:{source.artifact_ref}#/{pointer}",
            "runtime_source": None,
        }))
    return model.model_copy(update={"parameters": parameters})


def _expand_indexed_linear_core(
    core: IndexedLinearCoreSpec,
    issues: list[IndexedLinearIssue],
) -> _ExpandedIndexedCore:
    index_sets = {item.id: list(item.values) for item in core.index_sets}
    parameters = {item.id: item for item in core.parameters}
    variables = {item.id: item for item in core.variables}
    parameter_values = _parameter_values(
        parameters,
        index_sets,
        issues,
    )

    variable_keys: list[tuple[str, tuple]] = []
    variable_labels: list[str] = []
    integrality: list[int] = []
    lower_bounds: list[float | None] = []
    upper_bounds: list[float | None] = []
    for variable in core.variables:
        domains = _domains(variable.index_sets, index_sets, issues, f"variable {variable.id!r}")
        for coordinates in itertools.product(*domains):
            variable_keys.append((variable.id, coordinates))
            variable_labels.append(_indexed_label(variable.id, coordinates))
            integrality.append(0 if variable.domain == "continuous" else 1)
            if variable.domain == "binary":
                lower_bounds.append(0.0)
                upper_bounds.append(1.0)
            else:
                lower_bounds.append(variable.lower_bound)
                upper_bounds.append(variable.upper_bound)
    variable_positions = {key: index for index, key in enumerate(variable_keys)}

    objective = [0.0] * len(variable_keys)
    provenance: list[dict[str, Any]] = []
    for term_index, term in enumerate(core.objective.terms):
        for binding in _bindings(
            term.for_each,
            index_sets,
            issues,
            f"objective term {term_index}",
        ):
            resolved = _resolve_term(
                term,
                binding=binding,
                index_sets=index_sets,
                variables=variables,
                parameters=parameters,
                parameter_values=parameter_values,
                issues=issues,
                context=f"objective term {term_index}",
                provenance=provenance,
            )
            if resolved is not None:
                key, coefficient = resolved
                position = variable_positions.get(key)
                if position is not None:
                    objective[position] += coefficient

    return _ExpandedIndexedCore(
        index_sets=index_sets,
        parameters=parameters,
        variables=variables,
        parameter_values=parameter_values,
        variable_keys=variable_keys,
        variable_labels=variable_labels,
        variable_positions=variable_positions,
        integrality=integrality,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
        objective=objective,
        provenance=provenance,
    )


def _unique_issues(issues: list[IndexedLinearIssue]) -> list[IndexedLinearIssue]:
    unique = dict.fromkeys((issue.code, issue.message) for issue in issues)
    return [IndexedLinearIssue(code=code, message=message) for code, message in unique]


def _parameter_values(
    parameters: dict[str, IndexedParameterSpec],
    index_sets: dict[str, list],
    issues: list[IndexedLinearIssue],
) -> dict[tuple[str, tuple], tuple[float, str]]:
    result: dict[tuple[str, tuple], tuple[float, str]] = {}
    for parameter in parameters.values():
        domains = _domains(
            parameter.index_sets,
            index_sets,
            issues,
            f"parameter {parameter.id!r}",
        )
        items = list(parameter.values)
        if parameter.dense_values:
            coordinates = list(itertools.product(*domains))
            if len(parameter.dense_values) != len(coordinates):
                issues.append(IndexedLinearIssue(
                    "indexed_dense_parameter_length_mismatch",
                    f"parameter {parameter.id!r} expects {len(coordinates)} dense values, got {len(parameter.dense_values)}",
                ))
                continue
            items = [
                IndexedParameterValue(
                    coordinates=list(coordinate),
                    value=value,
                    source_ref=parameter.dense_source_ref,
                )
                for coordinate, value in zip(coordinates, parameter.dense_values)
            ]
        for item in items:
            if len(item.coordinates) != len(parameter.index_sets):
                issues.append(IndexedLinearIssue(
                    "indexed_parameter_dimension_mismatch",
                    f"parameter {parameter.id!r} expects {len(parameter.index_sets)} coordinates, got {len(item.coordinates)}",
                ))
                continue
            valid = True
            for coordinate, index_set_id in zip(item.coordinates, parameter.index_sets):
                if index_set_id in index_sets and coordinate not in index_sets[index_set_id]:
                    issues.append(IndexedLinearIssue(
                        "indexed_value_not_in_domain",
                        f"parameter {parameter.id!r} coordinate {coordinate!r} is not in index set {index_set_id!r}",
                    ))
                    valid = False
            if not math.isfinite(item.value):
                issues.append(IndexedLinearIssue(
                    "indexed_nonfinite_value",
                    f"parameter {parameter.id!r} contains a non-finite value",
                ))
                valid = False
            key = (parameter.id, tuple(item.coordinates))
            if key in result:
                issues.append(IndexedLinearIssue(
                    "indexed_parameter_duplicate",
                    f"parameter {parameter.id!r} repeats coordinates {item.coordinates!r}",
                ))
                valid = False
            if valid:
                result[key] = (float(item.value), item.source_ref)
    return result


def _domains(
    identifiers: list[str],
    index_sets: dict[str, list],
    issues: list[IndexedLinearIssue],
    context: str,
) -> list[list]:
    domains: list[list] = []
    for identifier in identifiers:
        values = index_sets.get(identifier)
        if values is None:
            issues.append(IndexedLinearIssue(
                "indexed_unknown_index_set",
                f"{context} references unknown index set {identifier!r}",
            ))
            domains.append([])
        else:
            domains.append(values)
    return domains


def _bindings(
    for_each: dict[str, str],
    index_sets: dict[str, list],
    issues: list[IndexedLinearIssue],
    context: str,
) -> list[dict[str, object]]:
    binders = list(for_each)
    domains = _domains(list(for_each.values()), index_sets, issues, context)
    return [dict(zip(binders, values)) for values in itertools.product(*domains)]


def _extend_bindings(
    binding: dict[str, object],
    for_each: dict[str, str],
    index_sets: dict[str, list],
    issues: list[IndexedLinearIssue],
    context: str,
) -> list[dict[str, object]]:
    if not for_each:
        return [binding]
    overlap = set(binding) & set(for_each)
    if overlap:
        issues.append(IndexedLinearIssue(
            "indexed_duplicate_binder",
            f"{context} redefines binders {sorted(overlap)}",
        ))
        return []
    return [{**binding, **extension} for extension in _bindings(for_each, index_sets, issues, context)]


def _resolve_term(
    term: IndexedLinearTerm,
    *,
    binding: dict[str, object],
    index_sets: dict[str, list],
    variables: dict,
    parameters: dict,
    parameter_values: dict,
    issues: list[IndexedLinearIssue],
    context: str,
    provenance: list[dict[str, Any]],
) -> tuple[tuple[str, tuple], float] | None:
    reference = term.variable
    variable = variables.get(reference.variable_id)
    if variable is None:
        issues.append(IndexedLinearIssue(
            "indexed_unknown_variable",
            f"{context} references unknown variable {reference.variable_id!r}",
        ))
        return None
    coordinates = _resolve_coordinates(
        reference,
        index_set_ids=variable.index_sets,
        binding=binding,
        index_sets=index_sets,
        issues=issues,
        context=context,
        dimension_code="indexed_dimension_mismatch",
    )
    coefficient = _resolve_linear_value(
        term.coefficient,
        binding=binding,
        index_sets=index_sets,
        parameters=parameters,
        parameter_values=parameter_values,
        issues=issues,
        context=f"{context} coefficient",
        provenance=provenance,
    )
    if coordinates is None or coefficient is None:
        return None
    return (reference.variable_id, coordinates), coefficient


def _resolve_linear_value(
    value,
    *,
    binding: dict[str, object],
    index_sets: dict[str, list],
    parameters: dict,
    parameter_values: dict,
    issues: list[IndexedLinearIssue],
    context: str,
    provenance: list[dict[str, Any]],
) -> float | None:
    if isinstance(value, ConstantLinearValue):
        if not math.isfinite(value.value):
            issues.append(IndexedLinearIssue("indexed_nonfinite_value", f"{context} is non-finite"))
            return None
        return float(value.value)
    if not isinstance(value, ParameterLinearValue):
        issues.append(IndexedLinearIssue("indexed_invalid_linear_value", f"{context} is invalid"))
        return None
    parameter = parameters.get(value.parameter_id)
    if parameter is None:
        issues.append(IndexedLinearIssue(
            "indexed_unknown_parameter",
            f"{context} references unknown parameter {value.parameter_id!r}",
        ))
        return None
    coordinates = _resolve_selectors(
        value.selectors,
        index_set_ids=parameter.index_sets,
        binding=binding,
        index_sets=index_sets,
        issues=issues,
        context=context,
        dimension_code="indexed_parameter_dimension_mismatch",
    )
    if coordinates is None:
        return None
    registered = parameter_values.get((parameter.id, coordinates))
    if registered is None:
        issues.append(IndexedLinearIssue(
            "indexed_parameter_missing",
            f"{context} has no registered value for {parameter.id}{coordinates!r}",
        ))
        return None
    numeric, source_ref = registered
    coefficient = numeric * value.multiplier
    if not math.isfinite(coefficient):
        issues.append(IndexedLinearIssue("indexed_nonfinite_value", f"{context} is non-finite"))
        return None
    provenance.append({
        "context": context,
        "parameter_id": parameter.id,
        "coordinates": list(coordinates),
        "value": numeric,
        "multiplier": value.multiplier,
        "source_ref": source_ref,
    })
    return coefficient


def _resolve_coordinates(
    reference: IndexedVariableReference,
    **kwargs,
) -> tuple | None:
    return _resolve_selectors(reference.selectors, **kwargs)


def _resolve_selectors(
    selectors: list[IndexSelector],
    *,
    index_set_ids: list[str],
    binding: dict[str, object],
    index_sets: dict[str, list],
    issues: list[IndexedLinearIssue],
    context: str,
    dimension_code: str,
) -> tuple | None:
    if len(selectors) != len(index_set_ids):
        issues.append(IndexedLinearIssue(
            dimension_code,
            f"{context} expects {len(index_set_ids)} selectors, got {len(selectors)}",
        ))
        return None
    coordinates: list[object] = []
    for selector, index_set_id in zip(selectors, index_set_ids):
        domain = index_sets.get(index_set_id)
        if domain is None:
            return None
        if selector.binder is not None:
            if selector.binder not in binding:
                issues.append(IndexedLinearIssue(
                    "indexed_unknown_binder",
                    f"{context} references unbound index {selector.binder!r}",
                ))
                return None
            selected = binding[selector.binder]
        else:
            selected = selector.value
        if selected not in domain:
            issues.append(IndexedLinearIssue(
                "indexed_value_not_in_domain",
                f"{context} value {selected!r} is not in index set {index_set_id!r}",
            ))
            return None
        position = domain.index(selected) + selector.offset
        if position < 0 or position >= len(domain):
            issues.append(IndexedLinearIssue(
                "indexed_offset_out_of_range",
                f"{context} offset {selector.offset} leaves index set {index_set_id!r}",
            ))
            return None
        coordinates.append(domain[position])
    return tuple(coordinates)


def _indexed_label(identifier: str, coordinates: tuple) -> str:
    if not coordinates:
        return identifier
    return f"{identifier}[{','.join(str(value) for value in coordinates)}]"


def _binding_label(identifier: str, binding: dict[str, object]) -> str:
    if not binding:
        return identifier
    suffix = ",".join(f"{name}={value}" for name, value in binding.items())
    return f"{identifier}[{suffix}]"
