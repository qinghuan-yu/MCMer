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
