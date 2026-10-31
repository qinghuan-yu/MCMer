import pytest
from pydantic import ValidationError


def _architecture_payload(*, execution_components, variables=None, operators=None):
    return {
        "schema_version": "1.0",
        "model_id": "fact_provenance_contract",
        "subproblems": [{
            "execution_status": "solvable",
            "execution_components": execution_components,
            "subproblem_id": "problem1",
            "objective": "Produce one evidence-bound result.",
            "variables": variables or [],
            "data": [{
                "id": "observations",
                "source_ref": "input_catalog.json#/tables/0",
                "fields": {"value": "value"},
            }],
            "operators": operators or [],
            "assumptions": [],
            "candidate_models": [
                {
                    "id": "baseline",
                    "role": "baseline",
                    "operator_node_ids": (
                        ["estimate"] if "operator_graph" in execution_components else []
                    ),
                    "selection_metric": "objective_value",
                    "applicability_boundary": "Registered input evidence.",
                },
                {
                    "id": "recommended",
                    "role": "recommended",
                    "operator_node_ids": (
                        ["estimate"] if "operator_graph" in execution_components else []
                    ),
                    "selection_metric": "objective_value",
                    "applicability_boundary": "Registered input evidence.",
                },
            ],
            "validation": {
                "model_families": ["optimization"],
                "identifiability_checks": ["rank_check"],
                "evidence_checks": ["source_replay"],
                "robustness_checks": ["coefficient_perturbation"],
            },
            "final_answers": [{
                "id": "answer",
                "item": "Evidence-bound result",
                "value_ref": "result",
                "status": "evidence_verified",
                "evidence_ids": ["fact.input"],
            }],
        }],
    }


def _indexed_core_and_constraints():
    from app.guidance.model_ir import (
        IndexedLinearConstraintDraft,
        IndexedLinearCoreDraft,
    )

    core = IndexedLinearCoreDraft.model_validate({
        "index_sets": [{"id": "period", "values": [1]}],
        "parameters": [{
            "source_kind": "static",
            "id": "demand",
            "index_sets": ["period"],
            "dense_values": [3.0],
            "dense_source_ref": "input_catalog.json#/tables/0/rows/0/cells/0",
            "source_fact_ids": ["fact.parameter"],
        }],
        "variables": [{
            "id": "quantity",
            "index_sets": ["period"],
            "domain": "continuous",
            "lower_bound": 0.0,
        }],
        "objective": {
            "sense": "minimize",
            "terms": [{
                "for_each": {"t": "period"},
                "coefficient": {"kind": "constant", "value": 1.0},
                "variable": {
                    "variable_id": "quantity",
                    "selectors": [{"binder": "t"}],
                },
            }],
        },
    })
    constraint = IndexedLinearConstraintDraft.model_validate({
        "id": "meet_demand",
        "for_each": {"t": "period"},
        "terms": [{
            "coefficient": {"kind": "constant", "value": 1.0},
            "variable": {
                "variable_id": "quantity",
                "selectors": [{"binder": "t"}],
            },
        }],
        "relation": "greater_equal",
        "rhs": {
            "kind": "parameter",
            "parameter_id": "demand",
            "selectors": [{"binder": "t"}],
        },
        "source_fact_ids": ["fact.constraint"],
    })
    return core, constraint


def test_architecture_draft_excludes_guessed_fact_binding_paths():
    from app.guidance.model_ir import SolvableModelIRDraft

    assert "fact_bindings" not in SolvableModelIRDraft.model_fields


def test_operator_graph_nodes_generate_final_fact_binding_paths():
    from app.guidance.model_ir import ModelIRGenerationDraft

    draft = ModelIRGenerationDraft.model_validate(_architecture_payload(
        execution_components=["operator_graph"],
        variables=[{
            "id": "result",
            "role": "derived",
            "symbol": "r",
            "unit": "dimensionless",
            "shape": [],
        }],
        operators=[{
            "node_id": "estimate",
            "operator_id": "statistics.estimate",
            "inputs": {"observations": "observations.value"},
            "outputs": ["result"],
            "source_fact_ids": ["fact.input"],
        }],
    ))

    promoted = draft.promote()

    assert promoted.subproblems[0].fact_bindings[0].model_dump() == {
        "fact_id": "fact.input",
        "model_paths": ["/execution_components/0/operators/0"],
    }


def test_indexed_nodes_generate_parameter_and_constraint_fact_binding_paths():
    from app.guidance.model_ir import ModelIRGenerationDraft

    core, constraint = _indexed_core_and_constraints()
    indexed_model = core.promote(constraints=[constraint.promote()])
    draft = ModelIRGenerationDraft.model_validate(_architecture_payload(
        execution_components=["indexed_linear_model"],
    ))

    promoted = draft.promote(indexed_linear_model=indexed_model)

    assert [
        binding.model_dump()
        for binding in promoted.subproblems[0].fact_bindings
    ] == [
        {
            "fact_id": "fact.parameter",
            "model_paths": ["/execution_components/0/model/parameters/0"],
        },
        {
            "fact_id": "fact.constraint",
            "model_paths": ["/execution_components/0/model/constraints/0"],
        },
    ]


def test_node_source_fact_ids_must_be_unique():
    from app.guidance.model_ir import OperatorInvocation

    with pytest.raises(ValidationError, match="source_fact_ids must be unique"):
        OperatorInvocation.model_validate({
            "node_id": "estimate",
            "operator_id": "statistics.estimate",
            "outputs": ["result"],
            "source_fact_ids": ["fact.input", "fact.input"],
        })


def test_remaining_locked_facts_preserve_order_and_reject_unknown_sources():
    from app.guidance.agents import _remaining_locked_facts

    locked_facts = [
        {"fact_id": "fact.operator"},
        {"fact_id": "fact.parameter"},
        {"fact_id": "fact.constraint"},
    ]

    assert _remaining_locked_facts(
        locked_facts,
        ["fact.operator"],
    ) == [
        {"fact_id": "fact.parameter"},
        {"fact_id": "fact.constraint"},
    ]
    with pytest.raises(ValueError, match="unknown locked fact ids"):
        _remaining_locked_facts(locked_facts, ["fact.unknown"])


def test_modeling_compaction_preserves_every_scoped_locked_fact():
    from app.guidance.agents import _compact_problem_spec_for_modeling
    from app.guidance.contracts import ProblemSpec

    facts = [
        {
            "fact_id": f"fact.input_{index}",
            "statement": f"Input {index} is locked.",
            "source_refs": [f"inputs/problem.docx#paragraph-{index}"],
            "subproblem_ids": ["problem1"],
            "semantic_key": f"input.value_{index}",
            "value": index,
            "unit": "unit",
        }
        for index in range(1, 15)
    ]
    compact = _compact_problem_spec_for_modeling(ProblemSpec(
        task_summary="Use every registered input.",
        subproblems=[{
            "id": "problem1",
            "objective": "Build the model.",
            "required_fact_ids": [fact["fact_id"] for fact in facts],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        }],
        locked_facts=facts,
    ))

    assert [fact["fact_id"] for fact in compact["locked_facts"]] == [
        fact["fact_id"] for fact in facts
    ]
    assert compact["subproblems"][0]["required_fact_ids"] == [
        fact["fact_id"] for fact in facts
    ]


def test_hybrid_candidates_may_wait_for_the_system_managed_solver_node():
    from app.guidance.model_ir import ModelIRGenerationDraft

    payload = _architecture_payload(
        execution_components=["operator_graph", "indexed_linear_model"],
        variables=[{
            "id": "forecast",
            "role": "derived",
            "symbol": "y_hat",
            "unit": "unit",
            "shape": [],
        }],
        operators=[{
            "node_id": "estimate",
            "operator_id": "forecast.future_basis_rolling_origin",
            "outputs": ["forecast"],
            "source_fact_ids": ["fact.input"],
        }],
    )
    candidates = payload["subproblems"][0]["candidate_models"]
    candidates[0]["operator_node_ids"] = []
    candidates[1]["operator_node_ids"] = ["estimate"]

    draft = ModelIRGenerationDraft.model_validate(payload)
    normalized = draft.normalize_indexed_execution().subproblems[0]

    assert normalized.candidate_models[0].operator_node_ids == ["solve_indexed_model"]
    assert normalized.candidate_models[1].operator_node_ids == [
        "estimate",
        "solve_indexed_model",
    ]
