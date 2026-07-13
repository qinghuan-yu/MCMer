"""Compile validated Model IR into a typed, topological operator graph."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.guidance.model_ir import BlockedModelIR, ModelIR, SolvableModelIR
from app.guidance.operators import OperatorDefinition, OperatorPort, OperatorRegistry


class CompilerContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CompilationDiagnostic(CompilerContract):
    code: str
    subproblem_id: str
    node_id: str | None = None
    message: str


class BlockedCompilation(CompilerContract):
    subproblem_id: str
    missing_operator_ids: list[str] = Field(default_factory=list)
    recovery_action: str


class ExecutionNode(CompilerContract):
    node_id: str
    subproblem_id: str
    ir_source: str
    operator_id: str
    operator_version: str
    inputs: dict[str, str]
    outputs: list[str]
    parameters: dict
    random_seed: int | None = None
    validator_id: str
    dependencies: list[str] = Field(default_factory=list)


class OperatorExecutionGraph(CompilerContract):
    nodes: list[ExecutionNode]


class ExecutionPlan(CompilerContract):
    schema_version: Literal["1.0"] = "1.0"
    model_id: str
    graph: OperatorExecutionGraph


class CompilationResult(CompilerContract):
    status: Literal["compiled", "blocked"]
    execution_plan: ExecutionPlan | None = None
    blocked_subproblems: list[BlockedCompilation] = Field(default_factory=list)
    diagnostics: list[CompilationDiagnostic] = Field(default_factory=list)


class ModelIRCompiler:
    def __init__(self, *, registry: OperatorRegistry) -> None:
        self.registry = registry

    def compile(
        self,
        model_ir: ModelIR,
        *,
        model_ir_ref: str = "model_ir.json",
    ) -> CompilationResult:
        diagnostics: list[CompilationDiagnostic] = []
        blocked: list[BlockedCompilation] = []
        nodes: list[ExecutionNode] = []

        for subproblem in model_ir.subproblems:
            if isinstance(subproblem, BlockedModelIR):
                blocked.append(BlockedCompilation(
                    subproblem_id=subproblem.subproblem_id,
                    recovery_action=subproblem.recovery_action,
                ))
                continue
            sub_diagnostics, sub_nodes, missing = self._compile_subproblem(
                subproblem,
                model_ir_ref=model_ir_ref,
            )
            diagnostics.extend(sub_diagnostics)
            nodes.extend(sub_nodes)
            if sub_diagnostics:
                blocked.append(BlockedCompilation(
                    subproblem_id=subproblem.subproblem_id,
                    missing_operator_ids=missing,
                    recovery_action=(
                        "Register and verify the missing operators, then recompile the approved "
                        "Model IR."
                        if missing
                        else "Resolve the compiler diagnostics, then recompile the approved Model IR."
                    ),
                ))

        if diagnostics:
            return CompilationResult(
                status="blocked",
                blocked_subproblems=blocked,
                diagnostics=diagnostics,
            )
        return CompilationResult(
            status="compiled",
            blocked_subproblems=blocked,
            execution_plan=ExecutionPlan(
                model_id=model_ir.model_id,
                graph=OperatorExecutionGraph(nodes=nodes),
            ),
        )

    def compile_to_file(
        self,
        model_ir: ModelIR,
        *,
        output_path,
        model_ir_ref: str = "model_ir.json",
    ) -> CompilationResult:
        result = self.compile(model_ir, model_ir_ref=model_ir_ref)
        if result.execution_plan is None:
            return result
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_name(output_path.name + ".tmp")
        temporary_path.write_text(
            result.execution_plan.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(output_path)
        return result

    def _compile_subproblem(
        self,
        subproblem: SolvableModelIR,
        *,
        model_ir_ref: str,
    ) -> tuple[list[CompilationDiagnostic], list[ExecutionNode], list[str]]:
        diagnostics: list[CompilationDiagnostic] = []
        definitions: dict[str, OperatorDefinition] = {}
        missing = sorted({
            invocation.operator_id
            for invocation in subproblem.operators
            if self.registry.get(invocation.operator_id) is None
        })
        for operator_id in missing:
            diagnostics.append(CompilationDiagnostic(
                code="missing_operator",
                subproblem_id=subproblem.subproblem_id,
                message=f"operator is not registered: {operator_id}",
            ))
        for invocation in subproblem.operators:
            definition = self.registry.get(invocation.operator_id)
            if definition is not None:
                definitions[invocation.node_id] = definition

        diagnostics.extend(self._data_reference_diagnostics(subproblem))
        order, cycle = _topological_order(subproblem)
        if cycle:
            diagnostics.append(CompilationDiagnostic(
                code="operator_cycle",
                subproblem_id=subproblem.subproblem_id,
                message="operator dependency cycle: " + " -> ".join(cycle),
            ))

        variables = {item.id: item for item in subproblem.variables}
        data = {item.id: item for item in subproblem.data}
        invocations = {item.node_id: item for item in subproblem.operators}
        declared_artifacts = set(variables)
        for answer in subproblem.final_answers:
            declared_artifacts.add(answer.value_ref)
            declared_artifacts.update(answer.evidence_ids)
        satisfied_preconditions = {
            *subproblem.validation.identifiability_checks,
            *subproblem.validation.evidence_checks,
            *subproblem.validation.robustness_checks,
        }

        for invocation in subproblem.operators:
            definition = definitions.get(invocation.node_id)
            if definition is None:
                continue
            expected_inputs = set(definition.inputs)
            actual_inputs = set(invocation.inputs)
            if expected_inputs != actual_inputs:
                diagnostics.append(CompilationDiagnostic(
                    code="input_schema_mismatch",
                    subproblem_id=subproblem.subproblem_id,
                    node_id=invocation.node_id,
                    message=(
                        f"expected input ports {sorted(expected_inputs)}, got "
                        f"{sorted(actual_inputs)}"
                    ),
                ))
            for port_name in sorted(expected_inputs & actual_inputs):
                actual = _reference_port(
                    invocation.inputs[port_name],
                    variables=variables,
                    data=data,
                    invocations=invocations,
                    definitions=definitions,
                )
                expected = definition.inputs[port_name]
                if actual is not None and not _ports_match(actual, expected):
                    diagnostics.append(CompilationDiagnostic(
                        code="input_type_mismatch",
                        subproblem_id=subproblem.subproblem_id,
                        node_id=invocation.node_id,
                        message=(
                            f"input {port_name!r} expects {expected.value_kind}/{expected.unit}, "
                            f"got {actual.value_kind}/{actual.unit}"
                        ),
                    ))
            expected_outputs = list(definition.outputs.values())
            if len(expected_outputs) != len(invocation.outputs):
                diagnostics.append(CompilationDiagnostic(
                    code="output_schema_mismatch",
                    subproblem_id=subproblem.subproblem_id,
                    node_id=invocation.node_id,
                    message=(
                        f"expected {len(expected_outputs)} outputs, got "
                        f"{len(invocation.outputs)}"
                    ),
                ))
            for artifact, expected in zip(invocation.outputs, expected_outputs):
                if artifact not in declared_artifacts:
                    diagnostics.append(CompilationDiagnostic(
                        code="undeclared_artifact",
                        subproblem_id=subproblem.subproblem_id,
                        node_id=invocation.node_id,
                        message=f"operator output is not declared by Model IR: {artifact}",
                    ))
                variable = variables.get(artifact)
                if variable is not None:
                    actual = OperatorPort(
                        value_kind=_value_kind(variable.shape),
                        unit=variable.unit,
                    )
                    if not _ports_match(actual, expected):
                        diagnostics.append(CompilationDiagnostic(
                            code="output_type_mismatch",
                            subproblem_id=subproblem.subproblem_id,
                            node_id=invocation.node_id,
                            message=(
                                f"output {artifact!r} expects {expected.value_kind}/{expected.unit}, "
                                f"got {actual.value_kind}/{actual.unit}"
                            ),
                        ))
            for precondition in definition.preconditions:
                if precondition not in satisfied_preconditions:
                    diagnostics.append(CompilationDiagnostic(
                        code="precondition_unsatisfied",
                        subproblem_id=subproblem.subproblem_id,
                        node_id=invocation.node_id,
                        message=f"operator precondition is not satisfied: {precondition}",
                    ))
            if not definition.deterministic and invocation.random_seed is None:
                diagnostics.append(CompilationDiagnostic(
                    code="random_seed_required",
                    subproblem_id=subproblem.subproblem_id,
                    node_id=invocation.node_id,
                    message="nondeterministic operator requires an explicit random seed",
                ))

        compiled_nodes: list[ExecutionNode] = []
        if order is not None:
            for node_id in order:
                invocation = invocations[node_id]
                definition = definitions.get(node_id)
                if definition is None:
                    continue
                compiled_nodes.append(ExecutionNode(
                    node_id=node_id,
                    subproblem_id=subproblem.subproblem_id,
                    ir_source=(
                        f"{model_ir_ref}#subproblems[{subproblem.subproblem_id}]"
                        f".operators[{node_id}]"
                    ),
                    operator_id=definition.operator_id,
                    operator_version=definition.version,
                    inputs=invocation.inputs,
                    outputs=invocation.outputs,
                    parameters=invocation.parameters,
                    random_seed=invocation.random_seed,
                    validator_id=definition.validator_id,
                    dependencies=sorted(_node_dependencies(
                        invocation,
                        node_ids=set(invocations),
                        artifact_producers=_artifact_producers(subproblem),
                    )),
                ))
        return diagnostics, compiled_nodes, missing

    @staticmethod
    def _data_reference_diagnostics(
        subproblem: SolvableModelIR,
    ) -> list[CompilationDiagnostic]:
        diagnostics: list[CompilationDiagnostic] = []
        for data_ref in subproblem.data:
            path = data_ref.source_ref.split("#", 1)[0].replace("\\", "/")
            parsed = PurePosixPath(path)
            if parsed.is_absolute() or ".." in parsed.parts or re.match(r"^[A-Za-z]:", path):
                diagnostics.append(CompilationDiagnostic(
                    code="unsafe_data_reference",
                    subproblem_id=subproblem.subproblem_id,
                    message=f"data reference escapes the workspace: {data_ref.source_ref}",
                ))
        return diagnostics


def _ports_match(actual: OperatorPort, expected: OperatorPort) -> bool:
    return actual.value_kind == expected.value_kind and actual.unit == expected.unit


def _value_kind(shape: list[str]) -> Literal["scalar", "vector", "matrix", "table", "artifact"]:
    if not shape:
        return "scalar"
    if len(shape) == 1:
        return "vector"
    if len(shape) == 2:
        return "matrix"
    return "table"


def _reference_port(
    reference: str,
    *,
    variables: dict,
    data: dict,
    invocations: dict,
    definitions: dict[str, OperatorDefinition],
) -> OperatorPort | None:
    owner, separator, field = reference.partition(".")
    if separator and owner in data:
        variable = variables.get(field)
        if variable is None:
            return None
        return OperatorPort(value_kind=_value_kind(variable.shape), unit=variable.unit)
    if separator and owner in invocations:
        definition = definitions.get(owner)
        if definition is None:
            return None
        if field in definition.outputs:
            return definition.outputs[field]
        invocation = invocations[owner]
        if field in invocation.outputs:
            index = invocation.outputs.index(field)
            ports = list(definition.outputs.values())
            return ports[index] if index < len(ports) else None
        return None
    variable = variables.get(reference)
    if variable is None:
        return None
    return OperatorPort(value_kind=_value_kind(variable.shape), unit=variable.unit)


def _artifact_producers(subproblem: SolvableModelIR) -> dict[str, str]:
    return {
        artifact: invocation.node_id
        for invocation in subproblem.operators
        for artifact in invocation.outputs
    }


def _node_dependencies(
    invocation,
    *,
    node_ids: set[str],
    artifact_producers: dict[str, str],
) -> set[str]:
    dependencies: set[str] = set()
    for reference in invocation.inputs.values():
        producer = artifact_producers.get(reference)
        if producer is not None:
            dependencies.add(producer)
            continue
        owner, separator, _ = reference.partition(".")
        if separator and owner in node_ids:
            dependencies.add(owner)
    return dependencies


def _topological_order(
    subproblem: SolvableModelIR,
) -> tuple[list[str] | None, list[str]]:
    invocations = {item.node_id: item for item in subproblem.operators}
    artifact_producers = _artifact_producers(subproblem)
    dependencies = {
        node_id: _node_dependencies(
            invocation,
            node_ids=set(invocations),
            artifact_producers=artifact_producers,
        )
        for node_id, invocation in invocations.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    order: list[str] = []

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
        order.append(node_id)
        return []

    for node_id in sorted(invocations):
        cycle = visit(node_id)
        if cycle:
            return None, cycle
    return order, []
