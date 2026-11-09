import asyncio
import json

from app.guidance.model_review_validation import unresolved_source_subproblem_ids
from app.guidance.reviewer import LLMModelReviewer
from test_guidance_source_adjudication_reviewer import AdjudicatedReviewLLM, _inputs
from test_guidance_source_resolution import _correction
from test_guidance_source_reviewer import SourceReviewLLM, _fixture, _review


def _cost_correction(spec):
    return _correction(
        correction_id="confirm_cost", target_kind="locked_fact", target_id="F_COST",
        replacement_fact=spec.locked_facts[0].model_dump(mode="json"),
        evidence=[{"source_ref": "cost.csv", "quote": "training\t8"}],
    )


class FullRevisionReviewLLM(AdjudicatedReviewLLM):
    async def chat(self, **kwargs):
        response = await super().chat(**kwargs)
        decision = json.loads(response.choices[0].message.content)
        if decision.get("subproblem_id") == "sp1":
            decision["required_revisions"] = [{
                "requirement_id": f"model_requirement_{index}", "kind": "model", "basis_refs": [],
                "description": f"Retain independently identified model requirement {index}.",
                "acceptance_criteria": f"Verify the corresponding mathematical change {index}.",
            } for index in range(50)]
            self.original_requirements = decision["required_revisions"]
            response.choices[0].message.content = json.dumps(decision)
        return response


def test_semantic_rejection_at_revision_capacity_preserves_independent_results(tmp_path):
    spec, model, catalog, profile = _inputs(tmp_path)
    llm = FullRevisionReviewLLM("contradicted")

    review = asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=spec, model_ir=model, input_catalog=catalog, evidence_profile=profile,
        task={}, work_dir=tmp_path,
    ))

    assert review.subproblem_reviews[-1].status == "approved"
    assert unresolved_source_subproblem_ids(review, spec) == {"sp1", "sp2", "downstream"}
    assert review.subproblem_reviews[0].status != "approved"
    assert review.subproblem_reviews[0].source_corrections == []
    requirements = review.subproblem_reviews[0].required_revisions
    assert [item.model_dump(mode="json") for item in requirements if item.kind == "model"] == llm.original_requirements
    source_requirements = [item for item in requirements if item.kind == "source"]
    assert len(source_requirements) == 1
    assert source_requirements[0].basis_refs == ["problem_spec.json#uncertainties/UNC001"]
    assert review.subproblem_reviews[0].assessment_status == "reviewed"
    assert review.subproblem_reviews[0].mathematical_checks
    assert not (tmp_path / "source_resolution.json").exists()


class MixedSourceReviewLLM(AdjudicatedReviewLLM):
    def __init__(self, supported):
        super().__init__("contradicted")
        self.supported = supported

    async def chat(self, **kwargs):
        response = await super().chat(**kwargs)
        decision = json.loads(response.choices[0].message.content)
        if decision.get("subproblem_id") == "sp1":
            decision["source_corrections"].append(self.supported.model_dump(mode="json"))
        elif "checks" in decision:
            decision["checks"].append({
                "correction_id": self.supported.correction_id, "status": "supported",
                "evidence": [item.model_dump(mode="json") for item in self.supported.evidence],
                "rationale": "The original table gives the proposed training unit cost.",
            })
        response.choices[0].message.content = json.dumps(decision)
        return response


def test_supported_correction_cannot_clear_another_rejected_source_of_the_same_owner(tmp_path):
    from app.guidance.source_adjudication import SourceAdjudicationRecord, supported_source_proposals
    from app.guidance.source_resolution import resolve_problem_sources

    spec, model, catalog, profile = _inputs(tmp_path)
    supported = _cost_correction(spec)
    review = _review(tmp_path, MixedSourceReviewLLM(supported), spec, model, catalog, profile)
    record = SourceAdjudicationRecord.model_validate_json(
        (tmp_path / "source_adjudication.json").read_text(encoding="utf-8")
    )
    assert review.subproblem_reviews[0].source_corrections == [supported]
    assert supported_source_proposals(record) == [("sp1", supported)]
    resolved, resolution = resolve_problem_sources(
        spec, supported_source_proposals(record), adjudication=record,
        input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
    )
    assert unresolved_source_subproblem_ids(review, resolved, resolution) == {"sp1", "sp2", "downstream"}
    assert review.subproblem_reviews[-1].status == "approved"


class FinalProposalReviewLLM(SourceReviewLLM):
    def __init__(self, proposal):
        super().__init__()
        self.proposal = proposal

    async def chat(self, **kwargs):
        response = await super().chat(**kwargs)
        decision = json.loads(response.choices[0].message.content)
        if decision.get("subproblem_id") == "sp1":
            decision.update(status="revision_required", source_corrections=[self.proposal.model_dump(mode="json")])
            response.choices[0].message.content = json.dumps(decision)
        return response


def test_final_review_new_proposal_preserves_initial_semantic_permission_bytes(tmp_path):
    spec, model, revision, catalog, profile = _fixture(tmp_path, resolved=True)
    original = (tmp_path / "source_adjudication.json").read_bytes()
    llm = FinalProposalReviewLLM(_cost_correction(spec))

    review = _review(tmp_path, llm, spec, model, catalog, profile, revision)

    assert review.subproblem_reviews[0].source_corrections == [llm.proposal]
    assert review.subproblem_reviews[0].status == "revision_required"
    assert llm.adjudication_payloads == []
    assert (tmp_path / "source_adjudication.json").read_bytes() == original
