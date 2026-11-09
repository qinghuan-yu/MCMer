import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.model_review_contracts import ApprovedModelIR
from app.guidance.model_review_validation import bind_model_review
from app.guidance.reviewer import LLMModelReviewer
from app.guidance.source_resolution import resolve_problem_sources
from test_guidance_source_resolution import _case, _correction


def _decision(identifier, requirements):
    return {
        "subproblem_id": identifier, "status": "approved",
        "mathematical_checks": [{
            "requirement_id": item, "status": "passed",
            "model_paths": ["/execution_components/0/model/objective"],
            "rationale": "Check the explicit typed model against the supplied source requirement.",
        } for item in requirements],
    }


class SourceReviewLLM:
    model = "test-source-review"
    temperature = 0
    max_tokens = 100

    def __init__(self, *, correction=None):
        self.correction = correction
        self.payloads = []

    async def chat(self, **kwargs):
        payload = json.loads(kwargs["history"][1]["content"])
        payload = payload.get("original_input", payload)
        self.payloads.append(payload)
        decision = _decision(payload["target_subproblem_id"], payload["mathematical_requirements"])
        if self.correction is not None and payload["target_subproblem_id"] == "sp1":
            decision.update(status="revision_required", source_corrections=[self.correction.model_dump(mode="json")])
        else:
            evidence = payload.get("source_resolution")
            decision["revision_checks"] = [{
                "requirement_id": item["requirement_id"],
                "status": "withdrawn" if evidence else "accepted",
                "model_paths": [] if evidence else ["/objective"],
                "source_correction_ids": [entry["correction_id"] for entry in evidence["corrections"]] if evidence else [],
                "rationale": "Resolve the original charging basis with the registered source adjudication.",
            } for item in payload["revision_requirements"]]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(decision)), finish_reason="stop",
        )])


def _fixture(root, *, resolved):
    before, catalog, profile = _case(root)
    correction = _correction()
    current, record = resolve_problem_sources(
        before, [("sp1", correction)], input_catalog=catalog, evidence_profile=profile, work_dir=root,
    )
    base = json.loads((Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json").read_text(encoding="utf-8"))["subproblems"][0]
    models = []
    for spec in before.subproblems:
        subproblem = deepcopy(base)
        subproblem["subproblem_id"] = spec.id
        models.append(subproblem)
    model = ModelIR(model_id="source-review", subproblems=models)
    decisions = [_decision(item.id, [*item.required_fact_ids, "$objective", "$formulation"]) for item in before.subproblems]
    decisions[0].update(status="revision_required", source_corrections=[correction.model_dump(mode="json")], required_revisions=[{
        "requirement_id": "training_basis", "kind": "source",
        "basis_refs": ["problem_spec.json#uncertainties/UNC001"],
        "description": "Resolve the falsely disputed training charge.",
        "acceptance_criteria": "Use the original text for both charged groups.",
    }])
    revision = ModelIRReview(model_id=model.model_id, model_ir_ref="model_ir_initial.json", subproblem_reviews=decisions)
    files = {"input_catalog.json": catalog, "evidence_profile.json": profile}
    if resolved:
        from app.guidance.stages import _build_reviewed_model_projection

        original_review = revision.model_copy(update={"model_ir_ref": "model_ir.json"})
        projection = _build_reviewed_model_projection(model, original_review, before)
        approved = ApprovedModelIR(
            model_id=model.model_id, review_status="partial", approved_model_ir=projection,
            executable_subproblem_ids=["independent"],
            review_binding=bind_model_review(
                review=original_review, model_ir=model, approved_model_ir=projection,
                problem_spec=before, input_catalog=catalog, evidence_profile=profile, revision_request=None,
            ),
        )
        files.update({
            "problem_spec_initial.json": before.model_dump(mode="json"),
            "problem_spec.json": current.model_dump(mode="json"),
            "source_resolution.json": record.model_dump(mode="json"),
            "model_ir_initial.json": model.model_dump(mode="json"),
            "approved_model_ir_initial.json": approved.model_dump(mode="json"),
        })
    for name, document in files.items():
        (root / name).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return current if resolved else before, model, revision, catalog, profile


def _review(root, llm, spec, model, catalog, profile, revision=None):
    return asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=spec, model_ir=model, revision_request=revision,
        input_catalog=catalog, evidence_profile=profile, task={}, work_dir=root,
    ))


def test_reviewer_accepts_a_source_correction_only_after_checking_the_original(tmp_path):
    spec, model, _, catalog, profile = _fixture(tmp_path, resolved=False)
    result = _review(tmp_path, SourceReviewLLM(correction=_correction()), spec, model, catalog, profile)
    assert result.subproblem_reviews[0].assessment_status == "reviewed"
    assert result.subproblem_reviews[0].status == "revision_required"
    assert result.subproblem_reviews[0].source_corrections[0].replacement_fact.value == "trainees_and_instructors"


def test_final_reviewer_receives_applied_adjudication_and_can_withdraw_source_obligation(tmp_path):
    spec, model, revision, catalog, profile = _fixture(tmp_path, resolved=True)
    llm = SourceReviewLLM()
    result = _review(tmp_path, llm, spec, model, catalog, profile, revision)
    assert "source_resolution" in llm.payloads[0]
    assert llm.payloads[0]["source_resolution"]["corrections"][0]["target_id"] == "UNC001"
    assert result.subproblem_reviews[0].status == "approved"
    assert result.subproblem_reviews[0].revision_checks[0].status == "withdrawn"


def test_source_change_reassesses_shared_descendants_even_when_model_json_is_equal(tmp_path):
    spec, model, revision, catalog, profile = _fixture(tmp_path, resolved=True)
    llm = SourceReviewLLM()
    result = _review(tmp_path, llm, spec, model, catalog, profile, revision)
    assert [item["target_subproblem_id"] for item in llm.payloads] == ["sp1", "sp2", "downstream"]
    assert result.subproblem_reviews[-1] == revision.subproblem_reviews[-1]


def test_changed_independent_model_cannot_reuse_its_old_review(tmp_path):
    spec, model, revision, catalog, profile = _fixture(tmp_path, resolved=True)
    model.subproblems[-1].objective = "A changed independent inventory objective."
    llm = SourceReviewLLM()
    _review(tmp_path, llm, spec, model, catalog, profile, revision)
    assert "independent" in [item["target_subproblem_id"] for item in llm.payloads]
