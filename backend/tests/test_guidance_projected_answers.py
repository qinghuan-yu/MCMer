import asyncio
from copy import deepcopy
import json

import pytest

from app.guidance.evidence_consistency import validate_planned_answer_evidence
from app.guidance.model_ir import ModelIR
from app.guidance.operator_execution import _result_registry
from app.guidance.recovery import canonical_digest
from app.guidance.verification_stage import ResultVerificationStage
from test_guidance_recovery import _scalar_model


def _model(paths, *, whole=False):
    data = _scalar_model().model_dump(mode="json")
    for sub, path in zip(data["subproblems"], paths):
        component = sub["execution_components"][0]
        producer = component["operators"][-1]
        component["variables"][-1]["id"] = "fit"
        producer["outputs"] = ["fit"]
        answer = sub["final_answers"][0]
        answer["value_ref"] = path
        if whole:
            sub["final_answers"].append({**answer, "id": sub["subproblem_id"] + "_whole", "value_ref": "fit"})
    return ModelIR.model_validate(data)


def test_projected_fields_resolve_only_within_their_owner_and_share_one_backing_output():
    model = _model(["fit.coef.a", "fit.coef.a"])
    a = {"coef": {"a": 3, "b": 0.5}, "observed": [1, 2]}
    b = {"coef": {"a": 7, "b": 0.8}, "observed": [5, 8]}
    values = {"chain": {"fit": a}, "independent": {"fit": b}}
    sources = {"chain": {"fit": "dependent"}, "independent": {"fit": "other"}}
    registry = _result_registry(model, values, sources)
    assert not registry["blocked_results"]
    assert [row["value"] for row in registry["verified_results"]] == [3, 7]
    assert registry["supporting_outputs"]["chain"]["fit"] == {"source_node": "dependent", "value": a}
    assert registry["verified_results"][0]["support_ref"] == "result_registry.json#/supporting_outputs/chain/fit"
    assert registry["summary"]["verified_count"] == 2

    whole = _result_registry(_model(["fit.coef.a", "fit.coef.b"], whole=True), values, sources)
    assert not whole.get("supporting_outputs")
    assert whole["verified_results"][0]["support_ref"] == "result_registry.json#chain_whole"
    missing = _result_registry(_model(["fit.unknown", "fit.coef.b"]), values, sources)
    assert [row["value"] for row in missing["verified_results"]] == [0.8]
    assert missing["blocked_results"][0]["id"] == "chain_answer"


def test_planning_rejects_answer_from_another_subproblem_output():
    with pytest.raises(ValueError, match="value_ref"):
        validate_planned_answer_evidence(_model(["fit.coef.a", "source_value.field"]), {"evidence": []})


def test_executor_keeps_equal_output_aliases_separate_for_each_subproblem(tmp_path):
    from test_guidance_recovery import _execute, _registry

    model = _model(["fit", "fit"])
    manifest = _execute(tmp_path, model, _registry([], set()))
    assert manifest.status == "passed"
    rows = json.loads((tmp_path / "result_registry.json").read_bytes())["verified_results"]
    assert [(row["source_node"], row["value"]) for row in rows] == [("dependent", 3), ("other", 9)]


def test_default_workflow_recomputes_projected_answers_from_bound_operator_output(tmp_path, monkeypatch):
    from app.guidance import workflow
    from app.guidance.recovery import file_snapshots
    from math_tool_fixtures import scripted_model_reply
    from scripts.run_closeout import prepare_run
    import test_anscombe_linear_fit as regression

    def reply(payload, kwargs):
        if kwargs.get("tools") and "subproblems" in payload:
            sub = payload["subproblems"][0]
            sub["final_answers"] = [
                {**sub["final_answers"][0], "id": field, "value_ref": f"line_fit.named_coefficients.{field}"}
                for field in ("intercept", "slope")
            ]
        return scripted_model_reply(payload, kwargs)

    monkeypatch.setattr(regression, "scripted_model_reply", reply)
    monkeypatch.setattr(workflow, "LLM", regression.OfflineRegression)
    root = tmp_path / "run"
    task = prepare_run(regression.CASE, root)

    async def run():
        return [event async for event in workflow.build_default_guidance_pipeline().run(root.name, task)]

    assert asyncio.run(run())[-1]["data"]["status"] == "completed"
    registry_path = root / "result_registry.json"
    manifest_path = root / "execution_manifest.json"
    original = json.loads(registry_path.read_bytes())
    manifest = json.loads(manifest_path.read_bytes())
    report = ResultVerificationStage.assess(manifest_path, root)
    record, = report.model_adequate.numerical_validation.baseline_comparisons
    reference = original["verified_results"][0]["support_ref"]
    assert record.evidence_ref == reference
    assert len(original["supporting_outputs"]["line"]) == 1
    support = original["supporting_outputs"]["line"]["line_fit"]
    assert manifest["output_value_sha256"] == {reference: canonical_digest(support["value"])}
    for mutate in (
        lambda r: r["verified_results"].pop(),
        lambda r: r["verified_results"][0].update(id=["invalid"]),
        lambda r: r["verified_results"][0].update(value=89),
        lambda r: r["verified_results"][0].update(value_ref="line_fit.named_coefficients.slope"),
        lambda r: r["verified_results"][0].pop("support_ref"),
        lambda r: r["verified_results"][0].update(support_ref="result_registry.json#/supporting_outputs/other/line_fit"),
        lambda r: r["supporting_outputs"]["line"]["line_fit"].update(source_node="read"),
        lambda r: r["supporting_outputs"]["line"]["line_fit"]["value"]["observed"].reverse(),
        lambda r: r["supporting_outputs"]["line"].pop("line_fit"),
    ):
        stale = deepcopy(original)
        mutate(stale)
        registry_path.write_text(json.dumps(stale), encoding="utf-8")
        manifest["output_files_sha256"].update(file_snapshots(root, [registry_path.name]))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failed = ResultVerificationStage.assess(manifest_path, root)
        assert failed.evidence_supported.status != "passed"
        assert not failed.model_adequate.numerical_validation.baseline_comparisons
