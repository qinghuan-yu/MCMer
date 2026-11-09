from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.guidance.model_review_contracts import (
    RevisionCheckSpec, RevisionRequirementSpec, SubproblemReviewSpec,
)


def _correction():
    return {
        "correction_id": "resolve_training",
        "target_kind": "uncertainty",
        "target_id": "U_training",
        "replacement_fact": {
            "fact_id": "F_training_objects",
            "statement": "Both trainees and instructors incur the training charge.",
            "source_refs": ["question.txt#paragraph-1"],
            "subproblem_ids": ["inventory"],
            "semantic_key": "training_charge_objects",
            "value": "trainees_and_instructors",
            "unit": "",
        },
        "evidence": [{
            "source_ref": "question.txt#paragraph-1",
            "quote": "Both trainees and instructors incur the training charge.",
        }],
        "rationale": "The source explicitly determines both charged groups.",
    }


def _source_requirement():
    return {
        "requirement_id": "training_scenarios",
        "kind": "source",
        "basis_refs": ["problem_spec.json#uncertainties/U_training"],
        "description": "Resolve the claimed ambiguity before choosing training scenarios.",
        "acceptance_criteria": "Use the explicit original charging rule.",
    }


def test_review_can_correct_a_false_ambiguity_without_inventing_a_model_defect():
    decision = SubproblemReviewSpec(
        subproblem_id="inventory", status="revision_required",
        source_corrections=[_correction()],
    )
    assert decision.source_corrections[0].replacement_fact.fact_id == "F_training_objects"
    assert decision.required_revisions == []


def test_source_requirement_explicitly_identifies_its_disputed_basis():
    requirement = RevisionRequirementSpec.model_validate(_source_requirement())
    assert requirement.kind == "source"
    assert requirement.basis_refs == ["problem_spec.json#uncertainties/U_training"]


def test_withdrawal_references_applied_source_corrections_instead_of_model_paths():
    check = RevisionCheckSpec(
        requirement_id="training_scenarios", status="withdrawn", model_paths=[],
        source_correction_ids=["resolve_training"],
        rationale="The original text resolves the alleged ambiguity.",
    )
    assert check.status == "withdrawn"
    assert check.model_paths == []


def test_legacy_untyped_revision_is_not_silently_treated_as_a_model_defect():
    payload = deepcopy(_source_requirement())
    payload.pop("kind")
    payload.pop("basis_refs")
    with pytest.raises(ValueError, match="kind"):
        RevisionRequirementSpec.model_validate(payload)


def _resolved_review_case(tmp_path):
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import ModelIR
    from app.guidance.recovery import canonical_digest
    from app.guidance.source_resolution import SourceResolutionRecord
    from test_guidance_source_resolution import _case, _correction as correction_fixture

    before, catalog, profile = _case(tmp_path)
    correction = correction_fixture()
    payload = before.model_dump(mode="json")
    payload["uncertainties"] = [item for item in payload["uncertainties"] if item["uncertainty_id"] != "UNC001"]
    payload["locked_facts"].append(correction.replacement_fact.model_dump(mode="json"))
    for subproblem in payload["subproblems"]:
        if subproblem["id"] in ["sp1", "sp2"]:
            subproblem["required_uncertainty_ids"] = []
            subproblem["required_fact_ids"].append("F_CHARGE_BASIS")
    current = ProblemSpec.model_validate(payload)
    record = SourceResolutionRecord(
        prior_problem_spec_sha256=canonical_digest(before), resolved_problem_spec_sha256=canonical_digest(current),
        corrections=[correction], affected_subproblem_ids=["sp1", "sp2", "downstream"],
        source_evidence_sha256={catalog["blocks"][0]["source_ref"]: catalog["blocks"][0]["sha256"]},
    )
    model_data = json.loads((Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json").read_text(encoding="utf-8"))
    model_data["subproblems"][0]["subproblem_id"] = "sp1"
    model = ModelIR.model_validate(model_data).subproblems[0]
    requested = SubproblemReviewSpec(
        subproblem_id="sp1", status="revision_required", required_revisions=[{
            **_source_requirement(), "basis_refs": ["problem_spec.json#uncertainties/UNC001"],
        }],
    )
    decision = SubproblemReviewSpec(
        subproblem_id="sp1", status="approved", revision_checks=[{
            "requirement_id": "training_scenarios", "status": "withdrawn", "model_paths": [],
            "source_correction_ids": [correction.correction_id],
            "rationale": "The applied source adjudication removed the false ambiguity.",
        }],
    )
    return current, record, model, requested, decision, catalog, profile


def test_applied_source_resolution_can_discharge_the_exact_erroneous_obligation(tmp_path):
    from app.guidance.model_review_validation import validate_subproblem_review

    spec, record, model, requested, decision, _, _ = _resolved_review_case(tmp_path)
    validate_subproblem_review(decision, model, revision_request=requested, problem_spec=spec, source_resolution=record)


def test_withdrawal_without_applied_source_resolution_is_rejected(tmp_path):
    from app.guidance.model_review_validation import validate_subproblem_review

    spec, _, model, requested, decision, _, _ = _resolved_review_case(tmp_path)
    with pytest.raises(ValueError, match="applied source resolution"):
        validate_subproblem_review(decision, model, revision_request=requested, problem_spec=spec)


def test_actual_model_defect_cannot_be_withdrawn_by_changing_its_source(tmp_path):
    from app.guidance.model_review_validation import validate_subproblem_review

    spec, record, model, requested, decision, _, _ = _resolved_review_case(tmp_path)
    requested.required_revisions[0].kind = "model"
    with pytest.raises(ValueError, match="model defect"):
        validate_subproblem_review(decision, model, revision_request=requested, problem_spec=spec, source_resolution=record)


def test_withdrawal_cannot_cite_an_unapplied_correction(tmp_path):
    from app.guidance.model_review_validation import validate_subproblem_review

    spec, record, model, requested, decision, _, _ = _resolved_review_case(tmp_path)
    decision.revision_checks[0].source_correction_ids = ["fabricated"]
    with pytest.raises(ValueError, match="unknown source correction"):
        validate_subproblem_review(decision, model, revision_request=requested, problem_spec=spec, source_resolution=record)


def test_source_obligation_cannot_be_falsely_accepted_as_a_model_repair(tmp_path):
    from app.guidance.model_review_validation import validate_subproblem_review

    spec, record, model, requested, decision, _, _ = _resolved_review_case(tmp_path)
    decision.revision_checks = [RevisionCheckSpec(
        requirement_id="training_scenarios", status="accepted", model_paths=["/objective"],
        rationale="Pretend the wrong source requirement was fulfilled in the model.",
    )]
    with pytest.raises(ValueError, match="source revision"):
        validate_subproblem_review(decision, model, revision_request=requested, problem_spec=spec, source_resolution=record)


def test_review_binding_changes_when_source_resolution_evidence_changes(tmp_path):
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_review_contracts import ModelIRReview
    from app.guidance.model_review_validation import bind_model_review

    spec, record, model, requested, decision, catalog, profile = _resolved_review_case(tmp_path)
    model_ir = ModelIR(model_id="source-binding", subproblems=[model])
    args = dict(
        review=ModelIRReview(model_id=model_ir.model_id, subproblem_reviews=[decision]),
        model_ir=model_ir, approved_model_ir=model_ir, problem_spec=spec,
        input_catalog=catalog, evidence_profile=profile,
        revision_request=ModelIRReview(model_id=model_ir.model_id, subproblem_reviews=[requested]),
    )
    original = bind_model_review(**args, source_resolution=record)
    changed = record.model_copy(deep=True)
    changed.corrections[0].rationale = "A different adjudication."
    assert original.source_resolution_sha256 is not None
    assert bind_model_review(**args, source_resolution=changed) != original
