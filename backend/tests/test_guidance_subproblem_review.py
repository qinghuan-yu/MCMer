import json
from pathlib import Path

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import BlockedModelIR, ModelIR
from app.guidance.model_review_contracts import (
    ApprovedModelIR, ModelIRReview, RevisionCheckSpec, SubproblemReviewSpec,
)
from app.guidance.model_review_validation import assess_mathematical_review, bind_model_review


def _model():
    path = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    return ModelIR.model_validate_json(path.read_text(encoding="utf-8"))


def _unassessed_payload(identifier):
    return {
        "subproblem_id": identifier,
        "status": "blocked",
        "assessment_status": "not_assessed",
        "blocking_issues": ["The reviewer exhausted its contract attempts."],
    }


def _revision_request(identifier):
    return SubproblemReviewSpec(
        subproblem_id=identifier, status="revision_required",
        required_revisions=[{
            "requirement_id": "objective_scope",
            "kind": "model",
            "description": "Correct the objective scope.",
            "acceptance_criteria": "The objective covers the intended period.",
        }],
    )


def test_reviewed_is_the_default_assessment_state():
    decision = SubproblemReviewSpec(subproblem_id="first", status="approved")
    assert decision.assessment_status == "reviewed"


@pytest.mark.parametrize("status", ["approved", "revision_required"])
def test_unassessed_review_cannot_approve_or_request_mathematical_revisions(status):
    payload = _unassessed_payload("first")
    payload["status"] = status
    with pytest.raises(ValueError, match="not_assessed review must be blocked"):
        SubproblemReviewSpec.model_validate(payload)


@pytest.mark.parametrize("field, content", [
    ("assumption_checks", {
        "assumption_id": "scenario", "status": "rejected", "rationale": "No contract was returned.",
        "alternative_scenario_coverage": "Unknown.", "sensitivity_check_coverage": "Unknown.",
    }),
    ("revision_checks", {
        "requirement_id": "objective_scope", "status": "rejected",
        "model_paths": ["/objective"], "rationale": "No contract was returned.",
    }),
    ("mathematical_checks", {
        "requirement_id": "$objective", "status": "not_assessed",
        "model_paths": [], "rationale": "No contract was returned.",
    }),
    ("required_revisions", {
        "requirement_id": "objective_scope", "description": "Change the model.",
        "kind": "model",
        "acceptance_criteria": "A contract should exist.",
    }),
])
def test_unassessed_review_cannot_fabricate_assessment_content(field, content):
    payload = _unassessed_payload("first")
    payload[field] = [content]
    with pytest.raises(ValueError, match="not_assessed review cannot contain assessment content"):
        SubproblemReviewSpec.model_validate(payload)


def test_unassessed_local_contract_skips_content_but_keeps_subproblem_identity():
    from app.guidance.model_review_validation import validate_model_review, validate_subproblem_review

    model = _model()
    payload = model.model_dump(mode="json")
    payload["subproblems"][0]["assumptions"] = [{
        "id": "scenario", "statement": "There is no terminal salvage value.",
        "basis": "scenario_choice", "source_refs": ["scenario.no_salvage"],
        "affected_model_elements": ["objective"],
        "alternative_scenarios": ["Allow terminal salvage."],
        "sensitivity_checks": ["Compare both terminal values."],
    }]
    model = ModelIR.model_validate(payload)
    subproblem = model.subproblems[0]
    decision = SubproblemReviewSpec.model_validate(_unassessed_payload(subproblem.subproblem_id))
    requested = _revision_request(subproblem.subproblem_id)

    validate_subproblem_review(decision, subproblem, revision_request=requested)
    validate_model_review(
        ModelIRReview(model_id=model.model_id, subproblem_reviews=[decision]), model,
        revision_request=ModelIRReview(model_id=model.model_id, subproblem_reviews=[requested]),
    )
    with pytest.raises(ValueError, match="subproblem review identity mismatch"):
        validate_subproblem_review(decision.model_copy(update={"subproblem_id": "other"}), subproblem)
    with pytest.raises(ValueError, match="revision request subproblem identity mismatch"):
        validate_subproblem_review(
            decision, subproblem, revision_request=requested.model_copy(update={"subproblem_id": "other"}),
        )
    reviewed = decision.model_copy(update={"assessment_status": "reviewed"})
    with pytest.raises(ValueError, match="assumption review coverage mismatch"):
        validate_subproblem_review(reviewed, subproblem, revision_request=requested)


@pytest.mark.parametrize("path", ["/subproblems/0/objective", "/subproblems/1/objective"])
def test_revision_contract_rejects_old_whole_model_and_cross_subproblem_paths(path):
    with pytest.raises(ValueError, match="relative to the current subproblem"):
        RevisionCheckSpec(
            requirement_id="objective_scope", status="accepted", model_paths=[path],
            rationale="The objective was corrected.",
        )


def test_local_revision_validation_accepts_only_complete_supported_checks():
    from app.guidance.model_review_validation import validate_subproblem_review

    subproblem = _model().subproblems[0]
    requested = _revision_request(subproblem.subproblem_id)
    decision = SubproblemReviewSpec(
        subproblem_id=subproblem.subproblem_id, status="approved",
        revision_checks=[{
            "requirement_id": "objective_scope", "status": "accepted",
            "model_paths": ["/objective"], "rationale": "The objective scope was corrected.",
        }],
    )
    validate_subproblem_review(decision, subproblem, revision_request=requested)
    with pytest.raises(ValueError, match="initial review cannot contain revision_checks"):
        validate_subproblem_review(decision, subproblem)
    with pytest.raises(ValueError, match="revision review coverage mismatch"):
        validate_subproblem_review(decision.model_copy(update={"revision_checks": []}), subproblem, revision_request=requested)
    rejected = decision.model_copy(update={"revision_checks": [
        decision.revision_checks[0].model_copy(update={"status": "rejected"}),
    ]})
    with pytest.raises(ValueError, match="unaccepted revision requirements"):
        validate_subproblem_review(rejected, subproblem, revision_request=requested)


def test_unassessed_subproblem_is_not_hidden_by_independent_approved_review(tmp_path):
    first_model = _model()
    first = first_model.subproblems[0]
    second = first.model_copy(update={"subproblem_id": "unassessed_problem"})
    model = ModelIR(model_id=first_model.model_id, subproblems=[first, second])
    review = ModelIRReview(model_id=model.model_id, subproblem_reviews=[
        SubproblemReviewSpec(
            subproblem_id=first.subproblem_id, status="approved",
            mathematical_checks=[{
                "requirement_id": requirement, "status": "passed",
                "model_paths": ["/execution_components/0/model/objective"],
                "rationale": "The mathematical requirement is represented in the objective.",
            } for requirement in ["$objective", "$formulation"]],
        ),
        SubproblemReviewSpec.model_validate(_unassessed_payload(second.subproblem_id)),
    ])
    problem = ProblemSpec(task_summary="Review both independent subproblems.", subproblems=[{
        "id": part.subproblem_id, "required_fact_ids": [], "required_uncertainty_ids": [],
        "depends_on_subproblem_ids": [],
    } for part in model.subproblems])
    blocked = BlockedModelIR(
        subproblem_id=second.subproblem_id, execution_status="blocked", objective=second.objective,
        blocking_reason="The reviewer exhausted its contract attempts.", missing_inputs=["valid review"],
        recovery_action="Repeat the subproblem review.", expected_outputs=["review decision"],
    )
    projection = ModelIR(model_id=model.model_id, subproblems=[first, blocked])
    evidence = {"schema_version": "1.0", "evidence": []}
    approved = ApprovedModelIR(
        review_status="partial", model_id=model.model_id,
        executable_subproblem_ids=[first.subproblem_id], approved_model_ir=projection,
        review_binding=bind_model_review(
            review=review, model_ir=model, approved_model_ir=projection, problem_spec=problem,
            evidence_profile=evidence, input_catalog=None, revision_request=None,
        ),
    )
    for name, document in {
        "model_ir.json": model.model_dump(mode="json"),
        "model_review.json": review.model_dump(mode="json"), "evidence_profile.json": evidence,
    }.items():
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")

    result = assess_mathematical_review(approved, root=tmp_path, problem_spec=problem)

    assert result.status == "not_assessed"
    assert any("unassessed_problem" in reason and "not assessed" in reason for reason in result.reasons)
