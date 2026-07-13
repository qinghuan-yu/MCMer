"""Execute a compiled graph exclusively through registered operator runtimes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import nbformat
from pydantic import BaseModel, ConfigDict, Field

from app.guidance.model_ir import BlockedModelIR, ModelIR, SolvableModelIR
from app.guidance.model_ir_compiler import CompilationResult, ExecutionPlan
from app.guidance.operators import OperatorRegistry


class ExecutionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OperatorExecutionFailure(ExecutionContract):
    node_id: str
    operator_id: str
    error_type: str
    message: str
    recovery_action: str


class OperatorExecutionManifest(ExecutionContract):
    schema_version: Literal["1.0"] = "1.0"
    model_id: str
    status: Literal["passed", "failed", "blocked"]
    execution_plan_ref: str = "execution_plan.json"
    execution_plan_sha256: str
    executed_node_ids: list[str]
    generated_files: list[str]
    failure: OperatorExecutionFailure | None = None


class OperatorGraphExecutor:
    def __init__(self, *, registry: OperatorRegistry) -> None:
        self.registry = registry

    def execute(
        self,
        *,
        execution_plan: ExecutionPlan | None,
        model_ir: ModelIR,
        work_dir: Path,
    ) -> OperatorExecutionManifest:
        if execution_plan is None:
            raise ValueError("a compiled execution plan is required")
        work_dir.mkdir(parents=True, exist_ok=True)
        values: dict[str, object] = {}
        artifact_nodes: dict[str, str] = {}
        executed: list[str] = []

        for node in execution_plan.graph.nodes:
            try:
                registered = self.registry.runtime(node.operator_id)
                if registered is None or registered.executor is None or registered.validator is None:
                    raise RuntimeError(f"operator runtime is not registered: {node.operator_id}")
                definition = registered.definition
                if definition.version != node.operator_version:
                    raise RuntimeError(f"operator version mismatch for node {node.node_id}")
                if definition.validator_id != node.validator_id:
                    raise RuntimeError(f"operator validator mismatch for node {node.node_id}")
                resolved_inputs = {
                    name: _resolve_input(reference, values)
                    for name, reference in node.inputs.items()
                }
                raw_outputs = registered.executor(
                    inputs=resolved_inputs,
                    parameters=node.parameters,
                    random_seed=node.random_seed,
                    work_dir=work_dir,
                )
                if not isinstance(raw_outputs, dict):
                    raise RuntimeError(f"operator returned a non-mapping result: {node.node_id}")
                issues = registered.validator(outputs=raw_outputs)
                if issues:
                    raise RuntimeError(
                        f"operator validation failed for {node.node_id}: " + "; ".join(issues)
                    )
                output_ports = list(definition.outputs)
                if set(raw_outputs) != set(output_ports):
                    raise RuntimeError(f"operator output ports do not match registration: {node.node_id}")
                for artifact, port_name in zip(node.outputs, output_ports):
                    values[artifact] = raw_outputs[port_name]
                    artifact_nodes[artifact] = node.node_id
                executed.append(node.node_id)
            except Exception as error:
                failure = OperatorExecutionFailure(
                    node_id=node.node_id,
                    operator_id=node.operator_id,
                    error_type=type(error).__name__,
                    message=str(error) or type(error).__name__,
                    recovery_action=_failure_recovery_action(error, node.node_id),
                )
                return self._write_runtime_failure(
                    execution_plan=execution_plan,
                    model_ir=model_ir,
                    work_dir=work_dir,
                    values=values,
                    artifact_nodes=artifact_nodes,
                    executed=executed,
                    failure=failure,
                )

        parameter_registry = _parameter_registry(model_ir, values, artifact_nodes)
        result_registry = _result_registry(model_ir, values, artifact_nodes)
        figures = [
            value
            for value in values.values()
            if isinstance(value, dict)
            and {"path", "role", "linked_result_ids"} <= set(value)
        ]
        figure_registry = {
            "figures": figures,
            "summary": {"total_count": len(figures)},
        }
        _write_json(work_dir / "parameter_registry.json", parameter_registry)
        _write_json(work_dir / "result_registry.json", result_registry)
        _write_json(work_dir / "figure_registry.json", figure_registry)
        _write_notebook(work_dir / "notebook.ipynb", execution_plan)

        plan_payload = execution_plan.model_dump_json()
        generated_files = [
            "execution_plan.json",
            "notebook.ipynb",
            "parameter_registry.json",
            "result_registry.json",
            "figure_registry.json",
            "execution_manifest.json",
        ]
        manifest = OperatorExecutionManifest(
            model_id=model_ir.model_id,
            status="passed",
            execution_plan_sha256=hashlib.sha256(plan_payload.encode("utf-8")).hexdigest(),
            executed_node_ids=executed,
            generated_files=generated_files,
        )
        _write_json(work_dir / "execution_manifest.json", manifest.model_dump(mode="json"))
        return manifest

    def _write_runtime_failure(
        self,
        *,
        execution_plan: ExecutionPlan,
        model_ir: ModelIR,
        work_dir: Path,
        values: dict[str, object],
        artifact_nodes: dict[str, str],
        executed: list[str],
        failure: OperatorExecutionFailure,
    ) -> OperatorExecutionManifest:
        parameter_registry = _parameter_registry(model_ir, values, artifact_nodes)
        result_registry = _failed_result_registry(
            model_ir,
            values,
            artifact_nodes,
            failure,
        )
        figures = [
            value
            for value in values.values()
            if isinstance(value, dict)
            and {"path", "role", "linked_result_ids"} <= set(value)
        ]
        _write_json(work_dir / "parameter_registry.json", parameter_registry)
        _write_json(
            work_dir / "result_registry.json",
            result_registry,
        )
        _write_json(
            work_dir / "figure_registry.json",
            {"figures": figures, "summary": {"total_count": len(figures)}},
        )
        _write_failed_notebook(work_dir / "notebook.ipynb", execution_plan, failure)
        generated_files = [
            "execution_plan.json",
            "notebook.ipynb",
            "parameter_registry.json",
            "result_registry.json",
            "figure_registry.json",
            "execution_manifest.json",
        ]
        plan_payload = execution_plan.model_dump_json()
        manifest = OperatorExecutionManifest(
            model_id=model_ir.model_id,
            status="failed",
            execution_plan_sha256=hashlib.sha256(plan_payload.encode("utf-8")).hexdigest(),
            executed_node_ids=list(executed),
            generated_files=generated_files,
            failure=failure,
        )
        _write_json(work_dir / "execution_manifest.json", manifest.model_dump(mode="json"))
        return manifest

    def write_blocked(
        self,
        *,
        compilation_result: CompilationResult,
        model_ir: ModelIR,
        work_dir: Path,
    ) -> OperatorExecutionManifest:
        work_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            work_dir / "parameter_registry.json",
            {"parameters": [], "summary": {"total_count": 0, "blocked_count": 0}},
        )
        _write_json(
            work_dir / "result_registry.json",
            {
                "verified_results": [],
                "blocked_results": [
                    {
                        "id": f"compilation_block_{index}",
                        "reason": diagnostic.message,
                        "code": diagnostic.code,
                        "subproblem_id": diagnostic.subproblem_id,
                        "node_id": diagnostic.node_id,
                        "recovery_action": _compilation_recovery_action(diagnostic.code),
                    }
                    for index, diagnostic in enumerate(
                        compilation_result.diagnostics,
                        start=1,
                    )
                ],
                "summary": {
                    "coverage_status": "blocked",
                    "verified_count": 0,
                    "blocked_count": len(compilation_result.diagnostics),
                },
            },
        )
        _write_json(
            work_dir / "figure_registry.json",
            {"figures": [], "summary": {"total_count": 0}},
        )
        _write_blocked_notebook(work_dir / "notebook.ipynb", model_ir.model_id)
        generated_files = [
            "compilation_result.json",
            "notebook.ipynb",
            "parameter_registry.json",
            "result_registry.json",
            "figure_registry.json",
            "execution_manifest.json",
        ]
        manifest = OperatorExecutionManifest(
            model_id=model_ir.model_id,
            status="blocked",
            execution_plan_sha256="",
            executed_node_ids=[],
            generated_files=generated_files,
        )
        _write_json(work_dir / "execution_manifest.json", manifest.model_dump(mode="json"))
        return manifest


def _resolve_input(reference: str, values: dict[str, object]) -> object:
    owner, separator, field = reference.partition(".")
    if separator and owner in values:
        value = values[owner]
        if isinstance(value, dict) and field in value:
            return value[field]
    if reference in values:
        return values[reference]
    return reference


def _parameter_registry(
    model_ir: ModelIR,
    values: dict[str, object],
    artifact_nodes: dict[str, str],
) -> dict:
    parameters: list[dict] = []
    for subproblem in model_ir.subproblems:
        if not isinstance(subproblem, SolvableModelIR):
            continue
        for variable in subproblem.variables:
            if variable.role != "unknown_parameter" or variable.id not in values:
                continue
            parameters.append({
                "parameter_id": variable.id,
                "symbol": variable.symbol,
                "value": values[variable.id],
                "unit": variable.unit,
                "source_node": artifact_nodes[variable.id],
                "status": "estimated",
            })
    return {
        "parameters": parameters,
        "summary": {"total_count": len(parameters), "blocked_count": 0},
    }


def _result_registry(
    model_ir: ModelIR,
    values: dict[str, object],
    artifact_nodes: dict[str, str],
) -> dict:
    verified: list[dict] = []
    blocked: list[dict] = []
    variables = {
        variable.id: variable
        for subproblem in model_ir.subproblems
        if isinstance(subproblem, SolvableModelIR)
        for variable in subproblem.variables
    }
    for subproblem in model_ir.subproblems:
        if isinstance(subproblem, BlockedModelIR):
            blocked.append({
                "id": "blocked_" + subproblem.subproblem_id.replace(".", "_"),
                "subproblem_id": subproblem.subproblem_id,
                "reason": subproblem.blocking_reason,
                "missing_inputs": subproblem.missing_inputs,
                "recovery_action": subproblem.recovery_action,
                "expected_outputs": subproblem.expected_outputs,
            })
            continue
        if not isinstance(subproblem, SolvableModelIR):
            continue
        for answer in subproblem.final_answers:
            if answer.value_ref not in values:
                blocked.append({
                    "id": answer.id,
                    "subproblem_id": subproblem.subproblem_id,
                    "reason": f"missing runtime value: {answer.value_ref}",
                })
                continue
            variable = variables.get(answer.value_ref)
            verified.append({
                "id": answer.id,
                "subproblem_id": subproblem.subproblem_id,
                "value": values[answer.value_ref],
                "unit": variable.unit if variable is not None else "unspecified",
                "source_node": artifact_nodes[answer.value_ref],
                "evidence_ids": answer.evidence_ids,
                "status": answer.status,
            })
    return {
        "verified_results": verified,
        "blocked_results": blocked,
        "summary": {
            "coverage_status": "complete" if not blocked else "partial",
            "verified_count": len(verified),
            "blocked_count": len(blocked),
        },
    }


def _failed_result_registry(
    model_ir: ModelIR,
    values: dict[str, object],
    artifact_nodes: dict[str, str],
    failure: OperatorExecutionFailure,
) -> dict:
    registry = _result_registry(model_ir, values, artifact_nodes)
    affected = False
    for blocked in registry["blocked_results"]:
        reason = str(blocked.get("reason") or "")
        if reason.startswith("missing runtime value:"):
            blocked.update({
                "reason": f"{failure.error_type}: {failure.message}",
                "failed_node_id": failure.node_id,
                "failed_operator_id": failure.operator_id,
                "recovery_action": failure.recovery_action,
            })
            affected = True
    if not affected:
        registry["blocked_results"].append({
            "id": f"execution_failure_{failure.node_id}",
            "reason": f"{failure.error_type}: {failure.message}",
            "failed_node_id": failure.node_id,
            "failed_operator_id": failure.operator_id,
            "recovery_action": failure.recovery_action,
        })
    verified_count = len(registry["verified_results"])
    blocked_count = len(registry["blocked_results"])
    registry["summary"] = {
        "coverage_status": "partial" if verified_count else "blocked",
        "verified_count": verified_count,
        "blocked_count": blocked_count,
    }
    return registry


def _failure_recovery_action(error: Exception, node_id: str) -> str:
    if isinstance(error, FileNotFoundError):
        return (
            "Provide the declared attachment at the registered workspace path, "
            f"then rerun node {node_id}."
        )
    if isinstance(error, ValueError):
        return (
            f"Correct the declared inputs, dimensions, bounds, or model assumptions for node {node_id}, "
            "then recompile and rerun the execution plan."
        )
    return (
        f"Inspect the registered inputs and operator diagnostics for node {node_id}, "
        "correct the failure, and rerun the same execution plan."
    )


def _compilation_recovery_action(code: str) -> str:
    if code in {"input_type_mismatch", "output_type_mismatch", "unit_conflict"}:
        return (
            "Align the declared variable and operator units and value kinds in Model IR, "
            "then compile the execution plan again."
        )
    if code == "operator_not_registered":
        return (
            "Register a problem-independent operator with the required typed ports, "
            "or mark the affected subproblem blocked with the missing capability."
        )
    if code == "precondition_unsatisfied":
        return (
            "Satisfy and record the operator mathematical preconditions in Model IR, "
            "then compile again."
        )
    return "Correct the reported Model IR contract diagnostic, then compile again."


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_notebook(path: Path, execution_plan: ExecutionPlan) -> None:
    notebook = nbformat.v4.new_notebook(
        metadata={"execution_plan": "execution_plan.json"},
        cells=[
            nbformat.v4.new_markdown_cell(
                "# Reproducible operator execution\n\n"
                "This notebook records the validated execution plan; registered operators remain "
                "the runtime source of truth."
            ),
            nbformat.v4.new_code_cell(
                "import json\n"
                "with open('execution_plan.json', encoding='utf-8') as stream:\n"
                "    execution_plan = json.load(stream)\n"
                "execution_plan"
            ),
        ],
    )
    notebook.metadata["model_id"] = execution_plan.model_id
    nbformat.write(notebook, path)


def _write_blocked_notebook(path: Path, model_id: str) -> None:
    notebook = nbformat.v4.new_notebook(
        metadata={"model_id": model_id, "execution_status": "blocked"},
        cells=[
            nbformat.v4.new_markdown_cell(
                "# Operator execution blocked\n\n"
                "See `compilation_result.json` for missing operators or invalid contracts."
            )
        ],
    )
    nbformat.write(notebook, path)


def _write_failed_notebook(
    path: Path,
    execution_plan: ExecutionPlan,
    failure: OperatorExecutionFailure,
) -> None:
    notebook = nbformat.v4.new_notebook(
        metadata={
            "model_id": execution_plan.model_id,
            "execution_plan": "execution_plan.json",
            "execution_status": "failed",
            "failed_node_id": failure.node_id,
        },
        cells=[
            nbformat.v4.new_markdown_cell(
                "# Operator execution failed\n\n"
                f"- Node: `{failure.node_id}`\n"
                f"- Operator: `{failure.operator_id}`\n"
                f"- Error: `{failure.error_type}`\n"
                f"- Message: {failure.message}\n"
                f"- Recovery: {failure.recovery_action}\n\n"
                "No missing downstream result is verified. See `result_registry.json` for blocked outputs."
            )
        ],
    )
    nbformat.write(notebook, path)
