"""Execute a compiled graph exclusively through registered operator runtimes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import nbformat
from pydantic import BaseModel, ConfigDict, Field

from app.guidance.artifacts import ClaimRecord
from app.guidance.model_ir import (
    BlockedModelIR,
    ModelIR,
    OperatorGraphExecutionComponent,
    SolvableModelIR,
)
from app.guidance.model_ir_compiler import CompilationResult, ExecutionPlan
from app.guidance.operators import OperatorRegistry
from app.guidance.recovery import (
    RecoveryCheckpoint, canonical_digest, file_snapshots, generated_figure_paths,
    invalidate_stage_outputs, model_source_snapshots,
)


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
    status: Literal["passed", "partial", "failed", "blocked"]
    execution_plan_ref: str = "execution_plan.json"
    execution_plan_sha256: str
    model_sha256: str
    source_files_sha256: dict[str, str | None]
    input_files_sha256: dict[str, str | None]
    output_files_sha256: dict[str, str | None]
    executed_node_ids: list[str]
    reused_node_ids: list[str] = Field(default_factory=list)
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
        invalidate_stage_outputs(work_dir, "result_verification")
        values: dict[str, object] = {}
        artifact_nodes: dict[str, str] = {}
        executed: list[str] = []
        reused: list[str] = []
        node_fingerprints: dict[str, str] = {}
        subproblems = {item.subproblem_id: item for item in model_ir.subproblems}
        source_files = model_source_snapshots(work_dir, model_ir)
        read_files: dict[str, str | None] = {}
        failures_by_subproblem: dict[str, OperatorExecutionFailure] = {}
        failures_by_node: dict[str, OperatorExecutionFailure] = {}
        failed_node_ids: set[str] = set()

        for node in execution_plan.graph.nodes:
            failed_dependencies = [
                dep for dep in node.dependencies if dep in failed_node_ids
            ]
            if node.subproblem_id in failures_by_subproblem or failed_dependencies:
                failed_node_ids.add(node.node_id)
                upstream_failure = failures_by_subproblem.get(node.subproblem_id)
                if upstream_failure is None:
                    upstream_failure = failures_by_node[failed_dependencies[0]]
                failures_by_subproblem[node.subproblem_id] = upstream_failure
                failures_by_node[node.node_id] = upstream_failure
                continue
            try:
                if execution_plan.operator_contract_sha256 != self.registry.contract_sha256():
                    raise ValueError("operator contract digest mismatch; recompile the Model IR")
                registered = self.registry.runtime(node.operator_id)
                if registered is None or registered.executor is None or registered.validator is None:
                    raise RuntimeError(f"operator runtime is not registered: {node.operator_id}")
                definition = registered.definition
                if definition.version != node.operator_version:
                    raise RuntimeError(f"operator version mismatch for node {node.node_id}")
                if definition.validator_id != node.validator_id:
                    raise RuntimeError(f"operator validator mismatch for node {node.node_id}")
                parameters = definition.validate_call(
                    inputs=node.inputs, parameters=node.parameters, outputs=node.outputs,
                )
                if not definition.deterministic and node.random_seed is None:
                    raise ValueError("nondeterministic operator requires an explicit random seed")
                resolved_inputs = {
                    name: _resolve_input(reference, values)
                    for name, reference in node.inputs.items()
                }
                input_files = file_snapshots(
                    work_dir, [resolved_inputs[name] for name in definition.file_inputs],
                )
                read_files.update(input_files)
                context = {
                    "input_files": input_files,
                    "operator_contract_sha256": execution_plan.operator_contract_sha256,
                    "node": node.model_dump(mode="json"),
                    "registration": definition.model_dump(mode="json"), "inputs": resolved_inputs,
                    "dependencies": {dep: node_fingerprints[dep] for dep in node.dependencies},
                    "source_files": {item.source_ref: source_files[item.source_ref]
                                     for item in subproblems[node.subproblem_id].data},
                }
                checkpoint = RecoveryCheckpoint(work_dir, "operators", node.node_id)
                raw_outputs = checkpoint.load(context)
                if raw_outputs is not None:
                    try:
                        registered.validate_outputs(raw_outputs)
                    except ValueError:
                        raw_outputs = None
                was_reused = raw_outputs is not None
                if raw_outputs is None:
                    checkpoint.discard()
                    raw_outputs = registered.invoke(
                        inputs=resolved_inputs,
                        parameters=parameters,
                        random_seed=node.random_seed,
                        work_dir=work_dir,
                    )
                registered.validate_outputs(raw_outputs)
                output_ports = list(definition.outputs)
                checkpoint.save(context, raw_outputs, files=generated_figure_paths(raw_outputs))
                node_fingerprints[node.node_id] = canonical_digest({"context": context, "outputs": raw_outputs})
                if was_reused:
                    reused.append(node.node_id)
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
                failures_by_subproblem.setdefault(node.subproblem_id, failure)
                failures_by_node[node.node_id] = failure
                failed_node_ids.add(node.node_id)

        first_failure = next(iter(failures_by_subproblem.values()), None)
        parameter_registry = _parameter_registry(
            model_ir,
            values,
            artifact_nodes,
            failed_subproblem_ids=set(failures_by_subproblem),
        )
        result_registry = _result_registry(
            model_ir,
            values,
            artifact_nodes,
            failures_by_subproblem=failures_by_subproblem,
        )
        has_verified = bool(result_registry["verified_results"])
        publishable_node_ids = {
            node.node_id for node in execution_plan.graph.nodes
            if node.subproblem_id not in failures_by_subproblem
        }
        figures = [
            value
            for artifact, value in values.items()
            if artifact_nodes.get(artifact) in publishable_node_ids
            and isinstance(value, dict)
            and {"path", "role", "linked_result_ids"} <= set(value)
        ]
        figure_registry = {
            "figures": figures,
            "summary": {"total_count": len(figures)},
        }
        _write_json(work_dir / "parameter_registry.json", parameter_registry)
        _write_json(work_dir / "result_registry.json", result_registry)
        _write_json(work_dir / "figure_registry.json", figure_registry)
        if first_failure is not None and not has_verified:
            _write_failed_notebook(work_dir / "notebook.ipynb", execution_plan, first_failure)
        else:
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
        status: Literal["passed", "partial", "failed"] = (
            "passed"
            if first_failure is None
            else ("partial" if has_verified else "failed")
        )
        manifest = OperatorExecutionManifest(
            model_id=model_ir.model_id,
            status=status,
            execution_plan_sha256=hashlib.sha256(plan_payload.encode("utf-8")).hexdigest(),
            model_sha256=canonical_digest(model_ir),
            source_files_sha256=source_files,
            input_files_sha256=read_files,
            output_files_sha256=file_snapshots(work_dir, [
                name for name in generated_files if name not in {"execution_manifest.json", "execution_plan.json"}
            ] + [figure["path"] for figure in figures]),
            executed_node_ids=list(executed),
            reused_node_ids=reused,
            generated_files=generated_files,
            failure=first_failure,
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
            model_sha256=canonical_digest(model_ir),
            source_files_sha256=model_source_snapshots(work_dir, model_ir),
            input_files_sha256={},
            output_files_sha256=file_snapshots(work_dir, [name for name in generated_files if name != "execution_manifest.json"]),
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
    *,
    failed_subproblem_ids: set[str] | None = None,
) -> dict:
    blocked_subproblems = failed_subproblem_ids or set()
    parameters: list[dict] = []
    for subproblem in model_ir.subproblems:
        if (
            not isinstance(subproblem, SolvableModelIR)
            or subproblem.subproblem_id in blocked_subproblems
        ):
            continue
        for component in subproblem.execution_components:
            if not isinstance(component, OperatorGraphExecutionComponent):
                continue
            for variable in component.variables:
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
    *,
    failures_by_subproblem: dict[str, OperatorExecutionFailure] | None = None,
) -> dict:
    runtime_failures = failures_by_subproblem or {}
    verified: list[dict] = []
    blocked: list[dict] = []
    claims: list[dict] = []
    variables = {
        variable.id: variable
        for subproblem in model_ir.subproblems
        if isinstance(subproblem, SolvableModelIR)
        for component in subproblem.execution_components
        if isinstance(component, OperatorGraphExecutionComponent)
        for variable in component.variables
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
        runtime_failure = runtime_failures.get(subproblem.subproblem_id)
        if runtime_failure is not None:
            if subproblem.final_answers:
                for answer in subproblem.final_answers:
                    blocked.append({
                        "id": answer.id,
                        "subproblem_id": subproblem.subproblem_id,
                        "reason": f"{runtime_failure.error_type}: {runtime_failure.message}",
                        "failed_node_id": runtime_failure.node_id,
                        "failed_operator_id": runtime_failure.operator_id,
                        "recovery_action": runtime_failure.recovery_action,
                    })
            else:
                blocked.append({
                    "id": f"execution_failure_{runtime_failure.node_id}",
                    "subproblem_id": subproblem.subproblem_id,
                    "reason": f"{runtime_failure.error_type}: {runtime_failure.message}",
                    "failed_node_id": runtime_failure.node_id,
                    "failed_operator_id": runtime_failure.operator_id,
                    "recovery_action": runtime_failure.recovery_action,
                })
            continue
        assumption_statements = [
            assumption.statement for assumption in subproblem.assumptions
        ]
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
            claims.append(ClaimRecord(
                id=answer.id,
                status=answer.status,
                claim_text=answer.item,
                evidence_ids=[answer.id],
                assumptions=assumption_statements,
                risk=(
                    "Conditional result: valid only under the registered modeling "
                    "assumptions and their alternative-scenario checks."
                    if assumption_statements
                    else "No additional modeling assumptions are registered."
                ),
            ).model_dump(mode="json"))
    covered_subproblems = {
        str(item.get("subproblem_id") or "")
        for item in blocked
        if item.get("subproblem_id")
    }
    for subproblem_id, failure in runtime_failures.items():
        if subproblem_id in covered_subproblems:
            continue
        blocked.append({
            "id": f"execution_failure_{failure.node_id}",
            "subproblem_id": subproblem_id,
            "reason": f"{failure.error_type}: {failure.message}",
            "failed_node_id": failure.node_id,
            "failed_operator_id": failure.operator_id,
            "recovery_action": failure.recovery_action,
        })
    return {
        "verified_results": verified,
        "blocked_results": blocked,
        "summary": {
            "coverage_status": "complete" if not blocked else ("partial" if verified else "blocked"),
            "verified_count": len(verified),
            "blocked_count": len(blocked),
            **({"claim_registry": claims} if (claims or not runtime_failures) else {}),
        },
    }


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
