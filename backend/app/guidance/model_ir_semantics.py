"""Static semantic checks for Model IR before operator compilation."""

from __future__ import annotations

from dataclasses import dataclass

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import (
    BinaryExpression,
    Expression,
    FunctionExpression,
    LiteralExpression,
    ModelIR,
    IndexedLinearExecutionComponent,
    OperatorGraphExecutionComponent,
    OperatorInvocation,
    SolvableModelIR,
    SymbolExpression,
)


def validate_model_fact_coverage(
    problem_spec: ProblemSpec,
    model_ir: ModelIR,
) -> None:
    facts_by_id = {fact.fact_id: fact for fact in problem_spec.locked_facts}
    subproblems_by_id = {item.id: item for item in problem_spec.subproblems}
    for subproblem in model_ir.subproblems:
        if not isinstance(subproblem, SolvableModelIR):
            continue
        spec_subproblem = subproblems_by_id.get(subproblem.subproblem_id)
        binding_ids = [binding.fact_id for binding in subproblem.fact_bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError(
                f"duplicate fact bindings for subproblem {subproblem.subproblem_id!r}"
            )
        unknown = sorted(set(binding_ids) - set(facts_by_id))
        if unknown:
            raise ValueError(f"unknown locked fact ids: {unknown!r}")
        expected = sorted(
            spec_subproblem.required_fact_ids
            if spec_subproblem is not None
            else [
                fact.fact_id
                for fact in problem_spec.locked_facts
                if subproblem.subproblem_id in fact.subproblem_ids
            ]
        )
        modeled = sorted(binding_ids)
        if expected != modeled:
            raise ValueError(
                f"fact coverage mismatch for {subproblem.subproblem_id!r}: "
                f"expected={expected!r}; modeled={modeled!r}"
            )
        required_uncertainty_ids = (
            spec_subproblem.required_uncertainty_ids
            if spec_subproblem is not None
            else [
                uncertainty.uncertainty_id
                for uncertainty in problem_spec.uncertainties
                if subproblem.subproblem_id in uncertainty.subproblem_ids
            ]
        )
        allowed_uncertainty_refs = {
            f"problem_spec.json#uncertainties/{uncertainty_id}"
            for uncertainty_id in required_uncertainty_ids
        }
        for assumption in subproblem.assumptions:
            if assumption.basis != "problem_ambiguity":
                continue
            invalid_refs = sorted(set(assumption.source_refs) - allowed_uncertainty_refs)
            if invalid_refs:
                raise ValueError(
                    "unknown or out-of-scope uncertainty refs for "
                    f"{subproblem.subproblem_id!r}: {invalid_refs!r}"
                )
        payload = subproblem.model_dump(mode="json")
        for binding in subproblem.fact_bindings:
            for path in binding.model_paths:
                try:
                    _resolve_json_pointer(payload, path)
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"model path does not exist for fact {binding.fact_id!r}: {path}"
                    ) from exc


def _resolve_json_pointer(document: object, pointer: str) -> object:
    current = document
    for raw_token in pointer.removeprefix("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise TypeError("JSON pointer traversed a scalar value")
    return current


@dataclass(frozen=True)
class SemanticDiagnostic:
    code: str
    subproblem_id: str
    path: str
    message: str


class ModelIRSemanticValidator:
    def validate(self, model_ir: ModelIR) -> list[SemanticDiagnostic]:
        diagnostics: list[SemanticDiagnostic] = []
        for subproblem in model_ir.subproblems:
            if isinstance(subproblem, SolvableModelIR):
                diagnostics.extend(self.validate_subproblem(subproblem))
        return diagnostics

    def validate_subproblem(
        self,
        subproblem: SolvableModelIR,
    ) -> list[SemanticDiagnostic]:
        diagnostics: list[SemanticDiagnostic] = []
        base = f"subproblems[{subproblem.subproblem_id}]"
        graph_components = [
            (index, component)
            for index, component in enumerate(subproblem.execution_components)
            if isinstance(component, OperatorGraphExecutionComponent)
        ]
        indexed_components = [
            (index, component)
            for index, component in enumerate(subproblem.execution_components)
            if isinstance(component, IndexedLinearExecutionComponent)
        ]
        variables = {
            item.id: item
            for _, component in graph_components
            for item in component.variables
        }
        operators = [
            (component_index, operator_index, invocation)
            for component_index, component in graph_components
            for operator_index, invocation in enumerate(component.operators)
        ]
        data_sources = {item.id: item for item in subproblem.data}
        operator_nodes = {
            invocation.node_id: invocation
            for _, _, invocation in operators
        }
        operator_nodes.update({
            component.solver_node_id: component
            for _, component in indexed_components
        })

        for data_index, data_ref in enumerate(subproblem.data):
            for field_id, override_unit in data_ref.unit_overrides.items():
                variable = variables.get(field_id)
                if variable is not None and variable.unit != override_unit:
                    diagnostics.append(SemanticDiagnostic(
                        code="unit_conflict",
                        subproblem_id=subproblem.subproblem_id,
                        path=f"{base}.data[{data_index}].unit_overrides[{field_id}]",
                        message=(
                            f"data unit {override_unit!r} conflicts with variable unit "
                            f"{variable.unit!r}"
                        ),
                    ))

        for component_index, operator_index, invocation in operators:
            for input_name, reference in invocation.inputs.items():
                owner, separator, field = reference.partition(".")
                data_ref = data_sources.get(owner)
                if separator and data_ref is not None and field not in data_ref.fields:
                    diagnostics.append(SemanticDiagnostic(
                        code="unknown_data_field",
                        subproblem_id=subproblem.subproblem_id,
                        path=(
                            f"{base}.execution_components[{component_index}]"
                            f".operators[{operator_index}].inputs[{input_name}]"
                        ),
                        message=f"data reference {reference!r} does not name a declared field",
                    ))

        for equation_index, equation in enumerate(subproblem.equations):
            left_shape = self._expression_shape(
                equation.left,
                variables,
                diagnostics,
                subproblem.subproblem_id,
                f"{base}.equations[{equation_index}].left",
            )
            right_shape = self._expression_shape(
                equation.right,
                variables,
                diagnostics,
                subproblem.subproblem_id,
                f"{base}.equations[{equation_index}].right",
            )
            if left_shape is not None and right_shape is not None and left_shape != right_shape:
                diagnostics.append(SemanticDiagnostic(
                    code="dimension_mismatch",
                    subproblem_id=subproblem.subproblem_id,
                    path=f"{base}.equations[{equation_index}]",
                    message=f"equation shapes differ: {left_shape} != {right_shape}",
                ))

        cycle = self._find_operator_cycle([
            invocation for _, _, invocation in operators
        ])
        if cycle:
            diagnostics.append(SemanticDiagnostic(
                code="operator_cycle",
                subproblem_id=subproblem.subproblem_id,
                path=f"{base}.execution_components",
                message="operator dependency cycle: " + " -> ".join(cycle),
            ))

        for candidate_index, candidate in enumerate(subproblem.candidate_models):
            for node_id in candidate.operator_node_ids:
                if node_id not in operator_nodes:
                    diagnostics.append(SemanticDiagnostic(
                        code="unknown_operator_node",
                        subproblem_id=subproblem.subproblem_id,
                        path=(
                            f"{base}.candidate_models[{candidate_index}]"
                            ".operator_node_ids"
                        ),
                        message=f"candidate references unknown operator node {node_id!r}",
                    ))

        diagnostics.extend(self._validate_indexed_architecture_coverage(subproblem))
        return diagnostics

    @staticmethod
    def _validate_indexed_architecture_coverage(
        subproblem: SolvableModelIR,
    ) -> list[SemanticDiagnostic]:
        base = f"subproblems[{subproblem.subproblem_id}]"
        produced_artifacts = {
            artifact
            for component in subproblem.execution_components
            if isinstance(component, OperatorGraphExecutionComponent)
            for operator in component.operators
            for artifact in operator.outputs
        }
        diagnostics: list[SemanticDiagnostic] = []
        for component_index, component in enumerate(subproblem.execution_components):
            if not isinstance(component, IndexedLinearExecutionComponent):
                continue
            indexed_model = component.model
            component_path = f"{base}.execution_components[{component_index}].model"
            used_references = {
                term.variable.variable_id
                for term in indexed_model.objective.terms
            } | {
                term.variable.variable_id
                for constraint in indexed_model.constraints
                for term in constraint.terms
            }
            for parameter_index, parameter in enumerate(indexed_model.parameters):
                source = parameter.runtime_source
                if (
                    source is not None
                    and source.artifact_ref not in produced_artifacts
                ):
                    diagnostics.append(SemanticDiagnostic(
                        code="indexed_runtime_source_unknown_artifact",
                        subproblem_id=subproblem.subproblem_id,
                        path=(
                            f"{component_path}.parameters[{parameter_index}]"
                            ".runtime_source.artifact_ref"
                        ),
                        message=(
                            f"runtime parameter {parameter.id!r} references an "
                            f"unknown operator artifact: {source.artifact_ref}"
                        ),
                    ))
            for variable_index, variable in enumerate(indexed_model.variables):
                if variable.id not in used_references:
                    diagnostics.append(SemanticDiagnostic(
                        code="indexed_variable_unused",
                        subproblem_id=subproblem.subproblem_id,
                        path=f"{component_path}.variables[{variable_index}]",
                        message=(
                            f"indexed variable {variable.id!r} is absent from "
                            "the objective and every constraint"
                        ),
                    ))

        return diagnostics

    def _expression_shape(
        self,
        expression: Expression,
        variables: dict[str, object],
        diagnostics: list[SemanticDiagnostic],
        subproblem_id: str,
        path: str,
    ) -> tuple[str, ...] | None:
        if isinstance(expression, SymbolExpression):
            variable = variables.get(expression.symbol)
            if variable is None:
                diagnostics.append(SemanticDiagnostic(
                    code="unknown_symbol",
                    subproblem_id=subproblem_id,
                    path=path,
                    message=f"expression references unknown symbol {expression.symbol!r}",
                ))
                return None
            return tuple(variable.shape)
        if isinstance(expression, LiteralExpression):
            return ()
        if isinstance(expression, BinaryExpression):
            left = self._expression_shape(
                expression.left,
                variables,
                diagnostics,
                subproblem_id,
                f"{path}.left",
            )
            right = self._expression_shape(
                expression.right,
                variables,
                diagnostics,
                subproblem_id,
                f"{path}.right",
            )
            if left is None or right is None:
                return None
            if expression.operator in {"add", "subtract"}:
                return left if left == right else None
            if left == ():
                return right
            if right == ():
                return left
            return left if left == right else None
        if isinstance(expression, FunctionExpression):
            shapes = [
                self._expression_shape(
                    argument,
                    variables,
                    diagnostics,
                    subproblem_id,
                    f"{path}.arguments[{index}]",
                )
                for index, argument in enumerate(expression.arguments)
            ]
            known = [shape for shape in shapes if shape is not None]
            if not known:
                return None
            return known[0] if all(shape == known[0] for shape in known) else None
        return None

    @staticmethod
    def _find_operator_cycle(operators: list[OperatorInvocation]) -> list[str]:
        node_ids = {item.node_id for item in operators}
        dependencies = {
            item.node_id: {
                reference.partition(".")[0]
                for reference in item.inputs.values()
                if reference.partition(".")[1]
                and reference.partition(".")[0] in node_ids
            }
            for item in operators
        }
        visiting: set[str] = set()
        visited: set[str] = set()
        stack: list[str] = []

        def visit(node_id: str) -> list[str]:
            if node_id in visiting:
                start = stack.index(node_id)
                return [*stack[start:], node_id]
            if node_id in visited:
                return []
            visiting.add(node_id)
            stack.append(node_id)
            for dependency in sorted(dependencies[node_id]):
                cycle = visit(dependency)
                if cycle:
                    return cycle
            stack.pop()
            visiting.remove(node_id)
            visited.add(node_id)
            return []

        for node_id in sorted(node_ids):
            cycle = visit(node_id)
            if cycle:
                return cycle
        return []
