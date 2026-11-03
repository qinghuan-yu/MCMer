"""Deterministic validation for initial and revision ModelIR reviews."""

from __future__ import annotations

from app.guidance.model_ir import (BlockedModelIR, ModelIR, SolvableModelIR)
from app.guidance.model_review_contracts import (ModelIRReview)


def validate_model_review(
    review: ModelIRReview,
    model_ir: ModelIR,
    *,
    revision_request: ModelIRReview | None = None,
) -> None:
    if review.model_id != model_ir.model_id:
        raise ValueError("model review model_id does not match Model IR")
    expected_subproblems = [
        subproblem.subproblem_id for subproblem in model_ir.subproblems
    ]
    reviewed_subproblems = [
        item.subproblem_id for item in review.subproblem_reviews
    ]
    if reviewed_subproblems != expected_subproblems:
        raise ValueError(
            "subproblem review coverage mismatch: "
            f"expected={expected_subproblems!r}; reviewed={reviewed_subproblems!r}"
        )
    subproblems_by_id = {
        subproblem.subproblem_id: subproblem
        for subproblem in model_ir.subproblems
    }
    for decision in review.subproblem_reviews:
        subproblem = subproblems_by_id[decision.subproblem_id]
        if isinstance(subproblem, BlockedModelIR) and decision.status != "blocked":
            raise ValueError(
                f"blocked Model IR subproblem {decision.subproblem_id!r} "
                "must receive a blocked review"
            )
    expected = [
        assumption.id
        for subproblem in model_ir.subproblems
        if isinstance(subproblem, SolvableModelIR)
        for assumption in subproblem.assumptions
    ]
    reviewed = [item.assumption_id for item in review.assumption_checks]
    if reviewed != expected:
        raise ValueError(
            f"assumption review coverage mismatch: expected={expected!r}; reviewed={reviewed!r}"
        )
    assumption_owner = {
        assumption.id: subproblem.subproblem_id
        for subproblem in model_ir.subproblems
        if isinstance(subproblem, SolvableModelIR)
        for assumption in subproblem.assumptions
    }
    decision_by_id = {
        item.subproblem_id: item for item in review.subproblem_reviews
    }
    for check in review.assumption_checks:
        if (
            check.status != "accepted"
            and decision_by_id[assumption_owner[check.assumption_id]].status == "approved"
        ):
            raise ValueError(
                "approved subproblem review contains an unaccepted assumption: "
                f"{check.assumption_id!r}"
            )

    if revision_request is None:
        if any(item.revision_checks for item in review.subproblem_reviews):
            raise ValueError("initial review cannot contain revision_checks")
        return
    if revision_request.model_id != model_ir.model_id:
        raise ValueError("revision request model_id does not match Model IR")
    if revision_request.status != "revision_required":
        raise ValueError("revision request must have revision_required status")

    requested_by_id = {
        item.subproblem_id: item for item in revision_request.subproblem_reviews
    }
    for decision in review.subproblem_reviews:
        requested = requested_by_id[decision.subproblem_id]
        expected_revisions = [
            item.requirement_id for item in requested.required_revisions
        ]
        reviewed_revisions = [
            item.requirement_id for item in decision.revision_checks
        ]
        if reviewed_revisions != expected_revisions:
            raise ValueError(
                "revision review coverage mismatch for "
                f"{decision.subproblem_id!r}: expected={expected_revisions!r}; "
                f"reviewed={reviewed_revisions!r}"
            )

    model_document = model_ir.model_dump(mode="json")
    for decision in review.subproblem_reviews:
        for check in decision.revision_checks:
            for path in check.model_paths:
                if not _json_pointer_exists(model_document, path):
                    raise ValueError(
                        f"revision evidence path {path!r} does not exist in Model IR"
                    )
        if decision.status == "approved":
            unaccepted_revisions = [
                item.requirement_id
                for item in decision.revision_checks
                if item.status != "accepted"
            ]
            if unaccepted_revisions:
                raise ValueError(
                    "approved subproblem review contains unaccepted revision "
                    f"requirements: {unaccepted_revisions!r}"
                )


def _json_pointer_exists(document: object, pointer: str) -> bool:
    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False
            current = current[token]
            continue
        if isinstance(current, list):
            if not token.isdigit():
                return False
            index = int(token)
            if index >= len(current):
                return False
            current = current[index]
            continue
        return False
    return True
