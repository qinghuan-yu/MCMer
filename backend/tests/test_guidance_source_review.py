"""数学审核只报告新来源冲突，不能再改写来源或撤回数学缺陷。"""

import pytest

from app.guidance.model_review_contracts import (
    RevisionCheckSpec, RevisionRequirementSpec, SourceConflictSpec, SubproblemReviewSpec,
)


def _model_requirement():
    return {
        "requirement_id": "training_cost", "kind": "model",
        "basis_refs": ["problem_spec.json#locked_facts/F_COST"],
        "description": "The training objective omits an explicitly charged participant.",
        "acceptance_criteria": "Represent all charged participants in the objective.",
    }


def test_late_source_conflict_reports_its_basis_without_inventing_a_model_defect():
    decision = SubproblemReviewSpec(
        subproblem_id="inventory", status="blocked", blocking_issues=["New conflict in the original evidence."],
        source_conflicts=[{
            "basis_refs": ["problem_spec.json#uncertainties/U_training"],
            "rationale": "The original source explicitly names both charged groups.",
        }],
    )
    assert decision.required_revisions == []
    assert decision.source_conflicts[0].basis_refs == ["problem_spec.json#uncertainties/U_training"]


@pytest.mark.parametrize("basis", [[], ["problem_spec.json#uncertainties/U_training"] * 2])
def test_source_conflict_requires_unique_explicit_basis(basis):
    with pytest.raises(ValueError):
        SourceConflictSpec(basis_refs=basis, rationale="Record the disputed original relation.")


@pytest.mark.parametrize("status", ["approved", "revision_required"])
def test_source_conflict_cannot_be_downgraded_to_a_mathematical_repair(status):
    with pytest.raises(ValueError, match="source conflict"):
        SubproblemReviewSpec(
            subproblem_id="inventory", status=status, required_revisions=[_model_requirement()],
            source_conflicts=[{
                "basis_refs": ["problem_spec.json#locked_facts/F_COST"],
                "rationale": "The prior source decision is disputed.",
            }],
        )


def test_legacy_untyped_revision_is_not_silently_treated_as_a_model_defect():
    payload = _model_requirement()
    payload.pop("kind")
    with pytest.raises(ValueError, match="kind"):
        RevisionRequirementSpec.model_validate(payload)


def test_source_obligation_is_not_a_mathematical_revision_kind():
    with pytest.raises(ValueError, match="kind"):
        RevisionRequirementSpec.model_validate({**_model_requirement(), "kind": "source"})


def test_actual_model_defect_cannot_be_withdrawn_by_changing_its_source():
    with pytest.raises(ValueError):
        RevisionCheckSpec(
            requirement_id="training_cost", status="withdrawn", model_paths=[],
            source_correction_ids=["resolve_training"], rationale="Changing a source cannot repair a formula.",
        )


def test_model_repair_still_requires_concrete_computational_evidence():
    with pytest.raises(ValueError, match="model paths"):
        RevisionCheckSpec(requirement_id="training_cost", status="accepted", model_paths=[],
                          rationale="The source interpretation was approved.")


def test_review_binding_changes_when_preflight_permission_changes(tmp_path):
    from app.guidance.model_review_contracts import ModelIRReview
    from app.guidance.model_review_validation import bind_model_review
    from test_guidance_source_reviewer import _decision, _prepare

    spec, model, record, catalog, profile = _prepare(tmp_path)
    review = ModelIRReview(model_id=model.model_id, subproblem_reviews=[
        _decision(item.id, [*item.required_fact_ids, "$objective", "$formulation"])
        for item in spec.subproblems
    ])
    args = dict(review=review, model_ir=model, approved_model_ir=model, problem_spec=spec,
                input_catalog=catalog, evidence_profile=profile, revision_request=None)
    original = bind_model_review(**args, source_preflight=record)
    changed = record.model_copy(deep=True)
    changed.adjudication.checks[0].rationale = "A different independently reviewed justification."
    assert original.source_preflight_sha256 is not None
    assert bind_model_review(**args, source_preflight=changed) != original


def test_source_conflict_basis_must_belong_to_the_reviewed_subproblem(tmp_path):
    from app.guidance.model_review_validation import validate_subproblem_review
    from test_guidance_source_reviewer import _prepare

    spec, model, record, catalog, profile = _prepare(tmp_path)
    decision = SubproblemReviewSpec(
        subproblem_id="sp1", status="blocked", blocking_issues=["Source conflict."],
        source_conflicts=[{
            "basis_refs": ["problem_spec.json#uncertainties/REAL_UNCERTAINTY"],
            "rationale": "Attempt to use another topic's source conflict.",
        }],
    )
    with pytest.raises(ValueError, match="scope|basis"):
        validate_subproblem_review(decision, model.subproblems[0], problem_spec=spec,
                                   source_preflight=record, input_catalog=catalog,
                                   evidence_profile=profile, work_dir=tmp_path)
