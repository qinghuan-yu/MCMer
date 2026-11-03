import json
from copy import deepcopy
from pathlib import Path

from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_compiler import ExecutionPlan
from app.guidance.operator_execution import OperatorGraphExecutor
from app.guidance.operators import OperatorDefinition, OperatorPort, OperatorRegistry


def test_failed_subproblem_does_not_publish_orphan_figures(tmp_path):
    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    failed = deepcopy(payload["subproblems"][0])
    failed["subproblem_id"] = "failed_question"
    failed["final_answers"][0].update(id="failed_answer", value_ref="failed_solution")
    payload["subproblems"].insert(0, failed)
    model = ModelIR.model_validate(payload)
    registry = OperatorRegistry()

    def figure(**kwargs):
        (kwargs["work_dir"] / "failed.png").write_bytes(b"probe")
        return {"out": {"path": "failed.png", "role": "supporting", "linked_result_ids": ["failed_answer"]}}

    def fail(**kwargs):
        raise ValueError("controlled failure after figure generation")

    for name, executor in [
        ("figure", figure), ("fail", fail), ("success", lambda **kwargs: {"out": {"value": 42}}),
    ]:
        registry.register(OperatorDefinition(
            operator_id=name, version="1", inputs={},
            outputs={"out": OperatorPort(value_kind="artifact", unit="unspecified")},
            deterministic=True, validator_id=name + ".validator",
        ), executor=executor, validator=lambda **kwargs: [])
    nodes = [
        ("figure", "failed_question", "figure", "failed_figure"),
        ("fail", "failed_question", "fail", "failed_solution"),
        ("solve", payload["subproblems"][1]["subproblem_id"], "success", "solution"),
    ]
    plan = ExecutionPlan.model_validate({"model_id": model.model_id, "graph": {"nodes": [{
        "node_id": node, "subproblem_id": question, "operator_id": operator,
        "operator_version": "1", "ir_source": "probe", "inputs": {}, "outputs": [output],
        "parameters": {}, "validator_id": operator + ".validator", "dependencies": [],
    } for node, question, operator, output in nodes]}})

    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=plan, model_ir=model, work_dir=tmp_path,
    )
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    figures = json.loads((tmp_path / "figure_registry.json").read_text(encoding="utf-8"))
    assert manifest.status == "partial"
    assert len(results["verified_results"]) == 1
    assert figures["figures"] == []


def test_failure_propagates_through_skipped_nodes_to_downstream_subproblem(tmp_path):
    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    failed = deepcopy(payload["subproblems"][0])
    failed["subproblem_id"] = "upstream"
    failed["final_answers"][0].update(id="upstream_answer", value_ref="upstream_solution")
    payload["subproblems"].insert(0, failed)
    model = ModelIR.model_validate(payload)
    registry = OperatorRegistry()
    calls = []

    def runtime(*, parameters, **kwargs):
        if parameters.get("fail"):
            raise ValueError("controlled upstream failure")
        calls.append("downstream")
        return {"out": {"value": 42}}

    registry.register(OperatorDefinition(
        operator_id="probe", version="1", inputs={},
        outputs={"out": OperatorPort(value_kind="artifact", unit="unspecified")},
        deterministic=True, validator_id="probe.validator",
    ), executor=runtime, validator=lambda **kwargs: [])
    downstream = payload["subproblems"][1]["subproblem_id"]
    nodes = [
        ("fail", "upstream", [], {"fail": True}, "upstream_solution"),
        ("skipped", "upstream", ["fail"], {}, "intermediate"),
        ("dependent", downstream, ["skipped"], {}, "dependent_value"),
        ("solve", downstream, [], {}, "solution"),
    ]
    plan = ExecutionPlan.model_validate({"model_id": model.model_id, "graph": {"nodes": [{
        "node_id": node, "subproblem_id": question, "operator_id": "probe",
        "operator_version": "1", "ir_source": "probe", "inputs": {}, "outputs": [output],
        "parameters": parameters, "validator_id": "probe.validator", "dependencies": dependencies,
    } for node, question, dependencies, parameters, output in nodes]}})
    OperatorGraphExecutor(registry=registry).execute(
        execution_plan=plan, model_ir=model, work_dir=tmp_path,
    )
    assert calls == []
