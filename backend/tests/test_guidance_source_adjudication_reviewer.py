"""独立来源许可先于数学审核，拒绝范围不能被后续审核重新批准。"""

import json

import pytest

from app.guidance.model_review_contracts import ModelReviewBinding
from app.guidance.model_review_validation import unresolved_source_subproblem_ids
from test_guidance_source_reviewer import SourceReviewLLM, _prepare, _review


@pytest.mark.parametrize("status", ["contradicted", "not_assessed", "supported"])
def test_math_reviewer_only_receives_subproblems_with_independent_source_permission(tmp_path, status):
    spec, model, record, catalog, profile = _prepare(tmp_path, first_check=status)
    original = (tmp_path / "source_preflight.json").read_bytes()
    llm = SourceReviewLLM()
    review = _review(tmp_path, llm, spec, model, catalog, profile)
    assert all("target_subproblem_id" in item for item in llm.payloads)
    assert (tmp_path / "source_preflight.json").read_bytes() == original
    if status == "supported":
        assert review.status == "approved"
        assert [item["target_subproblem_id"] for item in llm.payloads] == ["sp1", "sp2", "downstream", "independent"]
        assert all(item.mathematical_checks for item in review.subproblem_reviews)
    else:
        assert record.blocked_subproblem_ids == ["sp1", "sp2", "downstream"]
        assert [item["target_subproblem_id"] for item in llm.payloads] == ["independent"]
        for item in review.subproblem_reviews[:-1]:
            assert item.status == "blocked" and item.assessment_status == "not_assessed"
            assert item.mathematical_checks == item.required_revisions == item.revision_checks == []
        assert review.subproblem_reviews[-1].status == "approved"
        assert review.executable_subproblem_ids == ["independent"]


def test_legacy_review_binding_without_complete_preflight_is_invalid():
    payload = {name: "0" * 64 for name in [
        "model_sha256", "approved_model_sha256", "problem_spec_sha256", "evidence_profile_sha256",
        "input_catalog_sha256", "review_sha256", "revision_request_sha256",
    ]}
    with pytest.raises(ValueError, match="source_preflight_sha256"):
        ModelReviewBinding.model_validate(payload)


@pytest.mark.parametrize("failure_stage", ["interpretation", "adjudication"])
def test_failed_source_provider_does_not_fabricate_math_review_or_open_model_requests(tmp_path, failure_stage):
    failure = {f"{failure_stage}_failure": "source provider unavailable"}
    spec, model, record, catalog, profile = _prepare(tmp_path, **failure)
    original = (tmp_path / "source_preflight.json").read_bytes()
    llm = SourceReviewLLM()
    review = _review(tmp_path, llm, spec, model, catalog, profile)
    assert review.status == "blocked" and review.executable_subproblem_ids == []
    assert llm.payloads == []
    assert all(item.assessment_status == "not_assessed" and not item.mathematical_checks
               for item in review.subproblem_reviews)
    assert "source provider unavailable" in getattr(record, f"{failure_stage}_failure_reason")
    assert (tmp_path / "source_preflight.json").read_bytes() == original


def test_final_review_new_source_conflict_preserves_the_preflight_and_does_not_reassess(tmp_path):
    spec, model, _, catalog, profile = _prepare(tmp_path)
    initial = _review(tmp_path, SourceReviewLLM(revisions=1), spec, model, catalog, profile)
    permission = (tmp_path / "source_preflight.json").read_bytes()
    llm = SourceReviewLLM(conflict={
        "basis_refs": ["problem_spec.json#locked_facts/F_COST"],
        "rationale": "A new discrepancy in the charging basis was discovered after modeling.",
    })
    final = _review(tmp_path, llm, spec, model, catalog, profile, initial)
    first = final.subproblem_reviews[0]
    assert first.status == "blocked" and first.assessment_status == "reviewed"
    assert first.source_conflicts and first.revision_checks[0].status == "accepted"
    assert all("target_subproblem_id" in item for item in llm.payloads)
    assert (tmp_path / "source_preflight.json").read_bytes() == permission
    assert unresolved_source_subproblem_ids(final, spec) == {"sp1", "sp2", "downstream"}
