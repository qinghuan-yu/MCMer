from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ModelIR, SolvableModelIR
from app.guidance.model_review_contracts import ApprovedModelIR, ModelIRReview, SubproblemReviewSpec
from app.guidance.model_review_validation import assess_mathematical_review, bind_model_review
from app.guidance.modeling_context import _subproblem_review_payload
from app.guidance.recovery import canonical_digest


def _model_payload():
    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["subproblems"][0]["assumptions"] = [{
        "id": "shared_local_id",
        "statement": "Purchases have no terminal salvage value.",
        "basis": "scenario_choice",
        "source_refs": ["scenario.no_salvage"],
        "affected_model_elements": ["objective"],
        "alternative_scenarios": ["Allow a terminal salvage value."],
        "sensitivity_checks": ["Compare terminal inventories and costs."],
    }]
    return payload


def test_same_assumption_id_in_distinct_subproblems_merges_without_rewriting():
    payload = _model_payload()
    first = payload["subproblems"][0]
    second = deepcopy(first)
    second["subproblem_id"] = "independent_problem"
    second["assumptions"][0]["statement"] = "Only the alternative scenario has a salvage value."
    payload["subproblems"].append(second)
    expected = [SolvableModelIR.model_validate(part).model_dump(mode="json") for part in payload["subproblems"]]

    model = ModelIR.model_validate(payload)

    assert [part.model_dump(mode="json") for part in model.subproblems] == expected
    assert [part.assumptions[0].id for part in model.subproblems] == ["shared_local_id"] * 2


def test_canonical_subproblem_rejects_duplicate_assumption_ids():
    payload = _model_payload()["subproblems"][0]
    payload["assumptions"].append(deepcopy(payload["assumptions"][0]))
    with pytest.raises(ValidationError, match="assumption ids must be unique"):
        SolvableModelIR.model_validate(payload)


def test_revision_payload_only_contains_target_subproblem_assumption_checks():
    decisions = [{
        "subproblem_id": identifier, "status": "approved",
        "assumption_checks": [{
            "assumption_id": "shared_local_id", "status": "accepted",
            "rationale": f"Interpretation for {identifier}.",
            "alternative_scenario_coverage": "Declared condition only.",
            "sensitivity_check_coverage": "Not assessed.",
        }],
    } for identifier in ["first", "second"]]
    review = ModelIRReview(model_id="scoped_review", subproblem_reviews=decisions)

    payload = _subproblem_review_payload(review, "second")

    assert "assumption_checks" not in payload
    assert len(payload["subproblem_reviews"]) == 1
    assert payload["subproblem_reviews"][0]["assumption_checks"] == decisions[1]["assumption_checks"]
    assert "Interpretation for first." not in json.dumps(payload)


def test_legacy_flat_review_cannot_keep_a_matching_mathematical_binding(tmp_path):
    payload = _model_payload()
    payload["subproblems"][0]["assumptions"] = []
    model = ModelIR.model_validate(payload)
    identifier = model.subproblems[0].subproblem_id
    problem = ProblemSpec(task_summary="Check the scoped review migration.", subproblems=[{
        "id": identifier, "required_fact_ids": [], "required_uncertainty_ids": [],
        "depends_on_subproblem_ids": [],
    }])
    review = ModelIRReview.model_construct(
        model_id=model.model_id, status="approved", executable_subproblem_ids=[identifier],
        assumption_checks=[], subproblem_reviews=[SubproblemReviewSpec.model_validate({
        "subproblem_id": identifier, "status": "approved",
        "mathematical_checks": [{
            "requirement_id": requirement, "status": "passed",
            "model_paths": ["/execution_components/0/model/objective"],
            "rationale": "The objective is explicitly represented in the model.",
        } for requirement in ["$objective", "$formulation"]],
    })])
    legacy = review.model_dump(mode="json")
    for decision in legacy["subproblem_reviews"]:
        decision.pop("assumption_checks", None)
    legacy["assumption_checks"] = []
    evidence = {"schema_version": "1.0", "evidence": []}
    binding = bind_model_review(
        review=review, model_ir=model, approved_model_ir=model, problem_spec=problem,
        evidence_profile=evidence, input_catalog=None, revision_request=None,
    ).model_copy(update={"review_sha256": canonical_digest(legacy)})
    approved = ApprovedModelIR(
        review_status="approved", model_id=model.model_id,
        executable_subproblem_ids=[identifier], approved_model_ir=model, review_binding=binding,
    )
    for name, document in {
        "model_ir.json": model.model_dump(mode="json"),
        "model_review.json": legacy, "evidence_profile.json": evidence,
    }.items():
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")

    result = assess_mathematical_review(approved, root=tmp_path, problem_spec=problem)

    assert result.status == "not_assessed"
    assert any("unavailable or invalid" in reason for reason in result.reasons)
