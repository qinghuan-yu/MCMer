"""The semantic permit must survive revision and be required at application/recovery."""

import asyncio
import json

import pytest

from app.artifacts.evidence_profile import EvidenceProfile
from app.guidance.contracts import ProblemSpec
from app.guidance.model_review_contracts import ApprovedModelIR, ModelReviewSubmission
from app.guidance.pipeline import _preserve_revision_request
from app.guidance.recovery import (
    canonical_digest,
    invalidate_stage_outputs,
    load_source_adjudication,
    load_source_resolution,
)
from test_guidance_source_revision_flow import _case, _write


def test_pipeline_cannot_apply_a_source_proposal_without_semantic_permission(tmp_path):
    spec, model, review, _ = _case(tmp_path)
    original = _write(tmp_path, spec, model, review, adjudication=False)

    with pytest.raises(ValueError, match="adjudication|semantic|permission"):
        _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")

    assert (tmp_path / "problem_spec.json").read_bytes() == original
    assert not (tmp_path / "problem_spec_initial.json").exists()
    assert not (tmp_path / "source_resolution.json").exists()


def test_new_decomposition_discards_permission_but_revision_preserves_it(tmp_path):
    permit = tmp_path / "source_adjudication.json"
    original = '{"immutable": "initial source decision"}\n'.encode("utf-8")
    permit.write_bytes(original)

    invalidate_stage_outputs(tmp_path, "modeling")
    assert permit.read_bytes() == original
    invalidate_stage_outputs(tmp_path, "model_review")
    assert permit.read_bytes() == original
    invalidate_stage_outputs(tmp_path, "decomposition")
    assert not permit.exists()


def _permission(root, spec, review, catalog, status):
    from app.guidance.source_adjudication import (
        SourceAdjudicationResult,
        bind_source_adjudication,
    )

    proposals = [
        (decision.subproblem_id, correction)
        for decision in review.subproblem_reviews for correction in decision.source_corrections
    ]
    result = SourceAdjudicationResult.model_validate({"checks": [{
        "correction_id": correction.correction_id,
        "status": status,
        "evidence": [item.model_dump(mode="json") for item in correction.evidence],
        "rationale": "Independent semantic assessment of the complete proposed fact.",
    } for _, correction in proposals]})
    record = bind_source_adjudication(
        problem_spec=spec, proposals=proposals, input_catalog=catalog,
        evidence_profile={"evidence": []}, result=result, work_dir=root,
    )
    (root / "source_adjudication.json").write_text(
        record.model_dump_json(indent=2), encoding="utf-8", newline="\n",
    )
    return record


def _initial_approval(root, spec, model, review, catalog, record):
    from app.guidance.model_review_validation import bind_model_review
    from app.guidance.stages import _build_reviewed_model_projection

    projection = _build_reviewed_model_projection(model, review, spec)
    approved = ApprovedModelIR(
        review_status="partial", model_id=model.model_id,
        executable_subproblem_ids=["independent"], approved_model_ir=projection,
        review_binding=bind_model_review(
            review=review, model_ir=model, approved_model_ir=projection,
            problem_spec=spec, evidence_profile=EvidenceProfile().model_dump(mode="json"), input_catalog=catalog,
            revision_request=None, source_adjudication=record,
        ),
    )
    (root / "approved_model_ir.json").write_text(approved.model_dump_json(), encoding="utf-8")


@pytest.mark.parametrize("change", ["missing_approval", "missing_binding", "permission", "review", "model", "projection"])
def test_application_requires_the_unchanged_initial_review_binding(tmp_path, change):
    spec, model, review, catalog = _case(tmp_path)
    original = _write(tmp_path, spec, model, review, adjudication=False)
    record = _permission(tmp_path, spec, review, catalog, "supported")
    _initial_approval(tmp_path, spec, model, review, catalog, record)
    if change == "missing_approval":
        (tmp_path / "approved_model_ir.json").unlink()
    else:
        filename = {
            "missing_binding": "approved_model_ir.json",
            "permission": "source_adjudication.json", "review": "model_review.json",
            "model": "model_ir.json", "projection": "approved_model_ir.json",
        }[change]
        path = tmp_path / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        if change == "missing_binding":
            payload["review_binding"] = None
        elif change == "permission":
            payload["checks"][0]["rationale"] = "A later semantic interpretation replaced the initial assessment."
            payload["binding_sha256"] = canonical_digest({
                key: value for key, value in payload.items() if key != "binding_sha256"
            })
        elif change == "review":
            payload["subproblem_reviews"][1]["required_revisions"][0]["description"] = "A different revision request."
        elif change == "model":
            payload["subproblems"][0]["objective"] = "A changed mathematical objective."
        else:
            payload["approved_model_ir"]["subproblems"][0]["blocking_reason"] = "A changed execution projection."
        path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="binding|permission|snapshot"):
        _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")

    assert (tmp_path / "problem_spec.json").read_bytes() == original
    assert not (tmp_path / "problem_spec_initial.json").exists()
    assert not (tmp_path / "source_resolution.json").exists()


def test_rejected_permission_cannot_disappear_before_revision_even_when_no_proposal_will_apply(tmp_path):
    spec, model, review, catalog = _case(tmp_path)
    record = _permission(tmp_path, spec, review, catalog, "contradicted")
    review.subproblem_reviews[1].source_corrections = []
    original = _write(tmp_path, spec, model, review, adjudication=False)
    _initial_approval(tmp_path, spec, model, review, catalog, record)
    (tmp_path / "source_adjudication.json").unlink()

    with pytest.raises(ValueError, match="binding|permission|snapshot"):
        _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")

    assert (tmp_path / "problem_spec.json").read_bytes() == original
    assert not (tmp_path / "problem_spec_initial.json").exists()


@pytest.mark.parametrize("status", ["contradicted", "not_assessed"])
def test_pipeline_rejects_explicitly_unsupported_proposals_before_preserving_snapshots(tmp_path, status):
    spec, model, review, catalog = _case(tmp_path)
    original = _write(tmp_path, spec, model, review, adjudication=False)
    record = _permission(tmp_path, spec, review, catalog, status)
    _initial_approval(tmp_path, spec, model, review, catalog, record)

    with pytest.raises(ValueError, match="supported|adjudication|permission"):
        _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")

    assert (tmp_path / "problem_spec.json").read_bytes() == original
    assert not (tmp_path / "problem_spec_initial.json").exists()
    assert not (tmp_path / "source_resolution.json").exists()


def test_supported_application_recovers_against_the_original_problem_and_keeps_permission_bytes(tmp_path):
    spec, model, review, catalog = _case(tmp_path)
    _write(tmp_path, spec, model, review, adjudication=False)
    record = _permission(tmp_path, spec, review, catalog, "supported")
    _initial_approval(tmp_path, spec, model, review, catalog, record)
    permit_bytes = (tmp_path / "source_adjudication.json").read_bytes()

    _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    current = ProblemSpec.model_validate_json((tmp_path / "problem_spec.json").read_bytes())
    assert current != spec
    assert load_source_adjudication(tmp_path, current) == record
    resolution = load_source_resolution(tmp_path, current)
    assert resolution.source_adjudication_sha256 == canonical_digest(record)
    assert (tmp_path / "source_adjudication.json").read_bytes() == permit_bytes

    with pytest.raises(ValueError, match="one revision"):
        _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")


@pytest.mark.parametrize("change", ["missing", "verdict", "prior_problem", "source_file"])
def test_applied_resolution_cannot_recover_with_missing_or_changed_permission(tmp_path, change):
    spec, model, review, catalog = _case(tmp_path)
    _write(tmp_path, spec, model, review, adjudication=False)
    record = _permission(tmp_path, spec, review, catalog, "supported")
    _initial_approval(tmp_path, spec, model, review, catalog, record)
    _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    current = ProblemSpec.model_validate_json((tmp_path / "problem_spec.json").read_bytes())
    permit = tmp_path / "source_adjudication.json"
    if change == "missing":
        permit.unlink()
    elif change == "verdict":
        payload = json.loads(permit.read_text(encoding="utf-8"))
        payload["checks"][0]["status"] = "contradicted"
        permit.write_text(json.dumps(payload), encoding="utf-8")
    elif change == "prior_problem":
        prior = spec.model_copy(update={"task_summary": "A different source interpretation."})
        (tmp_path / "problem_spec_initial.json").write_text(prior.model_dump_json(), encoding="utf-8")
    else:
        (tmp_path / "question.txt").write_text("Changed source bytes.", encoding="utf-8")

    with pytest.raises(ValueError, match="source|adjudication|snapshot|hash|supported|permission"):
        load_source_resolution(tmp_path, current)


def test_review_stage_binds_a_newly_written_rejection_and_preserves_the_independent_branch(tmp_path):
    from app.guidance.stages import ModelReviewStage

    spec, model, review, catalog = _case(tmp_path)
    _write(tmp_path, spec, model, review, adjudication=False)
    record = None

    class Reviewer:
        async def review(self, **kwargs):
            nonlocal record
            record = _permission(tmp_path, spec, review, catalog, "contradicted")
            result = review.model_copy(deep=True)
            result.subproblem_reviews[1].source_corrections = []
            return result

    submission = tmp_path / "model_review_submission.json"
    submission.write_text(ModelReviewSubmission(model_ir_ref="model_ir.json").model_dump_json(), encoding="utf-8")
    output = tmp_path / "approved_model_ir.json"
    asyncio.run(ModelReviewStage(reviewer=Reviewer()).run(
        task_id="rejected-source", task={}, input_path=submission, output_path=output,
    ))

    approved = ApprovedModelIR.model_validate_json(output.read_bytes())
    assert approved.review_binding.source_adjudication_sha256 == canonical_digest(record)
    assert approved.review_binding.source_resolution_sha256 is None
    assert approved.executable_subproblem_ids == ["independent"]
    assert approved.approved_model_ir.subproblems[-1] == model.subproblems[-1]
    assert not (tmp_path / "source_resolution.json").exists()
