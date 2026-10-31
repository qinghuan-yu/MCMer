import json
from pathlib import Path

import pytest


def _model_ir():
    from app.guidance.model_ir import ModelIR

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    return ModelIR.model_validate(
        json.loads(fixture_path.read_text(encoding="utf-8"))
    )


def test_approved_rereview_requires_complete_accepted_revision_evidence() -> None:
    from app.guidance.model_ir import (
        ModelIRReview,
        RevisionCheckSpec,
        RevisionRequirementSpec,
    )
    from app.guidance.model_review_validation import validate_model_review

    model_ir = _model_ir()
    revision_request = ModelIRReview(
        model_id=model_ir.model_id,
        assumption_checks=[],
        subproblem_reviews=[
            {
                "subproblem_id": "finite_inventory_policy",
                "status": "revision_required",
                "required_revisions": [
                    RevisionRequirementSpec(
                        requirement_id="constraint-coverage",
                        description="Add the missing typed constraint.",
                        acceptance_criteria=(
                            "The revised ModelIR exposes the objective."
                        ),
                    )
                ],
            }
        ],
    )
    accepted_check = RevisionCheckSpec(
        requirement_id="constraint-coverage",
        status="accepted",
        model_paths=["/subproblems/0/objective"],
        rationale="The revised model exposes the requested objective.",
    )
    approved = ModelIRReview(
        model_id=model_ir.model_id,
        assumption_checks=[],
        subproblem_reviews=[
            {
                "subproblem_id": "finite_inventory_policy",
                "status": "approved",
                "revision_checks": [accepted_check],
            }
        ],
    )

    validate_model_review(approved, model_ir, revision_request=revision_request)

    missing_payload = approved.model_dump(mode="json")
    missing_payload["subproblem_reviews"][0]["revision_checks"] = []
    missing = ModelIRReview.model_validate(missing_payload)
    with pytest.raises(ValueError, match="revision review coverage mismatch"):
        validate_model_review(missing, model_ir, revision_request=revision_request)

    unaccepted_payload = approved.model_dump(mode="json")
    unaccepted_payload["subproblem_reviews"][0]["revision_checks"][0][
        "status"
    ] = "revision_required"
    unaccepted = ModelIRReview.model_validate(unaccepted_payload)
    with pytest.raises(ValueError, match="unaccepted revision requirements"):
        validate_model_review(unaccepted, model_ir, revision_request=revision_request)

    nonexistent_payload = approved.model_dump(mode="json")
    nonexistent_payload["subproblem_reviews"][0]["revision_checks"][0][
        "model_paths"
    ] = ["/subproblems/0/execution_components/0/model/constraints/99"]
    nonexistent = ModelIRReview.model_validate(nonexistent_payload)
    with pytest.raises(ValueError, match="does not exist"):
        validate_model_review(nonexistent, model_ir, revision_request=revision_request)
