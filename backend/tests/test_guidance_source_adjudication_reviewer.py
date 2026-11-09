import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import ModelReviewBinding
from app.guidance.model_review_validation import unresolved_source_subproblem_ids
from app.guidance.reviewer import LLMModelReviewer
from test_guidance_source_resolution import _case, _correction
from test_guidance_source_reviewer import _decision


def _inputs(root):
    spec, catalog, profile = _case(root)
    base = json.loads((Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json").read_text(encoding="utf-8"))["subproblems"][0]
    models = []
    for item in spec.subproblems:
        model = deepcopy(base)
        model["subproblem_id"] = item.id
        models.append(model)
    for name, value in [("input_catalog.json", catalog), ("evidence_profile.json", profile)]:
        (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return spec, ModelIR(model_id="semantic-gate", subproblems=models), catalog, profile


class AdjudicatedReviewLLM:
    model = "test-independent-source-review"
    temperature = 0
    max_tokens = 100

    def __init__(self, status, *, fail=False):
        self.status = status
        self.fail = fail
        self.payloads = []
        self.correction = _correction()
        if status != "supported":
            self.correction.replacement_fact.value = "trainees_only"
            self.correction.replacement_fact.statement = "Only trainees incur training costs."

    async def chat(self, **kwargs):
        payload = json.loads(kwargs["history"][1]["content"])
        payload = payload.get("original_input", payload)
        self.payloads.append(payload)
        if "target_subproblem_id" in payload:
            decision = _decision(payload["target_subproblem_id"], payload["mathematical_requirements"])
            if payload["target_subproblem_id"] == "sp1":
                decision.update(status="revision_required", source_corrections=[self.correction.model_dump(mode="json")])
        else:
            if self.fail:
                raise RuntimeError("semantic provider unavailable")
            decision = {"checks": [{
                "correction_id": self.correction.correction_id, "status": self.status,
                "evidence": [item.model_dump(mode="json") for item in self.correction.evidence],
                "rationale": "Compare both charged groups in the original sentence with the proposed relation.",
            }]}
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(decision)), finish_reason="stop",
        )])


@pytest.mark.parametrize("status", ["contradicted", "not_assessed", "supported"])
def test_reviewer_independently_checks_each_proposal_and_preserves_unresolved_scope(tmp_path, status):
    spec, model, catalog, profile = _inputs(tmp_path)
    llm = AdjudicatedReviewLLM(status)
    review = asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=spec, model_ir=model, input_catalog=catalog, evidence_profile=profile,
        task={}, work_dir=tmp_path,
    ))
    semantic = [item for item in llm.payloads if "target_subproblem_id" not in item]
    assert len(semantic) == 1
    assert "subproblem_model" not in semantic[0] and "dependency_models" not in semantic[0]
    assert "revision_requirements" not in semantic[0]
    record = json.loads((tmp_path / "source_adjudication.json").read_text(encoding="utf-8"))
    assert record["checks"][0]["status"] == status
    first = review.subproblem_reviews[0]
    assert first.assessment_status == "reviewed"
    assert first.mathematical_checks
    if status == "supported":
        assert first.source_corrections == [llm.correction]
    else:
        assert first.source_corrections == []
        assert first.required_revisions[-1].kind == "source"
        assert first.required_revisions[-1].basis_refs == ["problem_spec.json#uncertainties/UNC001"]
    assert unresolved_source_subproblem_ids(review, spec) == {"sp1", "sp2", "downstream"}
    assert review.subproblem_reviews[-1].status == "approved"
    assert not (tmp_path / "source_resolution.json").exists()


def test_legacy_review_binding_without_semantic_evidence_field_is_invalid():
    payload = {name: "0" * 64 for name in [
        "model_sha256", "approved_model_sha256", "problem_spec_sha256", "evidence_profile_sha256",
        "input_catalog_sha256", "review_sha256", "revision_request_sha256", "source_resolution_sha256",
    ]}
    with pytest.raises(ValueError, match="source_adjudication_sha256"):
        ModelReviewBinding.model_validate(payload)


def test_semantic_provider_failure_preserves_math_review_and_unresolved_source(tmp_path):
    spec, model, catalog, profile = _inputs(tmp_path)
    llm = AdjudicatedReviewLLM("contradicted", fail=True)
    review = asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=spec, model_ir=model, input_catalog=catalog, evidence_profile=profile,
        task={}, work_dir=tmp_path,
    ))
    record = json.loads((tmp_path / "source_adjudication.json").read_text(encoding="utf-8"))
    assert record["assessment_status"] == "not_assessed" and record["checks"] == []
    assert "semantic provider unavailable" in record["failure_reason"]
    assert review.subproblem_reviews[0].source_corrections == []
    assert review.subproblem_reviews[0].mathematical_checks
    assert unresolved_source_subproblem_ids(review, spec) == {"sp1", "sp2", "downstream"}
    assert review.subproblem_reviews[-1].status == "approved"


def test_final_review_does_not_reassess_new_proposals_or_overwrite_initial_permission(tmp_path):
    spec, model, catalog, profile = _inputs(tmp_path)
    args = dict(problem_spec=spec, model_ir=model, input_catalog=catalog, evidence_profile=profile,
                task={}, work_dir=tmp_path)
    initial = asyncio.run(LLMModelReviewer(AdjudicatedReviewLLM("supported")).review(**args))
    permission = (tmp_path / "source_adjudication.json").read_bytes()
    final_llm = AdjudicatedReviewLLM("not_assessed")
    final = asyncio.run(LLMModelReviewer(final_llm).review(**args, revision_request=initial))
    assert all("target_subproblem_id" in item for item in final_llm.payloads)
    assert (tmp_path / "source_adjudication.json").read_bytes() == permission
    assert final.status == "revision_required"
    assert unresolved_source_subproblem_ids(final, spec) == {"sp1", "sp2", "downstream"}
