"""Static semantic checks for Model IR before operator compilation."""

from __future__ import annotations

from dataclasses import dataclass

from app.guidance.model_ir import (
    BinaryExpression,
    Expression,
    FunctionExpression,
    LiteralExpression,
    ModelIR,
    SolvableModelIR,
    SymbolExpression,
)


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
        variables = {item.id: item for item in subproblem.variables}
        data_sources = {item.id: item for item in subproblem.data}
        operator_nodes = {item.node_id: item for item in subproblem.operators}

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

        for operator_index, invocation in enumerate(subproblem.operators):
            for input_name, reference in invocation.inputs.items():
                owner, separator, field = reference.partition(".")
                data_ref = data_sources.get(owner)
                if separator and data_ref is not None and field not in data_ref.fields:
                    diagnostics.append(SemanticDiagnostic(
                        code="unknown_data_field",
                        subproblem_id=subproblem.subproblem_id,
                        path=f"{base}.operators[{operator_index}].inputs[{input_name}]",
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

        cycle = self._find_operator_cycle(subproblem)
        if cycle:
            diagnostics.append(SemanticDiagnostic(
                code="operator_cycle",
                subproblem_id=subproblem.subproblem_id,
                path=f"{base}.operators",
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
    def _find_operator_cycle(subproblem: SolvableModelIR) -> list[str]:
        node_ids = {item.node_id for item in subproblem.operators}
        dependencies = {
            item.node_id: {
                reference.partition(".")[0]
                for reference in item.inputs.values()
                if reference.partition(".")[1]
                and reference.partition(".")[0] in node_ids
            }
            for item in subproblem.operators
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
