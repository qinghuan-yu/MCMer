import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.reviewer import LLMModelReviewer


def _case():
    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    original = json.loads(fixture.read_text(encoding="utf-8"))["subproblems"][0]
    models = []
    dependencies = {"source": [], "dependent": ["source"], "independent": []}
    for identifier in dependencies:
        item = deepcopy(original)
        item["subproblem_id"] = identifier
        models.append(item)
    return (
        ProblemSpec(task_summary="Review independent and dependent inventory policies.", subproblems=[{
            "id": identifier, "required_fact_ids": [], "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": parents,
        } for identifier, parents in dependencies.items()]),
        ModelIR(model_id="review-isolation", subproblems=models),
    )


class ReviewLLM:
    model = "test-review"
    max_tokens = 100
    temperature = 0

    def __init__(self, *, failures=(), omitted_math=()):
        self.failures = set(failures)
        self.omitted_math = set(omitted_math)
        self.calls = []

    async def chat(self, **kwargs):
        payload = json.loads(kwargs["history"][1]["content"])
        payload = payload.get("original_input", payload)
        self.calls.append((kwargs, payload))
        # 同一provider桩能返回旧整题合同；RED观察请求粒度及失败传播，而非桩KeyError。
        models = ([payload["subproblem_model"]] if "subproblem_model" in payload
                  else payload["model_ir"]["subproblems"])
        truncated = any(item["subproblem_id"] in self.failures for item in models)
        decisions = []
        for model in models:
            identifier = model["subproblem_id"]
            revisions = payload.get("revision_requirements", [])
            decisions.append({
                "subproblem_id": identifier, "status": "approved", "assumption_checks": [],
                "mathematical_checks": [{
                    "requirement_id": requirement, "status": "passed",
                    "model_paths": ["/execution_components/0/model/objective"],
                    "rationale": "The represented inventory objective is finite and explicit.",
                } for requirement in ["$objective", "$formulation"]
                    if (identifier, requirement) not in self.omitted_math],
                "revision_checks": [{
                    "requirement_id": requirement["requirement_id"], "status": "accepted",
                    "model_paths": ["/execution_components/0/model/objective"],
                    "rationale": "The requested objective correction is represented.",
                } for requirement in revisions],
            })
        content = (decisions[0] if "subproblem_model" in payload else {
            "model_id": payload["model_ir"]["model_id"], "subproblem_reviews": decisions,
        })
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(content)),
            finish_reason="length" if truncated else "stop",
        )])


def _review(root, llm, problem, model, **kwargs):
    return asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=problem, model_ir=model, evidence_profile={"evidence": []},
        task={}, work_dir=root, **kwargs,
    ))


def _targets(llm):
    return [payload["target_subproblem_id"] for _, payload in llm.calls]


def test_one_truncated_review_preserves_independent_review_and_local_diagnostic(tmp_path):
    problem, model = _case()
    llm = ReviewLLM(failures={"source"})
    review = _review(tmp_path, llm, problem, model)

    by_id = {item.subproblem_id: item for item in review.subproblem_reviews}
    assert by_id["source"].status == "blocked"
    assert by_id["source"].assessment_status == "not_assessed"
    assert by_id["source"].mathematical_checks == []
    assert by_id["independent"].status == "approved"
    assert _targets(llm) == ["source", "dependent", "independent"]
    diagnostic = json.loads((tmp_path / "contract_diagnostics/GuidanceModelReviewer_source.json").read_text(encoding="utf-8"))
    assert diagnostic["attempts"][0]["failure_kind"] == "truncation"
    llm.failures.clear()
    llm.calls.clear()
    assert _review(tmp_path, llm, problem, model).status == "approved"
    assert _targets(llm) == ["source"]


def test_review_cache_tracks_target_and_transitive_dependencies_not_other_models(tmp_path):
    problem, model = _case()
    llm = ReviewLLM()
    assert _review(tmp_path, llm, problem, model).status == "approved"
    assert len(llm.calls) == 3
    llm.calls.clear()
    changed = model.model_copy(deep=True)
    changed.model_id = "new-merged-id"
    changed.subproblems[0].objective = "Minimize the revised source inventory objective."
    _review(tmp_path, llm, problem, changed)
    assert _targets(llm) == ["source", "dependent"]
    dependent = llm.calls[1][1]
    assert dependent["dependency_models"]["source"]["objective"] == changed.subproblems[0].objective
    assert "independent" not in dependent["dependency_models"]
    assert dependent["problem_spec"] == problem.model_dump(mode="json")


def test_review_cache_changes_when_original_source_file_changes(tmp_path):
    problem, model = _case()
    source = tmp_path / "question.txt"
    source.write_text("Original problem.", encoding="utf-8")
    catalog = {"source_file": "question.txt", "source_sha256": "0" * 64, "blocks": []}
    llm = ReviewLLM()
    _review(tmp_path, llm, problem, model, input_catalog=catalog)
    assert len(llm.calls) == 3
    llm.calls.clear()
    _review(tmp_path, llm, problem, model, input_catalog=catalog)
    assert llm.calls == []
    source.write_text("Changed original problem.", encoding="utf-8")
    _review(tmp_path, llm, problem, model, input_catalog=catalog)
    assert _targets(llm) == ["source", "dependent", "independent"]


def test_missing_mathematical_requirement_is_retried_then_left_unassessed(tmp_path):
    problem, model = _case()
    llm = ReviewLLM(omitted_math={("source", "$formulation")})
    review = _review(tmp_path, llm, problem, model)
    assert review.subproblem_reviews[0].assessment_status == "not_assessed"
    assert review.subproblem_reviews[2].status == "approved"
    assert Counter(_targets(llm)) == {"source": 2, "dependent": 1, "independent": 1}


def test_revision_reuses_unchanged_approved_reviews_and_supplies_exact_local_requirements(tmp_path):
    problem, model = _case()
    llm = ReviewLLM()
    previous = _review(tmp_path, llm, problem, model).model_dump(mode="json")
    previous.pop("status")
    previous.pop("executable_subproblem_ids")
    previous["subproblem_reviews"][0].update(status="revision_required", required_revisions=[{
        "requirement_id": "fix-objective", "description": "Correct the source objective.",
        "acceptance_criteria": "The local objective has the supported coefficients.",
    }])
    llm.calls.clear()
    review = _review(tmp_path, llm, problem, model, revision_request=ModelIRReview.model_validate(previous))
    assert review.status == "approved"
    assert _targets(llm) == ["source"]
    payload = llm.calls[0][1]
    assert payload["review_phase"] == "revision"
    assert [item["requirement_id"] for item in payload["revision_requirements"]] == ["fix-objective"]
    assert review.subproblem_reviews[0].revision_checks[0].model_paths == ["/execution_components/0/model/objective"]
