import asyncio
import json
from pathlib import Path

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import (
    ApprovedModelIR,
    ModelIRReview,
    ModelReviewSubmission,
)


def _case():
    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    original = ModelIR.model_validate_json(fixture.read_text(encoding="utf-8"))
    identifiers = ["leaf", "middle", "repair", "independent"]
    model_ir = ModelIR(
        model_id="isolated-review-projection",
        subproblems=[
            original.subproblems[0].model_copy(update={"subproblem_id": identifier})
            for identifier in identifiers
        ],
    )
    dependencies = {"leaf": ["middle"], "middle": ["repair"]}
    problem_spec = ProblemSpec.model_validate({
        "task_summary": "Preserve independently approved models while dependencies remain blocked.",
        "subproblems": [{
            "id": identifier,
            "required_fact_ids": [],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": dependencies.get(identifier, []),
        } for identifier in identifiers],
    })
    return model_ir, problem_spec


def _review(model_ir, repair_status="revision_required"):
    return ModelIRReview(
        model_id=model_ir.model_id,
        subproblem_reviews=[{
            "subproblem_id": subproblem.subproblem_id,
            "status": repair_status if subproblem.subproblem_id == "repair" else "approved",
            "required_revisions": [{
                "requirement_id": "repair-conservation",
                "description": "Repair the state conservation equation.",
                "acceptance_criteria": "The state equation conserves the registered stock.",
            }] if subproblem.subproblem_id == "repair" and repair_status == "revision_required" else [],
            "blocking_issues": ["The review contract could not be completed."]
            if subproblem.subproblem_id == "repair" and repair_status == "blocked" else [],
        } for subproblem in model_ir.subproblems],
    )


@pytest.mark.parametrize("repair_status", ["revision_required", "blocked"])
def test_review_projection_retains_independent_approval_and_blocks_dependency_closure(
    tmp_path, repair_status,
):
    from app.artifacts.evidence_profile import load_evidence_profile
    from app.guidance.model_review_validation import bind_model_review
    from app.guidance.stages import ModelReviewStage

    model_ir, problem_spec = _case()
    review = _review(model_ir, repair_status)

    class Reviewer:
        async def review(self, **kwargs):
            return review

    (tmp_path / "model_ir.json").write_text(model_ir.model_dump_json(), encoding="utf-8")
    (tmp_path / "problem_spec.json").write_text(problem_spec.model_dump_json(), encoding="utf-8")
    submission_path = tmp_path / "model_review_submission.json"
    submission_path.write_text(
        ModelReviewSubmission(model_ir_ref="model_ir.json").model_dump_json(), encoding="utf-8",
    )
    approved_path = asyncio.run(ModelReviewStage(reviewer=Reviewer()).run(
        task_id="review-isolation", task={}, input_path=submission_path,
        output_path=tmp_path / "approved_model_ir.json",
    ))
    approved = ApprovedModelIR.model_validate_json(approved_path.read_text(encoding="utf-8"))
    persisted_review = ModelIRReview.model_validate_json(
        (tmp_path / "model_review.json").read_text(encoding="utf-8")
    )

    assert persisted_review == review
    assert approved.review_status == "partial"
    assert approved.executable_subproblem_ids == ["independent"]
    assert [item.execution_status for item in approved.approved_model_ir.subproblems] == [
        "blocked", "blocked", "blocked", "solvable",
    ]
    assert "middle" in approved.approved_model_ir.subproblems[0].blocking_reason
    assert "repair" in approved.approved_model_ir.subproblems[1].blocking_reason
    assert approved.approved_model_ir.subproblems[3] == model_ir.subproblems[3]
    assert approved.review_binding == bind_model_review(
        review=review, model_ir=model_ir, approved_model_ir=approved.approved_model_ir,
        problem_spec=problem_spec,
        evidence_profile=load_evidence_profile(tmp_path).model_dump(mode="json"),
        input_catalog=None, revision_request=None,
    )
    assert ModelIR.model_validate_json(
        (tmp_path / "model_ir.json").read_text(encoding="utf-8")
    ) == model_ir

def test_execution_projection_rejects_revision_workflow_status():
    model_ir, _ = _case()
    blocked_model = ModelIR(model_id=model_ir.model_id, subproblems=[{
        "execution_status": "blocked", "subproblem_id": "repair", "objective": "Repair the model.",
        "blocking_reason": "A revision remains outstanding.", "missing_inputs": ["approved model"],
        "recovery_action": "Revise and review.", "expected_outputs": ["solution"],
    }])
    with pytest.raises(ValueError, match="review_status"):
        ApprovedModelIR(
            review_status="revision_required", model_id=model_ir.model_id,
            executable_subproblem_ids=[], approved_model_ir=blocked_model,
        )


def test_pipeline_revises_from_review_then_executes_independent_final_projection(tmp_path):
    from app.guidance.pipeline import GuidancePipeline

    model_ir, problem_spec = _case()
    review = _review(model_ir)
    projection = ModelIR(model_id=model_ir.model_id, subproblems=[
        subproblem if subproblem.subproblem_id == "independent" else {
            "execution_status": "blocked", "subproblem_id": subproblem.subproblem_id,
            "objective": subproblem.objective,
            "blocking_reason": "Pending review or dependent on an unapproved model.",
            "missing_inputs": ["approved model"], "recovery_action": "Revise and review.",
            "expected_outputs": ["solution"],
        } for subproblem in model_ir.subproblems
    ])
    approved = ApprovedModelIR(
        review_status="partial", model_id=model_ir.model_id,
        executable_subproblem_ids=["independent"], approved_model_ir=projection,
    )
    calls = []

    class Stage:
        def __init__(self, name, payload):
            self.name, self.payload = name, payload

        async def run(self, *, input_path, output_path, **kwargs):
            calls.append((self.name, input_path.name if input_path else None))
            if self.name == "model_review":
                (tmp_path / "model_review.json").write_text(review.model_dump_json(), encoding="utf-8")
            if self.name == "calculation":
                persisted = ApprovedModelIR.model_validate_json(input_path.read_text(encoding="utf-8"))
                assert persisted.executable_subproblem_ids == ["independent"]
            output_path.write_text(self.payload, encoding="utf-8")
            return output_path

    pipeline = GuidancePipeline(
        decomposition_stage=Stage("decomposition", problem_spec.model_dump_json()),
        modeling_stage=Stage("modeling", model_ir.model_dump_json()),
        model_review_stage=Stage("model_review", approved.model_dump_json()),
        calculation_stage=Stage("calculation", "{}"),
        result_verification_stage=Stage("result_verification", '{"status":"partial"}'),
        guidance_stage=Stage("guidance", "Partial guidance."),
    )

    async def run():
        return [event async for event in pipeline.run("partial-revision", {"work_dir": str(tmp_path)})]

    asyncio.run(run())
    assert calls == [
        ("decomposition", None), ("modeling", "problem_spec.json"),
        ("model_review", "model_review_submission.json"),
        ("modeling", "revision_request.json"),
        ("model_review", "model_review_submission.json"),
        ("calculation", "approved_model_ir.json"),
        ("result_verification", "execution_manifest.json"),
        ("guidance", "verification_report.json"),
    ]
    assert json.loads((tmp_path / "model_review.json").read_text(encoding="utf-8"))["status"] == "revision_required"
    assert json.loads((tmp_path / "approved_model_ir.json").read_text(encoding="utf-8"))["review_status"] == "partial"
