"""离线模型传输驱动正式建模入口，校验工具反馈与每个子题的纠正上限。"""
import asyncio
from copy import deepcopy
import json

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.modeler import LLMModeler
from source_preflight_fixtures import write_source_preflight
from test_guidance_contract_agents import StubLLM
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
    return json.loads(llm.calls[index]["history"][1]["content"])


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
    details = _sent(llm, 1)["operator_contracts"]
    assert [op["operator_id"] for op in details] == lookup["inspect_operators"]
    assert "parameters_schema" in details[0] and "parameters_schema" not in details[1]
    value = _sent(llm, 2)["constraint_context"]["parameters"][0]["values"][0]
    assert value["value"] == 10 and value["source_ref"].startswith("inputs/training.docx#table-1")
    assert "undeclared" in _sent(llm, 3)["validation_error"]
    assert _run(tmp_path, StubLLM([]), spec, evidence) == subproblem


def test_one_correction_is_shared_by_bindings_and_formulation(tmp_path):
    proposal, formulation, evidence, spec = _training(tmp_path)
    bad_binding = deepcopy(proposal)
    bad_binding["indexed_data"]["parameters"][0]["binding"]["row_start"] = 100
    bad_equation = deepcopy(formulation)
    bad_equation["variables"] = []
    llm = StubLLM([bad_binding, proposal, bad_equation, formulation])
    assert _run(tmp_path, llm, spec, evidence).execution_status == "blocked"
    assert len(llm.calls) == 3
    assert "validation_error" in _sent(llm, 1)
    assert "constraint_context" in _sent(llm, 2)


@pytest.mark.parametrize("ids", [["unknown.operator"], ["optimization.milp", "optimization.milp"]])
def test_invalid_lookup_is_rejected_and_never_dispatches_an_operator(tmp_path, ids):
    _, _, evidence, spec = _training(tmp_path)
    llm = StubLLM([{"inspect_operators": ids}] * 2)
    assert _run(tmp_path, llm, spec, evidence).execution_status == "blocked"
    assert len(llm.calls) == 2
    assert ids[0] in _sent(llm, 1)["validation_error"]
    assert not (tmp_path / "execution_manifest.json").exists()
