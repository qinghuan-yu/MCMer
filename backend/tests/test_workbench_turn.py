import asyncio
import json

from app.guidance.contracts import ProblemSpec
from app.guidance.modeler import LLMModeler
from math_tool_fixtures import tool_reply
from source_preflight_fixtures import write_source_preflight
from test_guidance_contract_agents import _blocked_ir


def test_default_modeler_uses_native_math_tools_and_checkpoints_only_submitted_model(tmp_path):
    class Provider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            assert kwargs["tools"]
            names = {item["function"]["name"] for item in kwargs["tools"]}
            assert {"inspect_model", "edit_model", "submit_model"} <= names
            context = json.loads(kwargs["history"][1]["content"])
            assert context["indexed_solver_answer"] == {"value_ref": "solution", "evidence_ids": ["solve_indexed_model"]}
            assert kwargs["max_attempts"] == 1
            return tool_reply(("edit_model", {"architecture": _blocked_ir()}), ("submit_model", {}))

    spec = ProblemSpec(task_summary="Estimate missing measurements.",
                       subproblems=[{"id": "problem1", "required_fact_ids": [],
                                     "required_uncertainty_ids": [], "depends_on_subproblem_ids": []}])
    write_source_preflight(tmp_path, spec)
    llm = Provider()
    modeler = LLMModeler(llm)
    async def run():
        return await modeler.model(problem_spec=spec, task={}, work_dir=tmp_path)
    model = asyncio.run(run())
    assert model.model_id == "operator-gap"
    assert asyncio.run(run()) == model
    assert llm.calls == 1
    receipt = json.loads((tmp_path / "contract_diagnostics/GuidanceModeler.json").read_bytes())
    assert receipt["engine"] == "deepseek-harness" and receipt["status"] == "submitted"
    assert [entry["name"] for entry in receipt["actions"]] == ["edit_model", "submit_model"]


def test_revision_rechecks_evidence_from_older_accepted_model(tmp_path):
    from app.guidance.model_ir_drafts import ModelIRGenerationDraft
    from app.guidance.model_review_contracts import ModelIRReview
    from app.guidance.recovery import RecoveryCheckpoint
    from test_guidance_contract_agents import StubLLM, _operator_graph_draft, _subproblem

    draft = _operator_graph_draft()
    previous = ModelIRGenerationDraft.model_validate(draft).promote()
    sub = previous.subproblems[0]
    sub.final_answers[0].evidence_ids = ["unregistered_result"]
    spec = ProblemSpec(task_summary="Estimate the requested results.", subproblems=[_subproblem(sub.subproblem_id)])
    write_source_preflight(tmp_path, spec)
    llm = StubLLM(draft)
    modeler = LLMModeler(llm)
    RecoveryCheckpoint(tmp_path, "model_operator_contract", "current").save({
        "model_ir": previous.model_dump(mode="json"), "operator_contract_sha256": modeler.registry.contract_sha256(),
    }, {"validated": True})
    review = ModelIRReview(model_id=previous.model_id, subproblem_reviews=[{
        "subproblem_id": sub.subproblem_id, "status": "approved",
    }])
    revised = asyncio.run(modeler.revise(problem_spec=spec, previous_model_ir=previous, review=review,
                                         task={}, work_dir=tmp_path))
    assert len(llm.calls) == 1
    assert revised.subproblems[0].final_answers[0].evidence_ids == draft["subproblems"][0]["final_answers"][0]["evidence_ids"]
