import asyncio
import json

import pytest


def _model_ir(*, random_seed=7, identifiability_checks=None):
    from app.guidance.model_ir import ModelIR

    return ModelIR.model_validate({
        "schema_version": "1.0",
        "model_id": "typed_operator_contract",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "problem1",
            "objective": "Estimate one dimensionless coefficient.",
            "variables": [
                {
                    "id": "x",
                    "role": "observed",
                    "symbol": "x",
                    "unit": "m",
                    "shape": ["sample"],
                },
                {
                    "id": "y",
                    "role": "observed",
                    "symbol": "y",
                    "unit": "s",
                    "shape": ["sample"],
                },
                {
                    "id": "theta",
                    "role": "unknown_parameter",
                    "symbol": "theta",
                    "unit": "dimensionless",
                    "shape": [],
                },
            ],
            "data": [{
                "id": "observations",
                "source_ref": "data/observations.csv",
                "fields": {"x": "distance", "y": "duration"},
            }],
            "equations": [{
                "id": "identity_check",
                "left": {"kind": "symbol", "symbol": "y"},
                "right": {"kind": "symbol", "symbol": "y"},
            }],
            "operators": [{
                "node_id": "fit",
                "operator_id": "statistics.typed_fit",
                "inputs": {"x": "observations.x", "y": "observations.y"},
                "outputs": ["theta"],
                "random_seed": random_seed,
            }],
            "candidate_models": [
                {
                    "id": "constant_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["fit"],
                    "selection_metric": "rmse",
                    "applicability_boundary": "Observed rows under a constant response.",
                },
                {
                    "id": "typed_fit",
                    "role": "recommended",
                    "operator_node_ids": ["fit"],
                    "selection_metric": "rmse",
                    "applicability_boundary": "Observed rows under the typed fit assumptions.",
                },
            ],
            "validation": {
                "model_families": ["regression"],
                "identifiability_checks": identifiability_checks or ["full_column_rank"],
                "evidence_checks": ["residual_recompute"],
                "robustness_checks": ["seed_replay"],
            },
            "final_answers": [{
                "id": "theta_answer",
                "item": "Estimated coefficient",
                "value_ref": "theta",
                "status": "evidence_verified",
                "evidence_ids": ["theta"],
            }],
        }],
    })


def _registry(
    *,
    x_unit="m",
    output_unit="dimensionless",
    preconditions=None,
    deterministic=False,
    executor=None,
    validator=None,
):
    from app.guidance.operators import OperatorDefinition, OperatorPort, OperatorRegistry

    registry = OperatorRegistry()
    registry.register(
        OperatorDefinition(
            operator_id="statistics.typed_fit",
            version="1.0.0",
            inputs={
                "x": OperatorPort(value_kind="vector", unit=x_unit),
                "y": OperatorPort(value_kind="vector", unit="s"),
            },
            outputs={
                "estimate": OperatorPort(value_kind="scalar", unit=output_unit),
            },
            preconditions=preconditions or ["full_column_rank"],
            deterministic=deterministic,
            validator_id="statistics.typed_fit.verify",
        ),
        executor=executor,
        validator=validator,
    )
    return registry


def test_compiler_keeps_solvable_graph_when_another_subproblem_is_blocked() -> None:
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler

    payload = _model_ir().model_dump(mode="json")
    payload["subproblems"].append({
        "execution_status": "blocked",
        "subproblem_id": "problem2",
        "objective": "Compute an output requiring a missing appendix.",
        "blocking_reason": "The required appendix is absent.",
        "missing_inputs": ["appendix parameters"],
        "recovery_action": "Provide the appendix and recompile the Model IR.",
        "expected_outputs": ["problem2 answer"],
    })

    result = ModelIRCompiler(registry=_registry()).compile(ModelIR.model_validate(payload))

    assert result.status == "compiled"
    assert result.execution_plan is not None
    assert [item.subproblem_id for item in result.blocked_subproblems] == ["problem2"]


def test_compiler_orders_direct_artifact_references_after_their_producer() -> None:
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_catalog import build_default_operator_registry

    model_ir = ModelIR.model_validate({
        "model_id": "direct_artifact_dependency",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "problem1",
            "objective": "Extract a value from a derived design artifact.",
            "variables": [{
                "id": "theta",
                "role": "unknown_parameter",
                "symbol": "theta",
                "unit": "unspecified",
                "shape": [],
            }],
            "data": [{
                "id": "declared_input",
                "source_ref": "data/input.csv",
                "fields": {"theta": "theta"},
            }],
            "equations": [{
                "id": "identity",
                "left": {"kind": "symbol", "symbol": "theta"},
                "right": {"kind": "symbol", "symbol": "theta"},
            }],
            "operators": [
                {
                    "node_id": "z_source",
                    "operator_id": "design.periodic_key_moments",
                    "inputs": {},
                    "outputs": ["design"],
                    "parameters": {
                        "fixed_moments": [0],
                        "periodic_starts": [],
                        "period": 1,
                        "window_end_offset": 0,
                        "sample_count": 1,
                    },
                },
                {
                    "node_id": "a_extract",
                    "operator_id": "artifact.extract_scalar",
                    "inputs": {"artifact": "design"},
                    "outputs": ["theta"],
                    "parameters": {"path": "monitoring_count"},
                },
            ],
            "candidate_models": [
                {
                    "id": "declared_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["z_source"],
                    "selection_metric": "monitoring_count",
                    "applicability_boundary": "Declared source design without derived extraction.",
                },
                {
                    "id": "derived_design",
                    "role": "recommended",
                    "operator_node_ids": ["z_source", "a_extract"],
                    "selection_metric": "monitoring_count",
                    "applicability_boundary": "Declared source design and scalar extraction path.",
                },
            ],
            "validation": {
                "model_families": ["optimization"],
                "identifiability_checks": ["declared_structure"],
                "evidence_checks": ["artifact_trace"],
                "robustness_checks": ["boundary_check"],
            },
            "final_answers": [{
                "id": "theta_answer",
                "item": "Derived monitoring count",
                "value_ref": "theta",
                "status": "evidence_verified",
                "evidence_ids": ["design"],
            }],
        }],
    })

    result = ModelIRCompiler(registry=build_default_operator_registry()).compile(model_ir)

    assert result.status == "compiled"
    assert result.execution_plan is not None
    assert [node.node_id for node in result.execution_plan.graph.nodes] == [
        "z_source",
        "a_extract",
    ]
    assert result.execution_plan.graph.nodes[1].dependencies == ["z_source"]


@pytest.mark.parametrize(
    ("registry", "expected_code"),
    [
        (lambda: _registry(x_unit="kg"), "input_type_mismatch"),
        (lambda: _registry(output_unit="s"), "output_type_mismatch"),
    ],
)
def test_compiler_rejects_operator_input_or_output_type_mismatch(
    registry,
    expected_code,
) -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler

    result = ModelIRCompiler(registry=registry()).compile(_model_ir())

    assert result.status == "blocked"
    assert result.execution_plan is None
    diagnostic = next(item for item in result.diagnostics if item.code == expected_code)
    assert diagnostic.subproblem_id == "problem1"
    assert diagnostic.node_id == "fit"


def test_unit_conflict_writes_blocked_registry_with_recovery_action(tmp_path) -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_execution import OperatorGraphExecutor

    registry = _registry(x_unit="kg")
    model_ir = _model_ir()
    compilation = ModelIRCompiler(registry=registry).compile(model_ir)
    assert compilation.status == "blocked"

    manifest = OperatorGraphExecutor(registry=registry).write_blocked(
        compilation_result=compilation,
        model_ir=model_ir,
        work_dir=tmp_path,
    )

    assert manifest.status == "blocked"
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    conflict = next(
        item for item in results["blocked_results"]
        if item["code"] == "input_type_mismatch"
    )
    assert conflict["node_id"] == "fit"
    assert conflict["recovery_action"]
    assert "unit" in conflict["recovery_action"].lower()


def test_compiler_rejects_unsatisfied_operator_precondition() -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler

    result = ModelIRCompiler(registry=_registry()).compile(
        _model_ir(identifiability_checks=["residual_recompute"])
    )

    assert result.status == "blocked"
    assert result.execution_plan is None
    diagnostic = next(item for item in result.diagnostics if item.code == "precondition_unsatisfied")
    assert diagnostic.node_id == "fit"
    assert "full_column_rank" in diagnostic.message


def test_compiler_rejects_nondeterministic_operator_without_seed() -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler

    result = ModelIRCompiler(registry=_registry()).compile(_model_ir(random_seed=None))

    assert result.status == "blocked"
    assert result.execution_plan is None
    diagnostic = next(item for item in result.diagnostics if item.code == "random_seed_required")
    assert diagnostic.node_id == "fit"


def test_compiler_rejects_operator_dependency_cycle_before_execution() -> None:
    from app.guidance.model_ir import OperatorInvocation
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operators import OperatorDefinition, OperatorPort

    model_ir = _model_ir()
    subproblem = model_ir.subproblems[0]
    subproblem.operators[0].inputs["x"] = "post.value"
    subproblem.operators.append(OperatorInvocation(
        node_id="post",
        operator_id="transform.scalar_to_vector",
        inputs={"value": "fit.theta"},
        outputs=["x"],
    ))
    registry = _registry()
    registry.register(OperatorDefinition(
        operator_id="transform.scalar_to_vector",
        version="1.0.0",
        inputs={
            "value": OperatorPort(value_kind="scalar", unit="dimensionless"),
        },
        outputs={
            "value": OperatorPort(value_kind="vector", unit="m"),
        },
        preconditions=[],
        deterministic=True,
        validator_id="transform.scalar_to_vector.verify",
    ))

    result = ModelIRCompiler(registry=registry).compile(model_ir)

    assert result.status == "blocked"
    assert result.execution_plan is None
    diagnostic = next(item for item in result.diagnostics if item.code == "operator_cycle")
    assert diagnostic.subproblem_id == "problem1"
    assert "fit" in diagnostic.message and "post" in diagnostic.message


def test_compiler_rejects_data_reference_that_escapes_workspace() -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler

    model_ir = _model_ir()
    model_ir.subproblems[0].data[0].source_ref = "../secret.csv"

    result = ModelIRCompiler(registry=_registry()).compile(model_ir)

    assert result.status == "blocked"
    assert result.execution_plan is None
    diagnostic = next(item for item in result.diagnostics if item.code == "unsafe_data_reference")
    assert diagnostic.subproblem_id == "problem1"
    assert "../secret.csv" in diagnostic.message


def test_compiler_rejects_operator_output_not_declared_by_model_ir() -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler

    model_ir = _model_ir()
    model_ir.subproblems[0].operators[0].outputs = ["scratch_artifact"]

    result = ModelIRCompiler(registry=_registry()).compile(model_ir)

    assert result.status == "blocked"
    assert result.execution_plan is None
    diagnostic = next(item for item in result.diagnostics if item.code == "undeclared_artifact")
    assert diagnostic.node_id == "fit"
    assert "scratch_artifact" in diagnostic.message


def test_compiler_builds_topological_execution_graph_for_valid_model_ir() -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler

    result = ModelIRCompiler(registry=_registry()).compile(_model_ir())

    assert result.status == "compiled"
    assert result.diagnostics == []
    assert result.execution_plan is not None
    graph = result.execution_plan.graph
    assert [node.node_id for node in graph.nodes] == ["fit"]
    assert graph.nodes[0].operator_version == "1.0.0"
    assert graph.nodes[0].validator_id == "statistics.typed_fit.verify"


def test_compiler_writes_traceable_execution_plan_json(tmp_path) -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler

    output_path = tmp_path / "execution_plan.json"
    result = ModelIRCompiler(registry=_registry()).compile_to_file(
        _model_ir(),
        output_path=output_path,
        model_ir_ref="approved_model_ir.json",
    )

    assert result.status == "compiled"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    node = payload["graph"]["nodes"][0]
    assert node["ir_source"] == (
        "approved_model_ir.json#subproblems[problem1].operators[fit]"
    )
    assert node["inputs"] == {"x": "observations.x", "y": "observations.y"}
    assert node["outputs"] == ["theta"]
    assert node["operator_version"] == "1.0.0"
    assert node["validator_id"] == "statistics.typed_fit.verify"


def test_graph_executor_calls_only_registered_operator_and_writes_artifacts(tmp_path) -> None:
    import nbformat

    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_execution import OperatorGraphExecutor

    calls = []

    def execute_fit(*, inputs, parameters, random_seed, work_dir):
        calls.append({
            "inputs": inputs,
            "parameters": parameters,
            "random_seed": random_seed,
            "work_dir": work_dir,
        })
        return {"estimate": 2.5}

    def validate_fit(*, outputs):
        return [] if outputs["estimate"] == 2.5 else ["unexpected estimate"]

    registry = _registry(executor=execute_fit, validator=validate_fit)
    model_ir = _model_ir()
    compilation = ModelIRCompiler(registry=registry).compile_to_file(
        model_ir,
        output_path=tmp_path / "execution_plan.json",
    )

    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=tmp_path,
    )

    assert len(calls) == 1
    assert calls[0]["random_seed"] == 7
    assert manifest.status == "passed"
    assert manifest.executed_node_ids == ["fit"]
    assert not (tmp_path / "solve.py").exists()
    for artifact in (
        "execution_manifest.json",
        "notebook.ipynb",
        "parameter_registry.json",
        "result_registry.json",
        "figure_registry.json",
    ):
        assert (tmp_path / artifact).is_file()
    notebook = nbformat.read(tmp_path / "notebook.ipynb", as_version=4)
    assert notebook.metadata["execution_plan"] == "execution_plan.json"
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    assert results["verified_results"][0]["id"] == "theta_answer"
    assert results["verified_results"][0]["value"] == 2.5
    assert results["verified_results"][0]["subproblem_id"] == "problem1"


def test_graph_executor_records_operator_exception_as_transactional_failure(tmp_path) -> None:
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_execution import OperatorGraphExecutor

    def fail_fit(*, inputs, parameters, random_seed, work_dir):
        raise RuntimeError("synthetic operator failure")

    registry = _registry(executor=fail_fit, validator=lambda *, outputs: [])
    model_ir = _model_ir()
    compilation = ModelIRCompiler(registry=registry).compile_to_file(
        model_ir,
        output_path=tmp_path / "execution_plan.json",
    )

    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=tmp_path,
    )

    assert manifest.status == "failed"
    assert manifest.executed_node_ids == []
    assert manifest.failure is not None
    assert manifest.failure.node_id == "fit"
    assert manifest.failure.operator_id == "statistics.typed_fit"
    assert manifest.failure.error_type == "RuntimeError"
    assert "synthetic operator failure" in manifest.failure.message
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    assert results["verified_results"] == []
    assert results["summary"]["coverage_status"] == "blocked"
    assert results["blocked_results"][0]["failed_node_id"] == "fit"
    assert results["blocked_results"][0]["recovery_action"]
    assert json.loads(
        (tmp_path / "execution_manifest.json").read_text(encoding="utf-8")
    )["status"] == "failed"


def test_graph_executor_records_missing_attachment_without_losing_failure_artifacts(
    tmp_path,
) -> None:
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_catalog import build_default_operator_registry
    from app.guidance.operator_execution import OperatorGraphExecutor

    model_ir = ModelIR.model_validate({
        "schema_version": "1.0",
        "model_id": "missing_attachment_failure",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "problem1",
            "objective": "Read a required attachment before computing the result.",
            "variables": [{
                "id": "table_value",
                "role": "derived",
                "symbol": "D",
                "unit": "unspecified",
                "shape": ["row", "column", "table"],
            }],
            "data": [{
                "id": "missing_source",
                "source_ref": "data/does-not-exist.xlsx",
                "fields": {"observations": "Sheet1"},
            }],
            "equations": [{
                "id": "identity",
                "left": {"kind": "literal", "value": 0.0},
                "right": {"kind": "literal", "value": 0.0},
            }],
            "constraints": [],
            "operators": [{
                "node_id": "read_attachment",
                "operator_id": "data.read_excel_table",
                "inputs": {"source": "data/does-not-exist.xlsx"},
                "outputs": ["table_value"],
                "parameters": {"sheet": 0},
            }],
            "candidate_models": [
                {
                    "id": "declared_input_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["read_attachment"],
                    "selection_metric": "input_availability",
                    "applicability_boundary": "Only when the declared attachment exists.",
                },
                {
                    "id": "attachment_reader",
                    "role": "recommended",
                    "operator_node_ids": ["read_attachment"],
                    "selection_metric": "input_availability",
                    "applicability_boundary": "Only when the declared attachment exists.",
                },
            ],
            "validation": {
                "model_families": ["regression"],
                "identifiability_checks": ["source_exists"],
                "evidence_checks": ["source_trace"],
                "robustness_checks": ["missing_input"],
            },
            "final_answers": [{
                "id": "attachment_answer",
                "item": "Attachment-backed result",
                "value_ref": "table_value",
                "status": "evidence_verified",
                "evidence_ids": ["read_attachment"],
            }],
        }],
    })
    registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=registry).compile_to_file(
        model_ir,
        output_path=tmp_path / "execution_plan.json",
    )
    assert compilation.status == "compiled"

    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=tmp_path,
    )

    assert manifest.status == "failed"
    assert manifest.failure is not None
    assert manifest.failure.node_id == "read_attachment"
    assert manifest.failure.error_type == "FileNotFoundError"
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    blocked = results["blocked_results"][0]
    assert blocked["id"] == "attachment_answer"
    assert "does-not-exist.xlsx" in blocked["reason"]
    assert blocked["recovery_action"]
    for artifact in manifest.generated_files:
        assert (tmp_path / artifact).is_file()


def test_calculation_stage_blocks_missing_operator_without_program_generation(tmp_path) -> None:
    from app.guidance.model_ir import ApprovedModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_execution import OperatorGraphExecutor
    from app.guidance.operators import OperatorRegistry
    from app.guidance.stages import CalculationStage

    model_ir = _model_ir()
    approved_path = tmp_path / "approved_model_ir.json"
    approved_path.write_text(
        ApprovedModelIR(
            review_status="approved",
            model_id=model_ir.model_id,
            approved_model_ir=model_ir,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    registry = OperatorRegistry()
    output_path = asyncio.run(CalculationStage(
        compiler=ModelIRCompiler(registry=registry),
        executor=OperatorGraphExecutor(registry=registry),
    ).run(
        task_id="missing-operator",
        task={"work_dir": str(tmp_path)},
        input_path=approved_path,
        output_path=tmp_path / "execution_manifest.json",
    ))

    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    compilation = json.loads(
        (tmp_path / "compilation_result.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "blocked"
    assert compilation["diagnostics"][0]["code"] == "missing_operator"
    assert not (tmp_path / "execution_plan.json").exists()
    assert not (tmp_path / "solve.py").exists()
