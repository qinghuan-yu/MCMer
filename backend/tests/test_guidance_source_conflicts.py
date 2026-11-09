"""后置来源冲突隔离共享所有者和后继，不重新裁决题意。"""

from copy import deepcopy
import json

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.model_review_validation import unresolved_source_subproblem_ids
from app.guidance.source_resolution import resolve_problem_sources
from app.guidance.stages import _build_reviewed_model_projection
from test_guidance_source_resolution import _case
from test_guidance_source_reviewer import SourceReviewLLM, _models, _review, _save_preflight


def _shared_case(root):
    from test_guidance_source_preflight import _checks, _decisions

    before, catalog, profile = _case(root)
    payload = before.model_dump(mode="json")
    payload["uncertainties"][0]["subproblem_ids"].append("shared_owner")
    payload["locked_facts"][0]["subproblem_ids"].append("shared_owner")
    payload["subproblems"].insert(2, {
        "id": "shared_owner", "required_fact_ids": ["F_COST"], "required_uncertainty_ids": ["UNC001"],
        "depends_on_subproblem_ids": [],
    })
    payload["subproblems"][3]["depends_on_subproblem_ids"] = ["shared_owner"]
    before = ProblemSpec.model_validate(payload)
    interpretation = _decisions(before)
    spec, record = resolve_problem_sources(
        before, interpretation=interpretation, adjudication=_checks(interpretation),
        input_catalog=catalog, evidence_profile=profile, work_dir=root,
    )
    _save_preflight(root, spec, record, catalog, profile)
    return spec, _models(spec), record, catalog, profile


class ConflictingSourceLLM(SourceReviewLLM):
    def __init__(self, reporter):
        super().__init__()
        self.reporter = reporter
        self.original_decisions = []

    async def chat(self, **kwargs):
        response = await super().chat(**kwargs)
        decision = json.loads(response.choices[0].message.content)
        if decision["subproblem_id"] == self.reporter:
            decision.update(status="blocked", blocking_issues=["New conflict in a shared source relation."],
                            source_conflicts=[{
                                "basis_refs": ["problem_spec.json#locked_facts/F_COST"],
                                "rationale": "The original table and the modeled charge require another source decision.",
                            }])
        self.original_decisions.append(deepcopy(decision))
        response.choices[0].message.content = json.dumps(decision)
        return response


@pytest.mark.parametrize("reporter", ["sp1", "sp2", "shared_owner"])
def test_late_source_conflict_blocks_shared_owners_and_descendants_without_reopening_permission(tmp_path, reporter):
    spec, model, record, catalog, profile = _shared_case(tmp_path)
    original = spec.model_dump(mode="json")
    permission = (tmp_path / "source_preflight.json").read_bytes()
    llm = ConflictingSourceLLM(reporter)
    review = _review(tmp_path, llm, spec, model, catalog, profile)
    affected = {"sp1", "sp2", "shared_owner", "downstream"}
    assert unresolved_source_subproblem_ids(review, spec) == affected
    projection = _build_reviewed_model_projection(model, review, spec)
    statuses = {item.subproblem_id: item.execution_status for item in projection.subproblems}
    assert {identifier for identifier, status in statuses.items() if status == "blocked"} == affected
    assert statuses["independent"] == "solvable"
    decision = next(item for item in review.subproblem_reviews if item.subproblem_id == reporter)
    assert decision.status == "blocked" and decision.assessment_status == "reviewed"
    assert decision.source_conflicts[0].basis_refs == ["problem_spec.json#locked_facts/F_COST"]
    assert review.subproblem_reviews[-1].status == "approved"
    assert spec.model_dump(mode="json") == original
    assert (tmp_path / "source_preflight.json").read_bytes() == permission
    assert all("target_subproblem_id" in payload for payload in llm.payloads)
