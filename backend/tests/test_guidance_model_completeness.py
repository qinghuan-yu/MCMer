"""删掉业务必需方程的模型不得被确定性层报告为通过（计划 F1/F2）。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.model_ir_semantics import (
    ModelIRSemanticValidator,
    validate_model_fact_coverage,
)
from app.guidance.model_validators import build_default_model_validator_registry
from app.guidance.operator_catalog import build_default_operator_registry
from app.guidance.requirement_coverage import validate_requirement_coverage

FIXTURE = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
SPEC_FIXTURE = FIXTURE.with_name("generic_multiperiod_inventory_problem_spec.json")


def _problem_spec() -> ProblemSpec:
    return ProblemSpec.model_validate_json(SPEC_FIXTURE.read_text(encoding="utf-8"))


def _deterministic_verdict(payload: dict, work_dir: Path) -> tuple[str, list[str]]:
    """按建模阶段顺序：事实/需求覆盖 -> 语义 -> 编译 -> 执行校验 -> 适用性评估。"""
    model_ir = ModelIR.model_validate(payload)
    problem_spec = _problem_spec()
    try:
        validate_model_fact_coverage(problem_spec, model_ir)
        validate_requirement_coverage(problem_spec, model_ir)
    except ValueError as exc:
        return "rejected", [str(exc)]
    semantic = ModelIRSemanticValidator().validate(model_ir)
    if semantic:
        return "rejected", [diagnostic.code for diagnostic in semantic]
    registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=registry).compile(model_ir)
    if compilation.status != "compiled":
        return "rejected", [diagnostic.code for diagnostic in compilation.diagnostics]
    runtime = registry.runtime("optimization.milp")
    outputs = runtime.executor(
        inputs={},
        parameters=compilation.execution_plan.graph.nodes[0].parameters,
        random_seed=None,
        work_dir=work_dir,
    )
    runtime_errors = runtime.validator(outputs=outputs)
    if runtime_errors:
        return "rejected", [str(error) for error in runtime_errors]
    assessment = build_default_model_validator_registry().assess(
        family="mixed_integer_linear_optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[outputs["solution"]],
    )
    if assessment.status != "passed":
        return "rejected", list(assessment.reasons)
    return "passed", []


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _indexed_model(payload: dict) -> dict:
    return payload["subproblems"][0]["execution_components"][0]["model"]


def _without_constraints(constraint_ids: set[str]) -> dict:
    payload = _fixture()
    model = _indexed_model(payload)
    present = {constraint["id"] for constraint in model["constraints"]}
    assert constraint_ids <= present, constraint_ids - present
    model["constraints"] = [
        constraint for constraint in model["constraints"]
        if constraint["id"] not in constraint_ids
    ]
    return deepcopy(payload)


def _semantic_codes(payload: dict) -> list[tuple[str, str]]:
    return [
        (diagnostic.code, diagnostic.path)
        for diagnostic in ModelIRSemanticValidator().validate(ModelIR.model_validate(payload))
    ]


def test_complete_inventory_model_passes_deterministic_layers(tmp_path: Path) -> None:
    assert _deterministic_verdict(_without_constraints(set()), tmp_path) == ("passed", [])


@pytest.mark.parametrize(
    "removed",
    [
        pytest.param({"initial_balance", "state_balance"}, id="stock_balance"),
        pytest.param({"initial_commissioned", "commissioning_delay"}, id="commissioning_delay"),
        pytest.param({"tier_capacity", "setup_capacity"}, id="tier_and_setup_capacity"),
    ],
)
def test_inventory_model_missing_required_equations_is_rejected(
    removed: set[str], tmp_path: Path,
) -> None:
    verdict, _reasons = _deterministic_verdict(_without_constraints(removed), tmp_path)
    assert verdict == "rejected"


def _parameter_path(payload: dict, parameter_id: str) -> str:
    ids = [parameter["id"] for parameter in _indexed_model(payload)["parameters"]]
    return (
        "subproblems[finite_inventory_policy].execution_components[0]"
        f".model.parameters[{ids.index(parameter_id)}]"
    )


def test_parameter_absent_from_objective_and_constraints_is_reported() -> None:
    payload = _without_constraints({"tier_capacity", "setup_capacity"})
    unused = sorted(
        path for code, path in _semantic_codes(payload) if code == "indexed_parameter_unused"
    )
    assert unused == sorted([
        _parameter_path(payload, "tier_capacity"),
        _parameter_path(payload, "setup_capacity"),
    ])


def test_parameter_used_only_with_zero_multiplier_is_reported() -> None:
    payload = _fixture()
    for constraint in _indexed_model(payload)["constraints"]:
        if constraint["rhs"].get("parameter_id") == "demand":
            constraint["rhs"]["multiplier"] = 0.0
    unused = [
        path for code, path in _semantic_codes(payload) if code == "indexed_parameter_unused"
    ]
    assert unused == [_parameter_path(payload, "demand")]


def test_equation_without_any_symbol_is_rejected_as_vacuous() -> None:
    payload = _fixture()
    payload["subproblems"][0]["equations"] = [{
        "id": "placeholder",
        "left": {"kind": "literal", "value": 0.0},
        "right": {"kind": "literal", "value": 0.0},
    }]
    assert ("vacuous_equation", "subproblems[finite_inventory_policy].equations[0]") in (
        _semantic_codes(payload)
    )
