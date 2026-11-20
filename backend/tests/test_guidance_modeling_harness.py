"""在正式 Harness 会话中检查工具反馈、局部修订与预算。"""
import asyncio
from copy import deepcopy
import json

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.modeler import LLMModeler
from source_preflight_fixtures import write_source_preflight
from test_guidance_contract_agents import StubLLM, _tool_observations
from test_guidance_formulation import _case


def _training(tmp_path):
    draft, bindings, formulation, evidence = _case(tmp_path)
    spec = ProblemSpec(task_summary="Optimize training costs.", subproblems=[{
        "id": "training", "depends_on_subproblem_ids": [],
        "required_fact_ids": [], "required_uncertainty_ids": [],
    }])
    write_source_preflight(tmp_path, spec, profile=evidence)
    return {**draft, "indexed_data": bindings}, formulation, evidence, spec


def _run(tmp_path, llm, spec, evidence):
    return asyncio.run(LLMModeler(llm).model(
        problem_spec=spec, evidence_profile=evidence, task={}, work_dir=tmp_path,
    )).subproblems[0]


def _sent(llm, index):
    return json.loads(next(m["content"] for m in llm.calls[index]["history"] if m["role"] == "user"))


def test_lookup_returns_only_requested_contract_then_checks_and_repairs_candidate(tmp_path):
    proposal, formulation, evidence, spec = _training(tmp_path)
    lookup = {"inspect_operators": ["data.read_excel_table", "optimization.milp"]}
    invalid = deepcopy(formulation)
    invalid["constraints"][0]["terms"][0]["variable"]["variable_id"] = "undeclared"
    llm = StubLLM([lookup, proposal, invalid, formulation])
    subproblem = _run(tmp_path, llm, spec, evidence)
    assert subproblem.execution_status == "solvable", subproblem
    assert len(llm.calls) == 4
    assert all("parameters_schema" not in op for op in _sent(llm, 0)["available_operators"])
    assert {t["function"]["name"] for t in llm.calls[0]["tools"]} == {
        "inspect_operators", "inspect_model", "edit_model", "submit_model",
    }
    details = _tool_observations(llm.calls[1], "inspect_operators")[-1]["contracts"]
    assert [op["operator_id"] for op in details] == lookup["inspect_operators"]
    assert "parameters_schema" in details[0] and "parameters_schema" not in details[1]
    value = _tool_observations(llm.calls[2], "edit_model")[-1]["parameters"][0]["values"][0]
    assert value["value"] == 10 and value["source_ref"].startswith("inputs/training.docx#table-1")
    assert "undeclared" in _tool_observations(llm.calls[3], "edit_model")[-1]["error"]
    assert _run(tmp_path, StubLLM([]), spec, evidence) == subproblem


def test_bad_binding_and_equation_cannot_exceed_the_shared_turn_budget(tmp_path):
    proposal, formulation, evidence, spec = _training(tmp_path)
    bad_binding = deepcopy(proposal)
    bad_binding["indexed_data"]["parameters"][0]["binding"]["row_start"] = 100
    bad_equation = deepcopy(formulation)
    bad_equation["objective"]["terms"][0]["variable"]["variable_id"] = "unknown_decision"
    llm = StubLLM([bad_binding, proposal, *([bad_equation] * 8)])
    assert _run(tmp_path, llm, spec, evidence).execution_status == "blocked"
    assert len(llm.calls) == 8
    assert not _tool_observations(llm.calls[1], "edit_model")[-1]["ok"]
    assert _tool_observations(llm.calls[2], "edit_model")[-1]["ok"]
    assert "unknown_decision" in _tool_observations(llm.calls[-1], "edit_model")[-1]["error"]


@pytest.mark.parametrize("ids", [["unknown.operator"], ["optimization.milp", "optimization.milp"]])
def test_invalid_lookup_is_rejected_and_never_dispatches_an_operator(tmp_path, ids):
    _, _, evidence, spec = _training(tmp_path)
    llm = StubLLM([{"inspect_operators": ids}] * 8)
    assert _run(tmp_path, llm, spec, evidence).execution_status == "blocked"
    assert len(llm.calls) == 8
    rejection = _tool_observations(llm.calls[1], "inspect_operators")[-1]
    assert not rejection["ok"]
    assert "unknown" in rejection["error"] or "distinct" in rejection["error"]
    assert not (tmp_path / "execution_manifest.json").exists()
