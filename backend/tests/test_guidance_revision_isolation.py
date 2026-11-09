import asyncio
from pathlib import Path

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import BlockedModelIR, ModelIR
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.modeler import LLMModeler


@pytest.mark.parametrize("upstream_result", ["changed", "unchanged", "failed"])
def test_revision_preserves_unaffected_models_and_invalidates_changed_dependencies(
    tmp_path, monkeypatch, upstream_result,
):
    fixture = Path(__file__).parent / "fixtures/model_ir/wuyi_anchor_bolt.json"
    original = ModelIR.model_validate_json(fixture.read_text(encoding="utf-8")).subproblems[0]
    identifiers = ["leaf", "middle", "repair", "independent", "unreviewed", "unavailable", "unreviewed_child"]
    dependencies = {"leaf": ["middle"], "middle": ["repair"], "unreviewed_child": ["repair"]}
    unreviewed = {"unreviewed", "unreviewed_child"}
    previous = ModelIR(model_id="revision-isolation", subproblems=[
        original.model_copy(update={"subproblem_id": identifier})
        if identifier != "unavailable" else BlockedModelIR(
            execution_status="blocked", subproblem_id=identifier, objective="Estimate unavailable observations.",
            blocking_reason="Observations are unavailable.", missing_inputs=["observations"],
            recovery_action="Register observations.", expected_outputs=["estimate"],
        ) for identifier in identifiers
    ])
    before = previous.model_dump_json()
    previous_by_id = {item.subproblem_id: item for item in previous.subproblems}
    problem_spec = ProblemSpec(task_summary="Revise a dependent model without overwriting independent models.", subproblems=[{
        "id": identifier, "required_fact_ids": [], "required_uncertainty_ids": [],
        "depends_on_subproblem_ids": dependencies.get(identifier, []),
    } for identifier in identifiers])
    review = ModelIRReview(model_id=previous.model_id, subproblem_reviews=[{
        "subproblem_id": identifier,
        "status": "revision_required" if identifier == "repair" else
        "blocked" if identifier in unreviewed | {"unavailable"} else "approved",
        "assessment_status": "not_assessed" if identifier in unreviewed else "reviewed",
        "blocking_issues": ["No valid review is available."] if identifier in unreviewed else
        ["Observations are unavailable."] if identifier == "unavailable" else [],
        "required_revisions": [{
            "requirement_id": "repair-equation", "description": "Repair the physical equation.",
            "kind": "model",
            "acceptance_criteria": "The equation matches the source.",
        }] if identifier == "repair" else [],
    } for identifier in identifiers])
    calls = []
    dependency_snapshots = {}

    async def generate(**kwargs):
        identifier = kwargs["subproblem"]["id"]
        calls.append(identifier)
        dependency_snapshots[identifier] = kwargs["slice_spec"]["dependency_models"]
        if identifier == "repair" and upstream_result == "failed":
            raise RuntimeError("The requested repair failed validation.")
        subproblem = previous_by_id[identifier]
        if identifier == "repair" and upstream_result == "changed":
            subproblem = subproblem.model_copy(update={"objective": "Use the corrected source equation."})
        return ModelIR(model_id=f"generated-{identifier}", subproblems=[subproblem])

    monkeypatch.setattr("app.guidance.modeler._request_single_subproblem_model", generate)
    result = asyncio.run(LLMModeler(object()).revise(
        problem_spec=problem_spec, previous_model_ir=previous, review=review,
        evidence_profile={"evidence": []}, task={}, work_dir=tmp_path,
    ))

    assert calls == (["repair", "middle", "leaf"] if upstream_result == "changed" else ["repair"])
    assert result.model_id == previous.model_id
    assert [item.subproblem_id for item in result.subproblems] == identifiers
    assert previous.model_dump_json() == before
    for identifier in ["independent", "unreviewed", "unavailable"]:
        assert next(item for item in result.subproblems if item.subproblem_id == identifier) == previous_by_id[identifier]
    if upstream_result == "changed":
        assert dependency_snapshots["middle"]["repair"]["objective"] == "Use the corrected source equation."
        assert "middle" in dependency_snapshots["leaf"]
        assert dependency_snapshots["leaf"]["repair"]["objective"] == "Use the corrected source equation."
    elif upstream_result == "failed":
        assert all(isinstance(item, BlockedModelIR) for item in result.subproblems[:3])
        assert "repair" in result.subproblems[1].blocking_reason
        assert "middle" in result.subproblems[0].blocking_reason
    else:
        assert result == previous
    if upstream_result != "unchanged":
        assert isinstance(result.subproblems[-1], BlockedModelIR)
        assert "repair" in result.subproblems[-1].blocking_reason
