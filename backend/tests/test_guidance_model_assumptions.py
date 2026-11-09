import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError


def _assumption_payload() -> dict:
    return {
        "id": "training_resources_exclusive",
        "statement": (
            "Resources assigned to training are unavailable for service in the same period."
        ),
        "basis": "problem_ambiguity",
        "source_refs": [
            "problem_spec.json#uncertainties/training_resources_exclusive"
        ],
        "affected_model_elements": ["training_capacity", "service_capacity"],
        "alternative_scenarios": [
            "Training resources remain available for service in the same period."
        ],
        "sensitivity_checks": [
            "Resolve the model under both exclusivity scenarios and compare decisions."
        ],
    }


def _model_payload(*, include_assumptions: bool = True) -> dict:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    if include_assumptions:
        payload["subproblems"][0]["assumptions"] = [_assumption_payload()]
    else:
        payload["subproblems"][0].pop("assumptions", None)
    return payload


def _assumption_check(assumption_id: str, status: str = "accepted") -> dict:
    return {
        "assumption_id": assumption_id,
        "status": status,
        "rationale": "The declared scenario was reviewed for this subproblem.",
        "alternative_scenario_coverage": "Both local alternatives were considered.",
        "sensitivity_check_coverage": "The local sensitivity evidence was reviewed.",
    }


def _scoped_review_case():
    payload = _model_payload()
    first = payload["subproblems"][0]
    first["subproblem_id"] = "first"
    second = deepcopy(first)
    second["subproblem_id"] = "second"
    second["assumptions"][0]["statement"] = "The second subproblem uses its own resource convention."
    payload["subproblems"] = [first, second]
    review = {
        "model_id": payload["model_id"],
        "subproblem_reviews": [{
            "subproblem_id": item["subproblem_id"],
            "status": "approved",
            "assumption_checks": [_assumption_check(item["assumptions"][0]["id"])],
        } for item in payload["subproblems"]],
    }
    return payload, review


@pytest.mark.parametrize("rejected_index", [0, 1])
def test_same_named_assumptions_receive_independent_subproblem_decisions(rejected_index: int) -> None:
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_review_contracts import ModelIRReview
    from app.guidance.model_review_validation import validate_model_review

    payload, review_payload = _scoped_review_case()
    decision = review_payload["subproblem_reviews"][rejected_index]
    decision["status"] = "blocked"
    decision["blocking_issues"] = ["This subproblem's assumption lacks supporting evidence."]
    decision["assumption_checks"][0]["status"] = "rejected"
    review = ModelIRReview.model_validate(review_payload)
    model_ir = ModelIR.model_validate(payload)

    validate_model_review(review, model_ir)

    assert review.status == "partial"
    assert review.executable_subproblem_ids == [payload["subproblems"][1 - rejected_index]["subproblem_id"]]
    assert review.subproblem_reviews[1 - rejected_index].assumption_checks[0].status == "accepted"
    decision["status"] = "approved"
    decision.pop("blocking_issues")
    with pytest.raises(ValueError, match="approved subproblem review contains an unaccepted assumption"):
        validate_model_review(ModelIRReview.model_validate(review_payload), model_ir)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "wrong_owner", "wrong_order"])
def test_assumption_review_coverage_is_exact_within_each_subproblem(failure: str) -> None:
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_review_contracts import ModelIRReview
    from app.guidance.model_review_validation import validate_model_review

    payload, review_payload = _scoped_review_case()
    for subproblem, decision in zip(payload["subproblems"], review_payload["subproblem_reviews"]):
        extra = _assumption_payload()
        extra["id"] = f"only_{subproblem['subproblem_id']}"
        subproblem["assumptions"].append(extra)
        decision["assumption_checks"].append(_assumption_check(extra["id"]))
    checks = review_payload["subproblem_reviews"][0]["assumption_checks"]
    if failure == "missing":
        checks.pop()
    elif failure == "duplicate":
        checks.append(deepcopy(checks[0]))
    elif failure == "wrong_owner":
        checks[1]["assumption_id"] = "only_second"
    else:
        checks.reverse()

    with pytest.raises(ValueError, match="assumption (review coverage mismatch|check ids must be unique)"):
        validate_model_review(
            ModelIRReview.model_validate(review_payload), ModelIR.model_validate(payload),
        )


@pytest.mark.parametrize("old_checks", [[], [_assumption_check("old_assumption")]])
def test_review_rejects_legacy_root_assumption_checks(old_checks: list[dict]) -> None:
    from app.guidance.model_review_contracts import ModelIRReview

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ModelIRReview.model_validate({
            "model_id": "old_review",
            "assumption_checks": old_checks,
            "subproblem_reviews": [{"subproblem_id": "first", "status": "approved"}],
        })


def test_blocked_model_subproblem_cannot_receive_assumption_checks() -> None:
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_review_contracts import ModelIRReview
    from app.guidance.model_review_validation import validate_model_review

    model_ir = ModelIR.model_validate({
        "model_id": "blocked_model",
        "subproblems": [{
            "subproblem_id": "blocked",
            "execution_status": "blocked",
            "objective": "Estimate a result.",
            "blocking_reason": "Required data is missing.",
            "missing_inputs": ["observations"],
            "recovery_action": "Provide the observations.",
            "expected_outputs": ["estimate"],
        }],
    })
    payload = {
        "model_id": model_ir.model_id,
        "subproblem_reviews": [{
            "subproblem_id": "blocked", "status": "blocked",
            "blocking_issues": ["Required data is missing."],
        }],
    }
    validate_model_review(ModelIRReview.model_validate(payload), model_ir)
    payload["subproblem_reviews"][0]["assumption_checks"] = [_assumption_check("invented")]
    with pytest.raises(ValueError, match="assumption review coverage mismatch"):
        validate_model_review(ModelIRReview.model_validate(payload), model_ir)


def test_modeling_assumption_requires_alternatives_and_sensitivity_checks() -> None:
    from app.guidance.model_ir import ModelingAssumptionSpec

    assumption = ModelingAssumptionSpec.model_validate(_assumption_payload())
    assert assumption.basis == "problem_ambiguity"

    for missing_field in ("alternative_scenarios", "sensitivity_checks"):
        invalid = _assumption_payload()
        invalid[missing_field] = []
        with pytest.raises(ValidationError, match=missing_field):
            ModelingAssumptionSpec.model_validate(invalid)

    disguised_fact = _assumption_payload()
    disguised_fact["source_refs"] = ["problem_spec.json#locked_facts/0"]
    with pytest.raises(ValidationError, match="uncertainty ids"):
        ModelingAssumptionSpec.model_validate(disguised_fact)


def test_solvable_model_ir_requires_explicit_assumption_list() -> None:
    from app.guidance.model_ir import ModelIR

    with pytest.raises(ValidationError, match="assumptions"):
        ModelIR.model_validate(_model_payload(include_assumptions=False))

    payload = _model_payload()
    payload["subproblems"][0]["assumptions"] = []
    assert ModelIR.model_validate(payload).subproblems[0].assumptions == []


def test_approved_review_must_cover_every_modeling_assumption() -> None:
    from app.guidance.model_review_contracts import (AssumptionReviewSpec, ModelIRReview)
    from app.guidance.model_ir import (ModelIR)
    from app.guidance.model_review_validation import validate_model_review

    model_ir = ModelIR.model_validate(_model_payload())
    accepted = AssumptionReviewSpec(
        assumption_id="training_resources_exclusive",
        status="accepted",
        rationale="The ambiguity is disclosed and both scenarios are scheduled for comparison.",
        alternative_scenario_coverage="Both exclusivity choices are represented.",
        sensitivity_check_coverage="The objective and decisions are compared across choices.",
    )
    review = ModelIRReview(
        model_id=model_ir.model_id,
        status="approved",
        identifiability="passed",
        dimensional_consistency="passed",
        data_sufficiency="passed under the declared scenario",
        computational_feasibility="passed",
        subproblem_reviews=[{
            "subproblem_id": model_ir.subproblems[0].subproblem_id,
            "status": "approved",
            "assumption_checks": [accepted],
        }],
    )

    validate_model_review(review, model_ir)

    incomplete = review.model_copy(update={"subproblem_reviews": [
        review.subproblem_reviews[0].model_copy(update={"assumption_checks": []}),
    ]})
    with pytest.raises(ValueError, match="assumption review coverage mismatch"):
        validate_model_review(incomplete, model_ir)


def test_result_claims_and_guidance_inherit_modeling_assumptions() -> None:
    from app.guidance.model_ir import ModelIR
    from app.guidance.operator_execution import _result_registry
    from app.guidance.renderer import render_verified_guidance

    model_ir = ModelIR.model_validate(_model_payload())
    registry = _result_registry(
        model_ir,
        {"solution": {"objective_value": 42.0}},
        {"solution": "solve_indexed_model"},
    )

    statement = _assumption_payload()["statement"]
    assert registry["summary"]["claim_registry"][0]["assumptions"] == [statement]

    guidance = render_verified_guidance(
        approved_model={
            "review_status": "approved",
            "model_id": model_ir.model_id,
            "approved_model_ir": model_ir.model_dump(mode="json"),
        },
        verification_report={
            "status": "verified",
            "model_id": model_ir.model_id,
            "artifact_refs": {"figures": []},
        },
        parameter_registry={"parameters": []},
        result_registry=registry,
    )

    assert statement in guidance
    assert "conditional result" in guidance.lower()
