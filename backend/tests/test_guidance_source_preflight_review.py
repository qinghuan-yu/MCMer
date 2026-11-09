"""来源许可先于数学审核；数学修订不能再承载来源解释。"""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import ModelReviewBinding, RevisionCheckSpec, RevisionRequirementSpec, SubproblemReviewSpec
from app.guidance.reviewer import LLMModelReviewer
from test_guidance_source_resolution import _case, _correction


class ApprovedMathLLM:
    model = "test-math-preflight"
    temperature = 0
    max_tokens = 1000

    def __init__(self):
        self.calls = []

    async def chat(self, **kwargs):
        payload = json.loads(kwargs["history"][1]["content"])
        payload = payload.get("original_input", payload)
        self.calls.append(payload)
        result = {
            "subproblem_id": payload["target_subproblem_id"], "status": "approved",
            "mathematical_checks": [{
                "requirement_id": identifier, "status": "passed",
                "model_paths": ["/execution_components/0/model/objective"],
                "rationale": "The explicit typed expression matches the supplied source requirement.",
            } for identifier in payload["mathematical_requirements"]],
        }
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(result)), finish_reason="stop",
        )])


def review_case(root):
    spec, catalog, profile = _case(root)
    original = json.loads((Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json").read_text(encoding="utf-8"))["subproblems"][0]
    models = []
    for item in spec.subproblems:
        model = deepcopy(original)
        model["subproblem_id"] = item.id
        models.append(model)
    for name, value in (("input_catalog.json", catalog), ("evidence_profile.json", profile)):
        (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8", newline="\n")
    return spec, ModelIR(model_id="preflight-review", subproblems=models), catalog, profile


def test_math_reviewer_cannot_approve_unpermitted_source_interpretations(tmp_path):
    spec, model, catalog, profile = review_case(tmp_path)
    llm = ApprovedMathLLM()
    result = asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=spec, model_ir=model, input_catalog=catalog, evidence_profile=profile,
        task={}, work_dir=tmp_path,
    ))
    assert result.executable_subproblem_ids == []
    assert all(item.assessment_status == "not_assessed" for item in result.subproblem_reviews)
    assert llm.calls == []


def test_math_review_cannot_propose_late_source_mutations():
    with pytest.raises(ValueError):
        SubproblemReviewSpec.model_validate({
            "subproblem_id": "sp1", "status": "revision_required",
            "source_corrections": [_correction()],
        })


def test_math_revision_cannot_relabel_a_source_decision_as_a_repair():
    with pytest.raises(ValueError):
        RevisionRequirementSpec.model_validate({
            "requirement_id": "unclear-source", "kind": "source",
            "basis_refs": ["problem_spec.json#uncertainties/UNC001"],
            "description": "Choose an interpretation of the charging source.",
            "acceptance_criteria": "Replace the upstream source meaning.",
        })


def test_math_revision_cannot_withdraw_instead_of_fixing_its_obligation():
    with pytest.raises(ValueError):
        RevisionCheckSpec.model_validate({
            "requirement_id": "actual-model-defect", "status": "withdrawn",
            "model_paths": [], "source_correction_ids": ["resolve_charge_basis"],
            "rationale": "A source change cannot certify that a mathematical defect was repaired.",
        })


def test_late_source_conflict_can_only_block_without_requesting_a_correction():
    payload = {
        "subproblem_id": "sp1", "status": "blocked", "blocking_issues": ["New source conflict."],
        "source_conflicts": [{"basis_refs": ["problem_spec.json#uncertainties/UNC001"],
                              "rationale": "The reviewed source relation conflicts with the original wording."}],
    }
    decision = SubproblemReviewSpec.model_validate(payload)
    assert decision.status == "blocked"
    with pytest.raises(ValueError):
        SubproblemReviewSpec.model_validate({**payload, "status": "approved", "blocking_issues": []})


def test_old_review_binding_without_preflight_permission_is_invalid():
    payload = {name: "a" * 64 for name in (
        "model_sha256", "approved_model_sha256", "problem_spec_sha256", "evidence_profile_sha256",
        "input_catalog_sha256", "review_sha256", "revision_request_sha256",
        "source_resolution_sha256", "source_adjudication_sha256",
    )}
    with pytest.raises(ValueError):
        ModelReviewBinding.model_validate(payload)
