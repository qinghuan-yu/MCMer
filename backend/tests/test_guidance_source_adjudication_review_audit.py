"""来源冲突与大量真实数学义务并存时，既不漏许可也不丢缺陷。"""

import json

from app.guidance.contracts import ProblemSpec
from app.guidance.model_review_validation import unresolved_source_subproblem_ids
from app.guidance.source_resolution import resolve_problem_sources
from app.guidance.stages import _build_reviewed_model_projection
from test_guidance_source_preflight import _checks, _decisions
from test_guidance_source_resolution import SOURCE, _case
from test_guidance_source_reviewer import SourceReviewLLM, _models, _prepare, _review, _save_preflight


def test_late_source_conflict_preserves_fifty_independent_math_requirements(tmp_path):
    spec, model, _, catalog, profile = _prepare(tmp_path)
    llm = SourceReviewLLM(revisions=50, conflict={
        "basis_refs": ["problem_spec.json#locked_facts/F_COST"],
        "rationale": "A newly discovered source conflict needs a separate future source decision.",
    })
    review = _review(tmp_path, llm, spec, model, catalog, profile)
    first = review.subproblem_reviews[0]
    assert first.status == "blocked" and first.assessment_status == "reviewed"
    assert len(first.required_revisions) == 50
    assert [item.requirement_id for item in first.required_revisions] == [
        f"model_requirement_{index}" for index in range(50)
    ]
    assert all(item.kind == "model" for item in first.required_revisions)
    assert first.source_conflicts and first.mathematical_checks
    assert unresolved_source_subproblem_ids(review, spec) == {"sp1", "sp2", "downstream"}
    assert review.subproblem_reviews[-1].status == "approved"


def test_supported_correction_cannot_clear_another_rejected_source_of_the_same_owner(tmp_path):
    original, catalog, profile = _case(tmp_path)
    payload = original.model_dump(mode="json")
    payload["uncertainties"].append({
        "uncertainty_id": "UNC_IDLE", "statement": "Claimed ambiguity about idle maintenance.",
        "source_refs": [SOURCE], "subproblem_ids": ["sp1"], "semantic_key": "idle_maintenance",
        "candidate_values": ["every_idle_period", "never"], "unit": "",
    })
    payload["subproblems"][0]["required_uncertainty_ids"].append("UNC_IDLE")
    original = ProblemSpec.model_validate(payload)
    interpretation = _decisions(original)
    interpretation.decisions[-1].rationale = "An unsupported claim of ambiguity in idle maintenance."
    checks = _checks(interpretation)
    checks.checks[-1].status = "contradicted"
    checks.checks[-1].rationale = "The original source explicitly requires idle skilled modules to be maintained."
    spec, record = resolve_problem_sources(
        original, interpretation=interpretation, adjudication=checks,
        input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
    )
    _save_preflight(tmp_path, spec, record, catalog, profile)
    model = _models(spec)
    llm = SourceReviewLLM()
    review = _review(tmp_path, llm, spec, model, catalog, profile)
    assert "F_CHARGE_BASIS" in [item.fact_id for item in spec.locked_facts]
    assert "UNC_IDLE" in [item.uncertainty_id for item in spec.uncertainties]
    assert record.blocked_subproblem_ids == ["sp1", "sp2", "downstream"]
    assert [item["target_subproblem_id"] for item in llm.payloads] == ["independent"]
    assert review.executable_subproblem_ids == ["independent"]
    assert all(item.assessment_status == "not_assessed" for item in review.subproblem_reviews[:-1])


def test_final_review_preserves_all_model_obligations_beyond_fifty(tmp_path):
    spec, model, _, catalog, profile = _prepare(tmp_path)
    initial = _review(tmp_path, SourceReviewLLM(revisions=51), spec, model, catalog, profile)
    permission = (tmp_path / "source_preflight.json").read_bytes()
    llm = SourceReviewLLM()
    final = _review(tmp_path, llm, spec, model, catalog, profile, initial)
    assert final.subproblem_reviews[0].status == "approved"
    checks = final.subproblem_reviews[0].revision_checks
    assert [item.requirement_id for item in checks] == [
        item.requirement_id for item in initial.subproblem_reviews[0].required_revisions
    ]
    assert len(checks) == 51 and all(item.status == "accepted" for item in checks)
    assert (tmp_path / "source_preflight.json").read_bytes() == permission
