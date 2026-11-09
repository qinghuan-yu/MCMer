"""目标与关系在同一声明阶段构造，费用不依赖尚未定义的状态含义。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.modeler import LLMModeler
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.operator_catalog import build_default_operator_registry
from app.guidance.operator_runtimes.milp import solve_milp
from source_preflight_fixtures import write_source_preflight


def _term(variable, coefficient):
    return {"coefficient": coefficient, "variable": {"variable_id": variable}}


def _constant(value):
    return {"kind": "constant", "value": value}


def _case(root):
    from docx import Document

    from app.artifacts.evidence_profile import build_evidence_profile
    from app.artifacts.input_catalog import build_input_catalog

    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    architecture = json.loads(fixture.read_text(encoding="utf-8"))
    sub = architecture["subproblems"][0]
    for field in ("fact_bindings", "equations", "constraints"):
        sub.pop(field)
    sub.update(subproblem_id="training", execution_components=["indexed_linear_model"], variables=[], operators=[])
    for candidate in sub["candidate_models"]:
        candidate["operator_node_ids"] = []
    core = {
        "index_sets": [{"id": "period", "values": ["now"]}],
        "parameters": [{"id": name, "source_kind": "static", "binding": {
            "kind": "table_range", "source_ref": "inputs/training.docx#table-1",
            "row_start": row, "row_stop": row + 1, "column_start": 0, "column_stop": 1,
            "order": "row_major",
        }} for row, name in enumerate(("training_cost", "idle_cost"))],
    }
    formulation = {
        "variables": [{"id": name, "domain": "integer"} for name in ("learners", "trainers", "idle")],
        "objective": {"sense": "minimize", "terms": [
            _term(name, {"kind": "parameter", "parameter_id": tariff})
            for name, tariff in (("learners", "training_cost"), ("trainers", "training_cost"), ("idle", "idle_cost"))
        ]},
        "constraints": [
            {"id": "exact_training", "terms": [_term("learners", _constant(1))], "relation": "equal", "rhs": _constant(20)},
            {"id": "capacity", "terms": [_term("learners", _constant(1)), _term("trainers", _constant(-10))], "relation": "less_equal", "rhs": _constant(0)},
            {"id": "staff_balance", "terms": [_term("trainers", _constant(1)), _term("idle", _constant(1))], "relation": "equal", "rhs": _constant(20)},
        ],
    }
    source = root / "inputs/training.docx"
    source.parent.mkdir(parents=True)
    document = Document()
    table = document.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "10"
    table.cell(1, 0).text = "5"
    document.save(source)
    build_input_catalog(source, work_dir=root)
    evidence = build_evidence_profile(root).model_dump(mode="json")
    return architecture, core, formulation, evidence


@pytest.mark.parametrize("omit_on_first_attempt", [False, True])
def test_joint_formulation_charges_each_defined_training_participant(tmp_path, omit_on_first_attempt):
    architecture, core, formulation, evidence = _case(tmp_path)

    class StageLLM:
        attempts = 0

        async def chat(self, **kwargs):
            stage = kwargs["agent_name"]
            response = core if stage.endswith("indexed_core") else formulation if stage.endswith(("indexed_constraints", "indexed_formulation")) else architecture
            if response is formulation:
                self.attempts += 1
                if omit_on_first_attempt and self.attempts == 1:
                    response = deepcopy(formulation)
                    response["objective"]["terms"].pop()
                    response["constraints"].pop()
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response)))])

    spec = ProblemSpec(task_summary="Train exactly 20 learners; charge 10 per learner and trainer, 5 per idle staff; 20 existing staff; capacity 10 learners per trainer.", subproblems=[{
        "id": "training", "required_fact_ids": [], "required_uncertainty_ids": [], "depends_on_subproblem_ids": [],
    }])
    write_source_preflight(tmp_path, spec, profile=evidence)
    llm = StageLLM()
    model = asyncio.run(LLMModeler(llm, available_operator_ids=["optimization.milp"]).model(
        problem_spec=spec, evidence_profile=evidence, task={}, work_dir=tmp_path,
    ))
    assert model.subproblems[0].execution_status == "solvable", model.subproblems[0]
    compilation = ModelIRCompiler(registry=build_default_operator_registry()).compile(model, problem_spec=spec, evidence_profile=evidence)
    assert compilation.status == "compiled", compilation.diagnostics
    solution = solve_milp(inputs={}, parameters=compilation.execution_plan.graph.nodes[0].parameters, random_seed=None, work_dir=tmp_path)["solution"]
    assert solution["objective_value"] == 310
    assert solution["decision_values"] == {"learners": 20, "trainers": 2, "idle": 18}
    assert llm.attempts == (2 if omit_on_first_attempt else 1)
