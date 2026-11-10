from app.guidance.operator_parameters import OperatorParameters
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.guidance.contract_request import StructuredContractRequester
from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.operator_execution import OperatorGraphExecutor
from app.guidance.operators import OperatorDefinition, OperatorPort, OperatorRegistry


class NumberContract(BaseModel):
    value: int


class CountingLLM:
    model = "test-model"
    max_tokens = 100
    temperature = 0

    def __init__(self):
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs["history"][1]["content"])
        payload = payload.get("original_input", payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"value": payload["value"]}),
        ))])


def _request(root, llm, payload, *, validator=None, prompt="Return a number."):
    return asyncio.run(StructuredContractRequester(
        llm=llm, model_type=NumberContract, system_prompt=prompt,
        agent_name="RecoveryNumber", artifact_dir=root, semantic_validator=validator,
    ).request(payload))


def test_contract_recovery_reuses_valid_output_across_requester_instances(tmp_path):
    llm = CountingLLM()
    assert _request(tmp_path, llm, {"value": 7}).value == 7
    assert _request(tmp_path, llm, {"value": 7}).value == 7
    assert llm.calls == 1


@pytest.mark.parametrize("change", ["payload", "prompt", "model"])
def test_contract_recovery_invalidates_changed_generation_inputs(tmp_path, change):
    llm = CountingLLM()
    _request(tmp_path, llm, {"value": 7})
    if change == "model":
        llm.model = "another-model"
    _request(tmp_path, llm, {"value": 8 if change == "payload" else 7},
             prompt="New prompt." if change == "prompt" else "Return a number.")
    assert llm.calls == 2


def test_recovered_contract_is_revalidated_and_rejection_discards_checkpoint(tmp_path):
    from app.guidance.contract_request import ContractGenerationError

    llm = CountingLLM()
    _request(tmp_path, llm, {"value": 7})

    def reject(contract):
        raise ValueError("new semantic requirement rejects this value")

    with pytest.raises(ContractGenerationError):
        _request(tmp_path, llm, {"value": 7}, validator=reject)
    assert llm.calls == 3
    _request(tmp_path, llm, {"value": 7})
    assert llm.calls == 4


def test_contract_checkpoint_cannot_hide_changed_nonprompt_dependencies(tmp_path):
    llm = CountingLLM()
    for value in [7, 7, 8]:
        asyncio.run(StructuredContractRequester(
            llm=llm, model_type=NumberContract, system_prompt="Return a number.",
            agent_name="DependencyNumber", artifact_dir=tmp_path,
            checkpoint_context={"materialized_parameter": value},
        ).request({"value": 7}))
    assert llm.calls == 2


def _scalar_model(value=2):
    subproblems = []
    for sid, nodes in [("chain", [("source", {}, value), ("dependent", {"value": "source_value"}, 0)]),
                       ("independent", [("other", {}, 9)])]:
        subproblems.append({
            "execution_status": "solvable", "subproblem_id": sid, "objective": "Calculate a scalar.",
            "assumptions": [], "fact_bindings": [],
            "data": [{"id": f"{sid}_input", "source_ref": f"inputs/{sid}.txt", "fields": {"value": "constant"}}],
            "equations": [],
            "execution_components": [{
                "kind": "operator_graph", "component_id": f"{sid}_graph",
                "variables": [{"id": f"{name}_value", "role": "derived", "symbol": name,
                               "unit": "dimensionless", "shape": []} for name, _, _ in nodes],
                "operators": [{"node_id": name, "operator_id": "test.add" if inputs else "test.source",
                               "inputs": inputs, "outputs": [f"{name}_value"],
                               "parameters": {"name": name, "value": number}}
                              for name, inputs, number in nodes],
            }],
            "candidate_models": [{"id": f"{sid}_{role}", "role": role,
                                  "operator_node_ids": [name for name, _, _ in nodes],
                                  "selection_metric": "value", "applicability_boundary": "Finite scalar."}
                                 for role in ["baseline", "recommended"]],
            "validation": {"model_families": ["regression"], "identifiability_checks": ["finite"],
                           "evidence_checks": ["recompute"], "robustness_checks": ["repeat"]},
            "final_answers": [{"id": f"{sid}_answer", "item": "Scalar result",
                               "value_ref": f"{nodes[-1][0]}_value", "status": "evidence_verified",
                               "evidence_ids": [nodes[-1][0]]}],
        })
    return ModelIR.model_validate({"model_id": "recoverable", "subproblems": subproblems})


def _registry(calls, fail):
    class ProbeParameters(OperatorParameters):
        name: str
        value: int

    registry = OperatorRegistry()

    def execute(*, inputs, parameters, random_seed, work_dir):
        name = parameters["name"]
        calls.append(name)
        if name in fail:
            raise RuntimeError("temporary operator failure")
        return {"value": inputs.get("value", parameters["value"]) + (1 if inputs else 0)}

    for name, inputs in [("test.source", {}), ("test.add", {"value": OperatorPort(value_kind="scalar", unit="dimensionless")})]:
        registry.register(OperatorDefinition(parameter_model=ProbeParameters,
            operator_id=name, version="1", inputs=inputs,
            outputs={"value": OperatorPort(value_kind="scalar", unit="dimensionless")},
            deterministic=True, validator_id=f"{name}.validate",
        ), executor=execute, validator=lambda *, outputs: [])
    return registry


def _execute(root, model, registry):
    compilation = ModelIRCompiler(registry=registry).compile_to_file(model, output_path=root / "execution_plan.json")
    assert compilation.status == "compiled", compilation.diagnostics
    return OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan, model_ir=model, work_dir=root,
    )


def test_execution_retry_preserves_independent_and_upstream_success(tmp_path):
    calls, failures = [], {"dependent"}
    registry = _registry(calls, failures)
    model = _scalar_model()
    assert _execute(tmp_path, model, registry).status == "partial"
    assert calls == ["source", "dependent", "other"]
    failures.clear()
    calls.clear()
    assert _execute(tmp_path, model, registry).status == "passed"
    assert calls == ["dependent"]
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    assert [item["value"] for item in results["verified_results"]] == [3, 9]


def test_changed_upstream_rebuilds_dependency_but_preserves_independent_result(tmp_path):
    calls = []
    registry = _registry(calls, set())
    _execute(tmp_path, _scalar_model(), registry)
    calls.clear()
    _execute(tmp_path, _scalar_model(value=4), registry)
    assert calls == ["source", "dependent"]
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    assert [item["value"] for item in results["verified_results"]] == [5, 9]


def test_new_merged_model_identity_does_not_discard_unchanged_operator_results(tmp_path):
    calls = []
    registry = _registry(calls, set())
    _execute(tmp_path, _scalar_model(), registry)
    calls.clear()
    _execute(tmp_path, _scalar_model().model_copy(update={"model_id": "new-merged-model"}), registry)
    assert calls == []


def test_blocked_recompilation_does_not_keep_previous_execution_plan(tmp_path):
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_review_contracts import ApprovedModelIR, ModelIRReview
    from app.guidance.model_review_validation import bind_model_review
    from app.guidance.stages import CalculationStage
    from source_preflight_fixtures import write_source_preflight

    model = _scalar_model()
    _execute(tmp_path, model, _registry([], set()))
    spec = ProblemSpec(task_summary="Recompile the same approved model.", subproblems=[{
        "id": item.subproblem_id, "required_fact_ids": [], "required_uncertainty_ids": [],
        "depends_on_subproblem_ids": [],
    } for item in model.subproblems])
    spec, permission = write_source_preflight(tmp_path, spec)
    review = ModelIRReview(model_id=model.model_id, subproblem_reviews=[{
        "subproblem_id": item.subproblem_id, "status": "approved",
    } for item in model.subproblems])
    (tmp_path / "approved_model_ir.json").write_text(ApprovedModelIR(
        review_status="approved", model_id=model.model_id,
        executable_subproblem_ids=[item.subproblem_id for item in model.subproblems], approved_model_ir=model,
        review_binding=bind_model_review(
            review=review, model_ir=model, approved_model_ir=model, problem_spec=spec,
            evidence_profile={"schema_version": "1.0", "evidence": []}, input_catalog=None,
            revision_request=None, source_preflight=permission,
        ),
    ).model_dump_json(), encoding="utf-8")
    registry = OperatorRegistry()
    asyncio.run(CalculationStage(compiler=ModelIRCompiler(registry=registry),
                                executor=OperatorGraphExecutor(registry=registry)).run(
        task_id="recompile", task={}, input_path=tmp_path / "approved_model_ir.json",
        output_path=tmp_path / "execution_manifest.json",
    ))
    assert not (tmp_path / "execution_plan.json").exists()
    from app.guidance.verification_stage import ResultVerificationStage

    asyncio.run(ResultVerificationStage().run(
        task_id="recompile", task={}, input_path=tmp_path / "execution_manifest.json",
        output_path=tmp_path / "verification_report.json",
    ))
    report = json.loads((tmp_path / "verification_report.json").read_text(encoding="utf-8"))
    assert report["execution_verified"]["status"] == "failed"
    assert report["status"] == "blocked"


def test_changed_source_file_invalidates_only_its_branch_even_when_output_is_same(tmp_path):
    calls = []
    registry = _registry(calls, set())
    (tmp_path / "inputs").mkdir()
    source = tmp_path / "inputs/chain.txt"
    source.write_text("first input", encoding="utf-8")
    _execute(tmp_path, _scalar_model(), registry)
    source.write_text("changed input", encoding="utf-8")
    calls.clear()
    manifest = _execute(tmp_path, _scalar_model(), registry)
    assert calls == ["source", "dependent"]
    assert manifest.reused_node_ids == ["other"]


def test_corrupt_checkpoint_is_recomputed_and_never_published(tmp_path):
    calls = []
    registry = _registry(calls, set())
    _execute(tmp_path, _scalar_model(), registry)
    for path in (tmp_path / ".recovery/operators").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["value"] == {"value": 2}:
            record["value"]["value"] = 999
            path.write_text(json.dumps(record), encoding="utf-8")
    calls.clear()
    _execute(tmp_path, _scalar_model(), registry)
    assert calls == ["source"]
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    assert [item["value"] for item in results["verified_results"]] == [3, 9]


def test_missing_generated_file_invalidates_checkpoint(tmp_path):
    from app.guidance.recovery import RecoveryCheckpoint

    artifact = tmp_path / "figure.svg"
    artifact.write_text("<svg/>", encoding="utf-8")
    checkpoint = RecoveryCheckpoint(tmp_path, "operators", "figure")
    checkpoint.save({"inputs": [1, 2]}, {"path": "figure.svg"}, files=["figure.svg"])
    assert checkpoint.load({"inputs": [1, 2]}) == {"path": "figure.svg"}
    artifact.unlink()
    assert checkpoint.load({"inputs": [1, 2]}) is None


@pytest.mark.parametrize("parent_fails", [False, True])
def test_model_generation_follows_dependency_order_and_propagates_failure(tmp_path, monkeypatch, parent_fails):
    from app.guidance.contracts import ProblemSpec
    from app.guidance.modeler import LLMModeler
    from test_guidance_contract_agents import _blocked_ir, _subproblem

    calls = []

    async def generate(**kwargs):
        target = kwargs["subproblem"]["id"]
        calls.append(target)
        if target == "parent" and parent_fails:
            raise RuntimeError("upstream contract failed")
        return ModelIR.model_validate(_blocked_ir(f"model-{target}", target))

    monkeypatch.setattr("app.guidance.modeler._request_single_subproblem_model", generate)
    spec = ProblemSpec(task_summary="Dependent generation.", subproblems=[
        _subproblem("child", depends_on_subproblem_ids=["parent"]),
        _subproblem("parent"), _subproblem("independent"),
    ])
    from source_preflight_fixtures import write_source_preflight
    write_source_preflight(tmp_path, spec)
    result = asyncio.run(LLMModeler(CountingLLM()).model(
        problem_spec=spec, evidence_profile={"evidence": []}, task={}, work_dir=tmp_path,
    ))
    # 上游明确阻断时，下游也必须阻断；不能请求LLM虚构上游结果。
    assert calls == ["parent", "independent"]
    assert [item.subproblem_id for item in result.subproblems] == ["child", "parent", "independent"]
    assert "parent" in result.subproblems[0].blocking_reason


@pytest.mark.parametrize("artifact", ["approved_model_ir.json", "result_registry.json"])
def test_verification_rejects_stale_execution_model_or_result(tmp_path, artifact):
    from test_guidance_mathematical_adequacy import _execute_case
    from app.guidance.verification_stage import ResultVerificationStage

    _execute_case(tmp_path, "original")
    path = tmp_path / artifact
    data = json.loads(path.read_text(encoding="utf-8"))
    if artifact == "approved_model_ir.json":
        data["approved_model_ir"]["subproblems"][0]["objective"] = "A changed mathematical objective."
    else:
        data["verified_results"][0]["value"]["objective_value"] = -999
    path.write_text(json.dumps(data), encoding="utf-8")
    asyncio.run(ResultVerificationStage().run(
        task_id="stale", task={}, input_path=tmp_path / "execution_manifest.json",
        output_path=tmp_path / "verification_report.json",
    ))
    report = json.loads((tmp_path / "verification_report.json").read_text(encoding="utf-8"))
    assert report["execution_verified"]["status"] == "failed"


def test_failed_pipeline_retry_removes_old_publishable_artifacts(tmp_path):
    from app.guidance.pipeline import GuidancePipeline
    from test_guidance_pipeline_ir import RecordingStage, _blocked_ir, _run_pipeline

    class FailingReview:
        async def run(self, **kwargs):
            raise RuntimeError("review transport unavailable")

    old_files = ["execution_manifest.json", "result_registry.json", "verification_report.json",
                 "notebook.ipynb", "guidance.md", "res.md", "guidance_audit_report.json"]
    for name in old_files:
        (tmp_path / name).write_text("old published result", encoding="utf-8")
    calls = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        source_stage=RecordingStage("source", calls),
        modeling_stage=RecordingStage("modeling", calls, _blocked_ir()),
        model_review_stage=FailingReview(), calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )
    with pytest.raises(RuntimeError, match="transport"):
        _run_pipeline(pipeline, "retry", tmp_path)
    assert all(not (tmp_path / name).exists() for name in old_files)


@pytest.mark.parametrize("missing_binding", ["all", "result_registry.json", "notebook.ipynb", "figure.svg"])
def test_verification_rejects_incomplete_output_snapshots(tmp_path, missing_binding):
    from test_guidance_mathematical_adequacy import _execute_case
    from app.guidance.recovery import file_snapshots
    from app.guidance.verification_stage import ResultVerificationStage

    _execute_case(tmp_path, "original")
    manifest_path = tmp_path / "execution_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if missing_binding == "figure.svg":
        (tmp_path / "figure.svg").write_text("<svg/>", encoding="utf-8")
        registry_path = tmp_path / "figure_registry.json"
        registry_path.write_text(json.dumps({"figures": [{
            "path": "figure.svg", "role": "result", "linked_result_ids": ["answer"],
        }]}), encoding="utf-8")
        manifest["output_files_sha256"].update(file_snapshots(tmp_path, ["figure_registry.json"]))
    elif missing_binding == "all":
        manifest["output_files_sha256"] = {}
    else:
        del manifest["output_files_sha256"][missing_binding]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    asyncio.run(ResultVerificationStage().run(
        task_id="missing-binding", task={}, input_path=manifest_path,
        output_path=tmp_path / "verification_report.json",
    ))
    report = json.loads((tmp_path / "verification_report.json").read_text(encoding="utf-8"))
    assert report["execution_verified"]["status"] == "failed"
    assert any("snapshot" in reason for reason in report["execution_verified"]["reasons"])
