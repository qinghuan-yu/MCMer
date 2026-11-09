from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import SubproblemReviewSpec
from app.guidance.model_review_validation import validate_subproblem_review
from test_guidance_source_resolution import _correction
from test_guidance_source_reviewer import _decision, _fixture, _review


class ConflictingSourceLLM:
    model = "test-conflicting-source-review"
    temperature = 0
    max_tokens = 100

    def __init__(self, *, reverse=False):
        self.payloads = []
        self.decisions = []
        self.proposals = {}
        for identifier, value in zip(
            ["sp1", "sp2"],
            ["trainees_only", "trainees_and_instructors"] if reverse
            else ["trainees_and_instructors", "trainees_only"],
        ):
            correction = _correction(correction_id=f"resolve_{identifier}").model_dump(mode="json")
            correction["replacement_fact"]["subproblem_ids"].append("shared_owner")
            correction["replacement_fact"]["value"] = value
            self.proposals[identifier] = correction

    async def chat(self, **kwargs):
        payload = json.loads(kwargs["history"][1]["content"])
        payload = payload.get("original_input", payload)
        self.payloads.append(payload)
        target = payload["target_subproblem_id"]
        decision = _decision(target, payload["mathematical_requirements"])
        if target in self.proposals:
            decision.update(status="revision_required", source_corrections=[self.proposals[target]])
        self.decisions.append(deepcopy(decision))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(decision)), finish_reason="stop",
        )])


def _shared_case(root):
    spec, model, _, catalog, profile = _fixture(root, resolved=False)
    payload = spec.model_dump(mode="json")
    payload["uncertainties"][0]["subproblem_ids"].append("shared_owner")
    payload["subproblems"].insert(2, {
        "id": "shared_owner", "required_fact_ids": [], "required_uncertainty_ids": ["UNC001"],
        "depends_on_subproblem_ids": [],
    })
    payload["subproblems"][3]["depends_on_subproblem_ids"] = ["shared_owner"]
    models = model.model_dump(mode="json")
    owner = deepcopy(models["subproblems"][0])
    owner["subproblem_id"] = "shared_owner"
    models["subproblems"].insert(2, owner)
    return ProblemSpec.model_validate(payload), ModelIR.model_validate(models), catalog, profile


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_source_batch_blocks_owners_and_descendants_but_preserves_independent_review(tmp_path, reverse):
    spec, model, catalog, profile = _shared_case(tmp_path)
    original = spec.model_dump(mode="json")
    llm = ConflictingSourceLLM(reverse=reverse)
    for subproblem in model.subproblems[:2]:
        decision = _decision(subproblem.subproblem_id, ["F_COST", "$objective", "$formulation"])
        decision.update(status="revision_required", source_corrections=[llm.proposals[subproblem.subproblem_id]])
        validate_subproblem_review(
            SubproblemReviewSpec.model_validate(decision), subproblem,
            problem_spec=spec, input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
        )

    review = _review(tmp_path, llm, spec, model, catalog, profile)

    affected = ["sp1", "sp2", "shared_owner", "downstream"]
    decisions = {item.subproblem_id: item for item in review.subproblem_reviews}
    assert [item["target_subproblem_id"] for item in llm.payloads] == [*affected, "independent"]
    for identifier in affected:
        decision = decisions[identifier]
        assert decision.status == "blocked"
        assert decision.assessment_status == "not_assessed"
        assert decision.mathematical_checks == decision.assumption_checks == decision.revision_checks == []
        assert decision.required_revisions == decision.source_corrections == []
        assert any("source_correction_failure.json" in item for item in decision.blocking_issues)
    assert decisions["independent"] == SubproblemReviewSpec.model_validate(llm.decisions[-1])
    assert review.status == "partial"
    assert review.executable_subproblem_ids == ["independent"]
    assert spec.model_dump(mode="json") == original
    assert not (tmp_path / "source_resolution.json").exists()
    diagnostic = json.loads((tmp_path / "source_correction_failure.json").read_text(encoding="utf-8"))
    assert diagnostic["error_type"] == "ValueError"
    assert "conflicting source corrections" in diagnostic["error"]
    assert diagnostic["affected_subproblem_ids"] == affected
    assert [(item["subproblem_id"], item["correction"]) for item in diagnostic["proposals"]] == list(llm.proposals.items())
