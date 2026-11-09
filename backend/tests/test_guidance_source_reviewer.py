"""数学审核消费前置来源许可，保持真实修订与独立审核缓存。"""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.reviewer import LLMModelReviewer
from app.guidance.source_resolution import resolve_problem_sources
from test_guidance_source_resolution import _case


def _decision(identifier, requirements):
    return {
        "subproblem_id": identifier, "status": "approved",
        "mathematical_checks": [{
            "requirement_id": item, "status": "passed",
            "model_paths": ["/execution_components/0/model/objective"],
            "rationale": "Check the explicit typed model against the supplied source requirement.",
        } for item in requirements],
    }


def _models(spec):
    base = json.loads((Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json").read_text(encoding="utf-8"))["subproblems"][0]
    models = []
    for item in spec.subproblems:
        model = deepcopy(base)
        model["subproblem_id"] = item.id
        models.append(model)
    return ModelIR(model_id="source-review", subproblems=models)


def _save_preflight(root, spec, record, catalog, profile):
    for name, value in (
        ("problem_spec_original.json", record.original_problem_spec.model_dump(mode="json")),
        ("problem_spec.json", spec.model_dump(mode="json")),
        ("source_preflight.json", record.model_dump(mode="json")),
        ("input_catalog.json", catalog), ("evidence_profile.json", profile),
    ):
        (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8", newline="\n")


def _prepare(root, *, first_check="supported", interpretation_failure=None, adjudication_failure=None):
    from test_guidance_source_preflight import _checks, _decisions

    before, catalog, profile = _case(root)
    interpretation = _decisions(before)
    adjudication = _checks(interpretation)
    adjudication.checks[0].status = first_check
    if interpretation_failure:
        interpretation, adjudication = None, None
    elif adjudication_failure:
        adjudication = None
    spec, record = resolve_problem_sources(
        before, interpretation=interpretation, adjudication=adjudication,
        input_catalog=catalog, evidence_profile=profile, work_dir=root,
        interpretation_failure_reason=interpretation_failure,
        adjudication_failure_reason=adjudication_failure,
    )
    _save_preflight(root, spec, record, catalog, profile)
    return spec, _models(spec), record, catalog, profile


class SourceReviewLLM:
    model = "test-source-review"
    temperature = 0
    max_tokens = 1000

    def __init__(self, *, conflict=None, revisions=0):
        self.conflict = conflict
        self.revisions = revisions
        self.payloads = []

    async def chat(self, **kwargs):
        payload = json.loads(kwargs["history"][1]["content"])
        payload = payload.get("original_input", payload)
        self.payloads.append(payload)
        assert "target_subproblem_id" in payload, "Mathematical review must not run another source adjudication."
        decision = _decision(payload["target_subproblem_id"], payload["mathematical_requirements"])
        decision["revision_checks"] = [{
            "requirement_id": item["requirement_id"], "status": "accepted", "model_paths": ["/objective"],
            "rationale": "The explicit revised model repairs the corresponding mathematical defect.",
        } for item in payload["revision_requirements"]]
        if payload["target_subproblem_id"] == "sp1":
            if self.revisions:
                decision.update(status="revision_required", required_revisions=[{
                    "requirement_id": f"model_requirement_{index}", "kind": "model", "basis_refs": [],
                    "description": f"Retain independently identified model requirement {index}.",
                    "acceptance_criteria": f"Verify the corresponding mathematical change {index}.",
                } for index in range(self.revisions)])
            if self.conflict:
                decision.update(status="blocked", blocking_issues=["New original source conflict."],
                                source_conflicts=[self.conflict])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(decision)), finish_reason="stop",
        )])


def _review(root, llm, spec, model, catalog, profile, revision=None):
    return asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=spec, model_ir=model, revision_request=revision,
        input_catalog=catalog, evidence_profile=profile, task={}, work_dir=root,
    ))


def test_reviewer_uses_the_source_corrected_problem_without_reopening_source_decisions(tmp_path):
    spec, model, record, catalog, profile = _prepare(tmp_path)
    before = (tmp_path / "source_preflight.json").read_bytes()
    llm = SourceReviewLLM()
    result = _review(tmp_path, llm, spec, model, catalog, profile)
    assert result.status == "approved"
    assert all(item.assessment_status == "reviewed" for item in result.subproblem_reviews)
    sent_spec = llm.payloads[0]["problem_spec"]
    assert "UNC001" not in [item["uncertainty_id"] for item in sent_spec["uncertainties"]]
    assert "F_CHARGE_BASIS" in [item["fact_id"] for item in sent_spec["locked_facts"]]
    assert (tmp_path / "source_preflight.json").read_bytes() == before
    assert len(llm.payloads) == 4


def test_final_reviewer_checks_model_repairs_without_withdrawing_them(tmp_path):
    spec, model, _, catalog, profile = _prepare(tmp_path)
    initial = _review(tmp_path, SourceReviewLLM(revisions=1), spec, model, catalog, profile)
    permission = (tmp_path / "source_preflight.json").read_bytes()
    llm = SourceReviewLLM()
    result = _review(tmp_path, llm, spec, model, catalog, profile, initial)
    assert result.subproblem_reviews[0].status == "approved"
    assert result.subproblem_reviews[0].revision_checks[0].status == "accepted"
    assert len(result.subproblem_reviews[0].revision_checks) == len(initial.subproblem_reviews[0].required_revisions)
    assert (tmp_path / "source_preflight.json").read_bytes() == permission


def test_unchanged_independent_review_reuses_its_valid_contract_after_math_revision(tmp_path):
    spec, model, _, catalog, profile = _prepare(tmp_path)
    initial = _review(tmp_path, SourceReviewLLM(revisions=1), spec, model, catalog, profile)
    llm = SourceReviewLLM()
    final = _review(tmp_path, llm, spec, model, catalog, profile, initial)
    assert "independent" not in [item["target_subproblem_id"] for item in llm.payloads]
    assert final.subproblem_reviews[-1] == initial.subproblem_reviews[-1]


def test_changed_independent_model_cannot_reuse_its_old_review(tmp_path):
    spec, model, _, catalog, profile = _prepare(tmp_path)
    initial = _review(tmp_path, SourceReviewLLM(revisions=1), spec, model, catalog, profile)
    model.subproblems[-1].objective = "A changed independent inventory objective."
    llm = SourceReviewLLM()
    _review(tmp_path, llm, spec, model, catalog, profile, initial)
    assert "independent" in [item["target_subproblem_id"] for item in llm.payloads]
