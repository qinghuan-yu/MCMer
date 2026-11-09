import json
from pathlib import Path

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.generation_validation import (
    _validate_generation_draft_slice,
    _validate_model_ir_semantics,
)
from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_drafts import ModelIRGenerationDraft


QUADRATIC = "optimization.constrained_quadratic_multistart"
MILP = "mixed_integer_linear_optimization"
FORECAST = "forecast.future_basis_rolling_origin"


def _model_payload(*, indexed: bool, graph_method: str | None, families: list[str]) -> dict:
    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    subproblem = payload["subproblems"][0]
    subproblem["validation"]["model_families"] = families
    if not indexed:
        subproblem["execution_components"] = []
    if graph_method:
        subproblem["execution_components"].append({
            "kind": "operator_graph",
            "component_id": "numerical_graph",
            "variables": [{
                "id": "result", "role": "derived", "symbol": "r", "unit": "1",
            }],
            "operators": [{
                "node_id": "numerical_method", "operator_id": graph_method,
                "inputs": {"values": "source.value"}, "outputs": ["method_result"],
            }],
        })
    nodes = (["solve"] if indexed else []) + (["numerical_method"] if graph_method else [])
    for candidate in subproblem["candidate_models"]:
        candidate["operator_node_ids"] = nodes
    return payload


def _draft(payload: dict) -> ModelIRGenerationDraft:
    subproblem = payload["subproblems"][0]
    components = subproblem.pop("execution_components")
    subproblem["execution_components"] = []
    subproblem["variables"] = []
    subproblem["operators"] = []
    for component in components:
        if component["kind"] == "indexed_linear":
            subproblem["execution_components"].append("indexed_linear_model")
        else:
            subproblem["execution_components"].append("operator_graph")
            subproblem["variables"] = component["variables"]
            subproblem["operators"] = component["operators"]
    for candidate in subproblem["candidate_models"]:
        candidate["operator_node_ids"] = [
            node for node in candidate["operator_node_ids"] if node != "solve"
        ]
    for field in ("equations", "constraints", "fact_bindings"):
        subproblem.pop(field)
    return ModelIRGenerationDraft.model_validate(payload)


@pytest.mark.parametrize("entrypoint", ["draft", "canonical"])
@pytest.mark.parametrize(("indexed", "graph_method", "families", "error"), [
    (True, None, ["optimization", MILP], "optimization.*requires.*constrained_quadratic"),
    (True, None, ["optimization"], "mixed_integer_linear_optimization.*required"),
    (True, None, ["regression"], "mixed_integer_linear_optimization.*required"),
    (False, QUADRATIC, [MILP], "optimization.*required"),
    (False, QUADRATIC, ["optimization", MILP], "mixed_integer_linear_optimization.*requires"),
    (True, FORECAST, ["future_forecast", "optimization", MILP], "optimization.*requires"),
    (True, QUADRATIC, [MILP], "optimization.*required"),
    (True, QUADRATIC, ["optimization"], "mixed_integer_linear_optimization.*required"),
    (True, None, [MILP], None),
    (False, QUADRATIC, ["optimization"], None),
    (True, QUADRATIC, ["optimization", MILP], None),
    (True, FORECAST, ["future_forecast", MILP], None),
])
def test_validation_categories_match_actual_execution_methods(
    entrypoint: str, indexed: bool, graph_method: str | None,
    families: list[str], error: str | None,
) -> None:
    payload = _model_payload(indexed=indexed, graph_method=graph_method, families=families)
    if entrypoint == "draft":
        model = _draft(payload)
        subproblem_id = model.subproblems[0].subproblem_id

        def validate() -> None:
            _validate_generation_draft_slice(
                model, expected_id=subproblem_id,
                available_operator_ids=["optimization.milp", QUADRATIC, FORECAST],
                problem_spec=ProblemSpec(task_summary="Validate method categories.", subproblems=[{
                    "id": subproblem_id, "required_fact_ids": [],
                    "required_uncertainty_ids": [], "depends_on_subproblem_ids": [],
                }]),
            )
    else:
        model = ModelIR.model_validate(payload)

        def validate() -> None:
            _validate_model_ir_semantics(model)

    before = model.model_dump(mode="json")
    if error:
        with pytest.raises(ValueError, match=error):
            validate()
    else:
        validate()
    # 验证只拒绝或接受声明，不静默删除模型选择的验证族。
    assert model.model_dump(mode="json") == before
