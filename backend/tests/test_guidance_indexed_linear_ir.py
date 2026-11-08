from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.guidance.indexed_linear import validate_indexed_linear_core
from app.guidance.model_ir import IndexedLinearCoreSpec, ModelIR
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.model_validators import build_default_model_validator_registry
from app.guidance.operator_catalog import build_default_operator_registry
from app.guidance.operators import OperatorDefinition, OperatorPort, OperatorRegistry


def _registry() -> OperatorRegistry:
    registry = OperatorRegistry()
    registry.register(OperatorDefinition(
        operator_id="optimization.milp",
        version="1.0.0",
        inputs={},
        outputs={"solution": OperatorPort(value_kind="artifact", unit="unspecified")},
        deterministic=True,
        validator_id="optimization.milp.validator.v1",
    ))
    return registry


def _payload() -> dict:
    return {
        "schema_version": "1.0",
        "model_id": "generic_indexed_linear_model",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "finite_horizon_allocation",
            "objective": "Minimize indexed acquisition cost subject to state conservation.",
            "assumptions": [],
            "fact_bindings": [],
            "data": [{
                "id": "source",
                "source_ref": "input_catalog.json#table-1",
                "fields": {"value": "value"},
            }],
            "equations": [{
                "id": "linear_model_defined_below",
                "left": {"kind": "literal", "value": 0.0},
                "right": {"kind": "literal", "value": 0.0},
            }],
            "constraints": [],
            "execution_components": [{
                "kind": "indexed_linear",
                "component_id": "finite_horizon_policy",
                "model": {
                "index_sets": [
                    {"id": "period", "values": ["p1", "p2"]},
                    {"id": "transition_period", "values": ["p2"]},
                ],
                "parameters": [
                    {
                        "id": "unit_cost",
                        "index_sets": ["period"],
                        "values": [
                            {"coordinates": ["p1"], "value": 2.0, "source_ref": "input_catalog.json#table-1/r1c2"},
                            {"coordinates": ["p2"], "value": 3.0, "source_ref": "input_catalog.json#table-1/r2c2"},
                        ],
                    },
                    {
                        "id": "demand",
                        "index_sets": ["period"],
                        "values": [
                            {"coordinates": ["p1"], "value": 4.0, "source_ref": "input_catalog.json#table-1/r1c3"},
                            {"coordinates": ["p2"], "value": 5.0, "source_ref": "input_catalog.json#table-1/r2c3"},
                        ],
                    },
                ],
                "variables": [
                    {
                        "id": "inventory",
                        "index_sets": ["period"],
                        "domain": "continuous",
                        "lower_bound": 0.0,
                    },
                    {
                        "id": "order",
                        "index_sets": ["period"],
                        "domain": "integer",
                        "lower_bound": 0.0,
                    },
                ],
                "objective": {
                    "sense": "minimize",
                    "terms": [{
                        "for_each": {"t": "period"},
                        "coefficient": {"kind": "parameter", "parameter_id": "unit_cost", "selectors": [{"binder": "t"}]},
                        "variable": {"variable_id": "order", "selectors": [{"binder": "t"}]},
                    }],
                },
                "constraints": [
                    {
                        "id": "initial_balance",
                        "terms": [
                            {"coefficient": {"kind": "constant", "value": 1.0}, "variable": {"variable_id": "inventory", "selectors": [{"value": "p1"}]}},
                            {"coefficient": {"kind": "constant", "value": -1.0}, "variable": {"variable_id": "order", "selectors": [{"value": "p1"}]}},
                        ],
                        "relation": "equal",
                        "rhs": {"kind": "parameter", "parameter_id": "demand", "selectors": [{"value": "p1"}], "multiplier": -1.0},
                    },
                    {
                        "id": "state_balance",
                        "for_each": {"t": "transition_period"},
                        "terms": [
                            {"coefficient": {"kind": "constant", "value": 1.0}, "variable": {"variable_id": "inventory", "selectors": [{"binder": "t"}]}},
                            {"coefficient": {"kind": "constant", "value": -1.0}, "variable": {"variable_id": "inventory", "selectors": [{"binder": "t", "offset": -1}]}},
                            {"coefficient": {"kind": "constant", "value": -1.0}, "variable": {"variable_id": "order", "selectors": [{"binder": "t"}]}},
                        ],
                        "relation": "equal",
                        "rhs": {"kind": "parameter", "parameter_id": "demand", "selectors": [{"binder": "t"}], "multiplier": -1.0},
                    },
                ],
            },
                "solver_node_id": "solve",
                "outputs": ["solution"],
            }],
            "candidate_models": [
                {"id": "baseline", "role": "baseline", "operator_node_ids": ["solve"], "selection_metric": "objective", "applicability_boundary": "Simple feasible policy."},
                {"id": "recommended", "role": "recommended", "operator_node_ids": ["solve"], "selection_metric": "objective", "applicability_boundary": "Finite linear mixed-integer model."},
            ],
            "validation": {
                "model_families": ["optimization"],
                "identifiability_checks": ["finite_index_sets"],
                "evidence_checks": ["source_cells_registered"],
                "robustness_checks": ["baseline_comparison"],
            },
            "final_answers": [{
                "id": "answer",
                "item": "Optimal indexed decision",
                "value_ref": "solution",
                "status": "candidate_design",
                "evidence_ids": ["source"],
            }],
        }],
    }


def _component_payload() -> dict:
    return _payload()


def _indexed_model(payload: dict) -> dict:
    return next(
        component["model"]
        for component in payload["subproblems"][0]["execution_components"]
        if component["kind"] == "indexed_linear"
    )


def _add_forecast_component(
    subproblem: dict,
    *,
    inputs: dict[str, str] | None = None,
) -> None:
    subproblem["execution_components"].insert(0, {
        "kind": "operator_graph",
        "component_id": "demand_forecast",
        "variables": [{
            "id": "forecast_bundle",
            "role": "derived",
            "symbol": "F",
            "unit": "unspecified",
            "shape": ["artifact", "field", "value"],
        }],
        "operators": [{
            "node_id": "forecast",
            "operator_id": "forecast.test",
            "inputs": inputs or {},
            "outputs": ["forecast_bundle"],
            "parameters": {},
        }],
    })


def test_core_accepts_runtime_parameter_in_objective_before_materialization() -> None:
    payload = _indexed_model(_payload())
    payload.pop("constraints")
    payload["parameters"][0] = {
        "id": "unit_cost",
        "index_sets": ["period"],
        "runtime_source": {
            "artifact_ref": "forecast_bundle",
            "field_path": ["future_predictions"],
        },
    }
    core = IndexedLinearCoreSpec.model_validate(payload)

    issues = validate_indexed_linear_core(core)

    assert issues == []
    assert core.parameters[0].runtime_source is not None
    assert core.parameters[0].dense_values == []


def test_compiler_consumes_indexed_execution_component() -> None:
    model_ir = ModelIR.model_validate(_component_payload())

    result = ModelIRCompiler(registry=_registry()).compile(model_ir)

    assert result.status == "compiled"
    assert result.execution_plan is not None
    assert [node.node_id for node in result.execution_plan.graph.nodes] == ["solve"]


def _compile_runtime_demand_plan():
    payload = _payload()
    subproblem = payload["subproblems"][0]
    demand = _indexed_model(payload)["parameters"][1]
    demand.pop("values")
    demand["runtime_source"] = {
        "artifact_ref": "forecast_bundle",
        "field_path": ["future_predictions"],
    }
    _add_forecast_component(subproblem)
    registry = build_default_operator_registry()
    registry.register(OperatorDefinition(
        operator_id="forecast.test",
        version="1.0.0",
        inputs={},
        outputs={"forecast": OperatorPort(value_kind="table", unit="unspecified")},
        deterministic=True,
        validator_id="forecast.test.validator.v1",
    ))
    compilation = ModelIRCompiler(registry=registry).compile(ModelIR.model_validate(payload))
    assert compilation.status == "compiled", compilation.diagnostics
    return registry, compilation.execution_plan.graph.nodes[1]


def test_indexed_linear_ir_compiles_finite_domains_and_state_transitions() -> None:
    result = ModelIRCompiler(registry=_registry()).compile(ModelIR.model_validate(_payload()))

    assert result.status == "compiled"
    node = result.execution_plan.graph.nodes[0]
    compiled = node.parameters["compiled_model"]
    assert compiled["variable_labels"] == [
        "inventory[p1]",
        "inventory[p2]",
        "order[p1]",
        "order[p2]",
    ]
    assert compiled["integrality"] == [0, 0, 1, 1]
    assert compiled["objective"] == [0.0, 0.0, 2.0, 3.0]
    assert compiled["constraint_labels"] == ["initial_balance", "state_balance[t=p2]"]
    assert compiled["matrix"] == [
        [1.0, 0.0, -1.0, 0.0],
        [-1.0, 1.0, 0.0, -1.0],
    ]
    assert compiled["constraint_lower"] == [-4.0, -5.0]
    assert compiled["constraint_upper"] == [-4.0, -5.0]


def test_indexed_linear_ir_expands_dense_parameter_values_with_shared_provenance() -> None:
    payload = _payload()
    unit_cost = _indexed_model(payload)["parameters"][0]
    unit_cost["dense_values"] = [2.0, 3.0]
    unit_cost["dense_source_ref"] = "input_catalog.json#table-1/unit-cost-row-major"
    unit_cost.pop("values")

    result = ModelIRCompiler(registry=_registry()).compile(ModelIR.model_validate(payload))

    assert result.status == "compiled", result.diagnostics
    compiled = result.execution_plan.graph.nodes[0].parameters["compiled_model"]
    assert compiled["objective"] == [0.0, 0.0, 2.0, 3.0]
    assert {
        item["source_ref"]
        for item in compiled["provenance"]
        if item["parameter_id"] == "unit_cost"
    } == {"input_catalog.json#table-1/unit-cost-row-major"}


def test_indexed_linear_ir_compiles_runtime_parameter_dependency_before_milp() -> None:
    payload = _component_payload()
    subproblem = payload["subproblems"][0]
    indexed_component = subproblem["execution_components"][0]
    demand = indexed_component["model"]["parameters"][1]
    demand.pop("values")
    demand["runtime_source"] = {
        "artifact_ref": "forecast_bundle",
        "field_path": ["future_predictions"],
    }
    subproblem["execution_components"].insert(0, {
        "kind": "operator_graph",
        "component_id": "demand_forecast",
        "variables": [{
            "id": "forecast_bundle",
            "role": "derived",
            "symbol": "F",
            "unit": "unspecified",
            "shape": ["artifact", "field", "value"],
        }],
        "operators": [{
            "node_id": "forecast",
            "operator_id": "forecast.test",
            "inputs": {},
            "outputs": ["forecast_bundle"],
            "parameters": {},
        }],
    })

    registry = _registry()
    registry.register(OperatorDefinition(
        operator_id="forecast.test",
        version="1.0.0",
        inputs={},
        outputs={"forecast": OperatorPort(value_kind="table", unit="unspecified")},
        deterministic=True,
        validator_id="forecast.test.validator.v1",
    ))
    result = ModelIRCompiler(registry=registry).compile(ModelIR.model_validate(payload))

    assert result.status == "compiled", result.diagnostics
    assert [node.node_id for node in result.execution_plan.graph.nodes] == [
        "forecast",
        "solve",
    ]
    solve = result.execution_plan.graph.nodes[1]
    assert solve.dependencies == ["forecast"]
    assert solve.inputs == {"demand": "forecast_bundle"}
    template = solve.parameters["indexed_model_template"]
    runtime_parameter = next(
        parameter for parameter in template["parameters"]
        if parameter["id"] == "demand"
    )
    assert runtime_parameter["runtime_source"] == {
        "artifact_ref": "forecast_bundle",
        "field_path": ["future_predictions"],
    }


def test_runtime_parameter_model_gets_index_diagnostics_before_execution() -> None:
    payload = _payload()
    subproblem = payload["subproblems"][0]
    demand = _indexed_model(payload)["parameters"][1]
    demand.pop("values")
    demand["runtime_source"] = {
        "artifact_ref": "forecast_bundle",
        "field_path": ["future_predictions"],
    }
    _add_forecast_component(subproblem)
    _indexed_model(payload)["constraints"][1]["rhs"]["selectors"] = [{
        "binder": "unknown_period",
    }]
    registry = _registry()
    registry.register(OperatorDefinition(
        operator_id="forecast.test",
        version="1.0.0",
        inputs={},
        outputs={"forecast": OperatorPort(value_kind="table", unit="unspecified")},
        deterministic=True,
        validator_id="forecast.test.validator.v1",
    ))

    result = ModelIRCompiler(registry=registry).compile(
        ModelIR.model_validate(payload)
    )

    assert result.status == "blocked"
    assert "indexed_unknown_binder" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_indexed_linear_ir_rejects_unknown_runtime_parameter_artifact() -> None:
    payload = _payload()
    demand = _indexed_model(payload)["parameters"][1]
    demand.pop("values")
    demand["runtime_source"] = {
        "artifact_ref": "missing_forecast_bundle",
        "field_path": ["future_predictions"],
    }

    result = ModelIRCompiler(registry=_registry()).compile(ModelIR.model_validate(payload))

    assert result.status == "blocked"
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "indexed_runtime_source_unknown_artifact",
    }


def test_indexed_linear_ir_rejects_runtime_parameter_dependency_cycle() -> None:
    payload = _payload()
    subproblem = payload["subproblems"][0]
    demand = _indexed_model(payload)["parameters"][1]
    demand.pop("values")
    demand["runtime_source"] = {
        "artifact_ref": "forecast_bundle",
        "field_path": ["future_predictions"],
    }
    _add_forecast_component(subproblem, inputs={"solution": "solution"})

    registry = _registry()
    registry.register(OperatorDefinition(
        operator_id="forecast.test",
        version="1.0.0",
        inputs={"solution": OperatorPort(value_kind="artifact", unit="unspecified")},
        outputs={"forecast": OperatorPort(value_kind="table", unit="unspecified")},
        deterministic=True,
        validator_id="forecast.test.validator.v1",
    ))
    result = ModelIRCompiler(registry=registry).compile(ModelIR.model_validate(payload))

    assert result.status == "blocked"
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "operator_cycle",
    }


def test_milp_materializes_runtime_parameter_from_upstream_artifact(
    tmp_path: Path,
) -> None:
    registry, solve = _compile_runtime_demand_plan()
    runtime = registry.runtime("optimization.milp")

    outputs = runtime.executor(
        inputs={"demand": {"future_predictions": [4.0, 5.0]}},
        parameters=solve.parameters,
        random_seed=None,
        work_dir=tmp_path,
    )

    solution = outputs["solution"]
    assert solution["compiled_model"]["constraint_lower"] == [-4.0, -5.0]
    assert solution["decision_values"]["order[p1]"] == 9.0
    assert solution["decision_values"]["order[p2]"] == 0.0
    assert solution["objective_value"] == 18.0
    demand_sources = {
        item["source_ref"]
        for item in solution["provenance"]
        if item["parameter_id"] == "demand"
    }
    assert demand_sources == {"artifact:forecast_bundle#/future_predictions"}


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ({}, "runtime parameter 'demand'.*future_predictions"),
        ({"future_predictions": [4.0]}, "runtime parameter 'demand'.*expected 2"),
        (
            {"future_predictions": [4.0, float("nan")]},
            "runtime parameter 'demand'.*finite",
        ),
    ],
)
def test_milp_rejects_invalid_runtime_parameter_before_solver(
    tmp_path: Path,
    artifact: dict,
    message: str,
) -> None:
    registry, solve = _compile_runtime_demand_plan()
    runtime = registry.runtime("optimization.milp")

    with pytest.raises(ValueError, match=message):
        runtime.executor(
            inputs={"demand": artifact},
            parameters=solve.parameters,
            random_seed=None,
            work_dir=tmp_path,
        )


def test_indexed_linear_ir_rejects_dimension_and_offset_errors_before_execution() -> None:
    payload = _payload()
    model = _indexed_model(payload)
    model["objective"]["terms"][0]["variable"]["selectors"] = []
    model["constraints"][1]["terms"][1]["variable"]["selectors"][0]["offset"] = -2

    result = ModelIRCompiler(registry=_registry()).compile(ModelIR.model_validate(deepcopy(payload)))

    assert result.status == "blocked"
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "indexed_dimension_mismatch",
        "indexed_offset_out_of_range",
    }


def test_generic_multiperiod_inventory_fixture_closes_delay_state_and_tiered_costs(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    model_ir = ModelIR.model_validate_json(fixture.read_text(encoding="utf-8"))
    operator_registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=operator_registry).compile(model_ir)

    assert compilation.status == "compiled", compilation.diagnostics
    node = compilation.execution_plan.graph.nodes[0]
    runtime = operator_registry.runtime("optimization.milp")
    outputs = runtime.executor(
        inputs={},
        parameters=node.parameters,
        random_seed=None,
        work_dir=tmp_path,
    )
    assert runtime.validator(outputs=outputs) == []
    solution = outputs["solution"]
    decisions = solution["decision_values"]

    assert decisions["commissioned[p1]"] == decisions["purchase[p0]"]
    assert decisions["commissioned[p2]"] == decisions["purchase[p1]"]
    assert [decisions[f"loss_state[p{index}]"] for index in range(3)] == [1.0, 0.0, 1.0]
    assert [decisions[f"maintenance_state[p{index}]"] for index in range(3)] == [0.0, 1.0, 0.0]
    for index in range(3):
        purchase = decisions[f"purchase[p{index}]"]
        tier_one = decisions[f"tier_one[p{index}]"]
        tier_two = decisions[f"tier_two[p{index}]"]
        assert purchase == tier_one + tier_two
        assert tier_one <= 2.0
        assert purchase <= 20.0 * decisions[f"setup[p{index}]"]

    assessment = build_default_model_validator_registry().assess(
        family="mixed_integer_linear_optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[solution],
    )
    assert assessment.status == "passed", assessment.reasons
    assert all(check.status == "passed" for check in assessment.checks.values())
    parameter_ids = {item["parameter_id"] for item in solution["provenance"]}
    assert {"demand", "loss", "maintenance", "tier_one_cost", "tier_two_cost"} <= parameter_ids


def test_static_parameter_tampered_values_are_rejected_against_locked_facts_and_evidence_tables() -> None:
    from app.guidance.contracts import ProblemSpec
    from app.guidance.evidence_consistency import validate_model_evidence_consistency

    problem_spec = ProblemSpec.model_validate({
        "task_summary": "Verify static parameter values against raw locked facts and tables.",
        "subproblems": [{
            "id": "finite_horizon_allocation",
            "objective": "Minimize indexed acquisition cost subject to state conservation.",
            "required_fact_ids": [
                "fact.unit_cost_p1",
                "fact.unit_cost_p2",
                "fact.demand_table",
            ],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        }],
        "data_sources": [{
            "id": "demand_table",
            "description": "Registered period cost and demand table.",
            "source_ref": "input_catalog.json#table-1",
        }],
        "locked_facts": [
            {
                "fact_id": "fact.unit_cost_p1",
                "statement": "Unit cost in period p1 is 2.0.",
                "source_refs": ["input_catalog.json#paragraph-1"],
                "subproblem_ids": ["finite_horizon_allocation"],
                "semantic_key": "cost.unit_p1",
                "value": 2.0,
                "unit": "yuan/unit",
            },
            {
                "fact_id": "fact.unit_cost_p2",
                "statement": "Unit cost in period p2 is 3.0.",
                "source_refs": ["input_catalog.json#paragraph-1"],
                "subproblem_ids": ["finite_horizon_allocation"],
                "semantic_key": "cost.unit_p2",
                "value": 3.0,
                "unit": "yuan/unit",
            },
            {
                "fact_id": "fact.demand_table",
                "statement": "Period demand comes from table-1.",
                "source_refs": ["input_catalog.json#table-1"],
                "subproblem_ids": ["finite_horizon_allocation"],
                "semantic_key": "demand.table",
                "value": "registered_dataset",
                "unit": "unit",
            },
        ],
        "uncertainties": [],
    })
    evidence_profile = {
        "schema_version": "1.0",
        "evidence": [{
            "evidence_id": "question.table.1",
            "kind": "embedded_table",
            "source_ref": "input_catalog.json#table-1",
            "source_file": "inputs/problem.docx",
            "labels": ["Table 1"],
            "sha256": "a" * 64,
            "size_bytes": 64,
            "rows": [
                ["period", "unit_cost", "demand"],
                ["p1", "2.0", "4.0"],
                ["p2", "3.0", "5.0"],
            ],
            "profile": {"row_count": 3, "column_count": 3},
        }],
    }

    valid_payload = _payload()
    valid_model = _indexed_model(valid_payload)
    valid_model["parameters"][0]["source_fact_ids"] = [
        "fact.unit_cost_p1",
        "fact.unit_cost_p2",
    ]
    valid_model["parameters"][1]["source_fact_ids"] = ["fact.demand_table"]
    valid_ir = ModelIR.model_validate(valid_payload)

    validate_model_evidence_consistency(problem_spec, valid_ir, evidence_profile)
    valid_compilation = ModelIRCompiler(registry=_registry()).compile(
        valid_ir,
        problem_spec=problem_spec,
        evidence_profile=evidence_profile,
    )
    assert valid_compilation.status == "compiled", valid_compilation.diagnostics

    tampered_fact_payload = deepcopy(valid_payload)
    _indexed_model(tampered_fact_payload)["parameters"][0]["values"][0]["value"] = 999.0
    tampered_fact_ir = ModelIR.model_validate(tampered_fact_payload)

    with pytest.raises(ValueError, match="indexed_parameter_evidence_mismatch"):
        validate_model_evidence_consistency(
            problem_spec,
            tampered_fact_ir,
            evidence_profile,
        )
    tampered_fact_compilation = ModelIRCompiler(registry=_registry()).compile(
        tampered_fact_ir,
        problem_spec=problem_spec,
        evidence_profile=evidence_profile,
    )
    assert tampered_fact_compilation.status == "blocked"
    assert "indexed_parameter_evidence_mismatch" in {
        item.code for item in tampered_fact_compilation.diagnostics
    }

    tampered_table_payload = deepcopy(valid_payload)
    demand_param = _indexed_model(tampered_table_payload)["parameters"][1]
    demand_param.pop("values")
    demand_param["dense_values"] = [4.0, 999.0]
    demand_param["dense_source_ref"] = "input_catalog.json#table-1/demand"
    tampered_table_ir = ModelIR.model_validate(tampered_table_payload)

    with pytest.raises(ValueError, match="indexed_parameter_evidence_mismatch"):
        validate_model_evidence_consistency(
            problem_spec,
            tampered_table_ir,
            evidence_profile,
        )
    tampered_table_compilation = ModelIRCompiler(registry=_registry()).compile(
        tampered_table_ir,
        problem_spec=problem_spec,
        evidence_profile=evidence_profile,
    )
    assert tampered_table_compilation.status == "blocked"
    assert "indexed_parameter_evidence_mismatch" in {
        item.code for item in tampered_table_compilation.diagnostics
    }

    from app.guidance.fact_coverage import FactCoverage, FactScope
    from app.guidance.generation_validation import _validate_indexed_core_stage
    from app.guidance.model_ir_drafts import (IndexedLinearCoreDraft)

    core_payload = {
        "index_sets": [{"id": "period", "values": ["p1", "p2"]}],
        "parameters": [{
            "source_kind": "static",
            "id": "unit_cost",
            "index_sets": ["period"],
            "binding": {
                "kind": "table_column",
                "source_ref": "input_catalog.json#table-1",
                "column": "unit_cost",
            },
            "source_fact_ids": ["fact.unit_cost_p1", "fact.unit_cost_p2"],
        }],
        "variables": [{
            "id": "order",
            "index_sets": ["period"],
            "domain": "integer",
            "lower_bound": 0.0,
        }],
        "objective": {
            "sense": "minimize",
            "terms": [{
                "for_each": {"t": "period"},
                "coefficient": {
                    "kind": "parameter",
                    "parameter_id": "unit_cost",
                    "selectors": [{"binder": "t"}],
                },
                "variable": {
                    "variable_id": "order",
                    "selectors": [{"binder": "t"}],
                },
            }],
        },
    }
    core_draft = IndexedLinearCoreDraft.model_validate(core_payload)
    _validate_indexed_core_stage(
        core_draft,
        FactCoverage(FactScope.from_problem_spec(problem_spec, problem_spec.subproblems[0].id)),
        evidence_profile=evidence_profile,
    )
    materialized = core_draft.promote_core(
        locked_facts=problem_spec.locked_facts, evidence_profile=evidence_profile,
    )
    assert [item.value for item in materialized.parameters[0].values] == [2.0, 3.0]
    materialized.parameters[0].values[0].value = 999.0
    from app.guidance.parameter_evidence import validate_static_parameter_evidence

    errors = validate_static_parameter_evidence(
        materialized, locked_facts=problem_spec.locked_facts, evidence_profile=evidence_profile,
    )
    assert any("disagree with ordered source" in error for error in errors)
    core_payload["parameters"][0]["dense_values"] = [999.0, 3.0]
    with pytest.raises(ValueError, match="dense_values"):
        IndexedLinearCoreDraft.model_validate(core_payload)
