import json
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
        assumption_checks=[accepted],
        subproblem_reviews=[{
            "subproblem_id": model_ir.subproblems[0].subproblem_id,
            "status": "approved",
        }],
    )

    validate_model_review(review, model_ir)

    incomplete = review.model_copy(update={"assumption_checks": []})
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
