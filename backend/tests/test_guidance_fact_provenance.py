import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


def _architecture_payload(*, execution_components, model_families, variables=None, operators=None):
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
                "model_families": model_families,
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


def _quantity_variables():
    return [{"id": "quantity", "index_sets": ["period"], "domain": "continuous", "lower_bound": 0.0}]


def _quantity_objective():
    return {"sense": "minimize", "terms": [{
        "for_each": {"t": "period"},
        "coefficient": {"kind": "constant", "value": 1.0},
        "variable": {"variable_id": "quantity", "selectors": [{"binder": "t"}]},
    }]}


def _indexed_core_and_constraints():
    from app.guidance.model_ir_drafts import (IndexedLinearConstraintDraft, IndexedLinearCoreDraft)

    core = IndexedLinearCoreDraft.model_validate({
        "index_sets": [{"id": "period", "values": [1]}],
        "parameters": [{
            "source_kind": "static",
            "id": "demand",
            "index_sets": ["period"],
            "binding": {"kind": "fact", "fact_id": "fact.parameter"},
            "source_fact_ids": ["fact.parameter"],
        }],
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
    from app.guidance.model_ir_drafts import (SolvableModelIRDraft)

    assert "fact_bindings" not in SolvableModelIRDraft.model_fields


def test_operator_graph_nodes_generate_final_fact_binding_paths():
    from app.guidance.model_ir_drafts import (ModelIRGenerationDraft)

    draft = ModelIRGenerationDraft.model_validate(_architecture_payload(
        execution_components=["operator_graph"],
        model_families=["regression"],
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
    from app.guidance.model_ir_drafts import (ModelIRGenerationDraft)

    core, constraint = _indexed_core_and_constraints()
    from app.guidance.model_ir_drafts import IndexedLinearFormulationDraft

    materialized = core.promote_core(locked_facts=[{"fact_id": "fact.parameter", "value": 3}])
    indexed_model = IndexedLinearFormulationDraft(variables=_quantity_variables(), objective=_quantity_objective(), constraints=[constraint]).promote(materialized)
    draft = ModelIRGenerationDraft.model_validate(_architecture_payload(
        execution_components=["indexed_linear_model"],
        model_families=["mixed_integer_linear_optimization"],
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


def test_indexed_promotion_preserves_all_paths_for_a_reused_fact():
    from app.guidance.model_ir_drafts import IndexedLinearFormulationDraft, ModelIRGenerationDraft

    core, constraint = _indexed_core_and_constraints()
    materialized = core.promote_core(locked_facts=[{"fact_id": "fact.parameter", "value": 3}])
    formulation = IndexedLinearFormulationDraft(
        variables=_quantity_variables(), objective=_quantity_objective(),
        constraints=[constraint.model_copy(update={
            "id": f"meet_demand_{index}", "source_fact_ids": ["fact.parameter"],
        }) for index in range(20)],
    )
    draft = ModelIRGenerationDraft.model_validate(_architecture_payload(
        execution_components=["indexed_linear_model"],
        model_families=["mixed_integer_linear_optimization"],
    ))

    promoted = draft.promote(indexed_linear_model=formulation.promote(materialized))

    expected_paths = [
        "/execution_components/0/model/parameters/0",
        *[f"/execution_components/0/model/constraints/{index}" for index in range(20)],
    ]
    assert len(expected_paths) == len(set(expected_paths)) == 21
    assert [binding.model_dump() for binding in promoted.subproblems[0].fact_bindings] == [{
        "fact_id": "fact.parameter", "model_paths": expected_paths,
    }]


def test_canonical_fact_binding_rejects_duplicate_paths():
    from app.guidance.model_ir import FactBindingSpec

    with pytest.raises(ValidationError, match="model_paths must be unique"):
        FactBindingSpec(
            fact_id="fact.parameter",
            model_paths=["/execution_components/0/model/parameters/0"] * 2,
        )


def test_coverage_preserves_fact_snapshot_and_rejects_unknown_sources():
    from app.guidance.fact_coverage import FactCoverage, FactScope

    spec, _, _, _ = _shared_fact_generation()
    original = spec.locked_facts[0].model_dump(mode="json")
    scope = FactScope.from_problem_spec(spec, "problem1")
    coverage = FactCoverage(scope)
    covered = coverage.with_stage("architecture", [("estimate", ["fact.parameter"])])

    assert coverage.uncovered_fact_ids == ("fact.parameter",)
    assert covered.uncovered_fact_ids == ()
    assert scope.payload() == [original]
    with pytest.raises(ValueError, match="unknown locked fact ids"):
        covered.with_stage("indexed_core", [("demand", ["fact.unknown"])])
    assert covered.uncovered_fact_ids == ()

    # 外部对象和派发副本的修改都不能改变后续生成所见的事实。
    spec.locked_facts[0].value = 99
    scope.payload()[0]["source_refs"].clear()
    assert scope.payload() == [original]
    replaced = covered.with_stage("architecture", [("estimate", [])])
    assert replaced.uncovered_fact_ids == ("fact.parameter",)
    assert covered.uncovered_fact_ids == ()


def test_modeling_compaction_preserves_every_scoped_locked_fact():
    from app.guidance.modeling_context import _compact_problem_spec_for_modeling
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


def test_modeling_compaction_preserves_subproblem_tail_rules_and_list_items():
    from app.guidance.modeling_context import _compact_problem_spec_for_modeling
    from app.guidance.contracts import ProblemSpec

    long_description = "A" * 320 + " TAIL_RULE_MUST_SURVIVE"
    long_objective = "B" * 300 + " OBJECTIVE_TAIL_MUST_SURVIVE"
    long_statement = "C" * 300 + " FACT_STATEMENT_TAIL_MUST_SURVIVE"
    constraints = [
        f"Constraint {index}: " + ("D" * 190) + f" RULE_{index}"
        for index in range(1, 13)
    ]
    compact = _compact_problem_spec_for_modeling(ProblemSpec(
        task_summary="Preserve every subproblem rule without truncation.",
        subproblems=[{
            "id": "problem1",
            "title": "Subproblem Title",
            "brief_description": "Brief summary",
            "objective": long_objective,
            "description": long_description,
            "constraints": constraints,
            "required_fact_ids": ["fact.long_statement"],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        }],
        locked_facts=[{
            "fact_id": "fact.long_statement",
            "statement": long_statement,
            "source_refs": ["inputs/problem.docx#paragraph-1"],
            "subproblem_ids": ["problem1"],
            "semantic_key": "rule.tail",
            "value": 42,
            "unit": "unit",
        }],
    ))

    subproblem = compact["subproblems"][0]
    assert subproblem["description"] == long_description
    assert subproblem["objective"] == long_objective
    assert subproblem["title"] == "Subproblem Title"
    assert subproblem["brief_description"] == "Brief summary"
    assert subproblem["constraints"] == constraints
    assert compact["locked_facts"][0]["statement"] == long_statement



def test_hybrid_candidates_may_wait_for_the_system_managed_solver_node():
    from app.guidance.model_ir_drafts import (ModelIRGenerationDraft)

    payload = _architecture_payload(
        execution_components=["operator_graph", "indexed_linear_model"],
        model_families=["future_forecast", "mixed_integer_linear_optimization"],
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


class _StageLLM:
    def __init__(self, architecture, core, constraints):
        self.responses = {
            "architecture": architecture,
            "indexed_core": core,
            "indexed_constraints": constraints,
        }
        self.calls = []
        self.attempts = {}

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        stage = next((
            name for name in ("indexed_core", "indexed_constraints")
            if kwargs["agent_name"].endswith(name)
        ), "architecture")
        attempt = self.attempts.get(stage, 0)
        self.attempts[stage] = attempt + 1
        responses = self.responses[stage]
        response = responses[min(attempt, len(responses) - 1)] if isinstance(responses, list) else responses
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(response),
        ))])


def _shared_fact_generation():
    from app.guidance.contracts import ProblemSpec

    fact_id = "fact.parameter"
    spec = ProblemSpec(
        task_summary="Meet one period of demand at minimum cost.",
        subproblems=[{
            "id": "problem1", "objective": "Meet demand.",
            "required_fact_ids": [fact_id], "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        }],
        locked_facts=[{
            "fact_id": fact_id, "statement": "Demand is 3 units.",
            "source_refs": ["inputs/problem.docx#paragraph-1"],
            "subproblem_ids": ["problem1"], "semantic_key": "demand.value",
            "value": 3, "unit": "unit",
        }],
    )
    architecture = _architecture_payload(
        execution_components=["operator_graph", "indexed_linear_model"],
        model_families=["mixed_integer_linear_optimization"],
        variables=[{
            "id": "result", "role": "derived", "symbol": "d",
            "unit": "unit", "shape": [],
        }],
        operators=[{
            "node_id": "estimate", "operator_id": "data.scalar_constant",
            "parameters": {"value": 3}, "outputs": ["result"],
            "source_fact_ids": [fact_id],
        }],
    )
    core, constraint = _indexed_core_and_constraints()
    constraint.source_fact_ids = [fact_id]
    constraints = {"variables": _quantity_variables(), "objective": _quantity_objective(), "additional_index_sets": [], "constraints": [constraint.model_dump()]}
    return spec, architecture, core.model_dump(mode="json"), constraints


def test_modeler_reuses_a_fact_in_architecture_parameters_and_constraints(tmp_path):
    from app.guidance.modeler import LLMModeler

    spec, architecture, core, constraints = _shared_fact_generation()
    original_spec = deepcopy(spec.model_dump(mode="json"))
    llm = _StageLLM(architecture, core, constraints)

    model = asyncio.run(LLMModeler(
        llm, available_operator_ids=["data.scalar_constant", "optimization.milp"],
    ).model(problem_spec=spec, task={}, work_dir=tmp_path))

    subproblem = model.subproblems[0]
    assert subproblem.execution_status == "solvable", subproblem.model_dump()
    assert len(llm.calls) == 3
    for call in llm.calls:
        payload = json.loads(call["history"][1]["content"])
        assert payload["locked_facts"] == original_spec["locked_facts"]
    assert spec.model_dump(mode="json") == original_spec
    assert [binding.model_dump() for binding in subproblem.fact_bindings] == [{
        "fact_id": "fact.parameter",
        "model_paths": [
            "/execution_components/0/operators/0",
            "/execution_components/1/model/parameters/0",
            "/execution_components/1/model/constraints/0",
        ],
    }]
    core_payload = json.loads(llm.calls[1]["history"][1]["content"])
    constraint_payload = json.loads(llm.calls[2]["history"][1]["content"])
    assert core_payload["fact_coverage"]["uncovered_fact_ids"] == []
    assert len(core_payload["fact_coverage"]["uses"]) == 1
    assert len(constraint_payload["fact_coverage"]["uses"]) == 2


@pytest.mark.parametrize("stage", ["architecture", "indexed_core", "indexed_constraints"])
@pytest.mark.parametrize("invalid_fact", ["fact.unknown", "fact.other_subproblem"])
def test_modeler_rejects_unknown_and_cross_scope_facts_at_every_stage(tmp_path, stage, invalid_fact):
    from app.guidance.contracts import ProblemSpec
    from app.guidance.modeler import LLMModeler

    spec, architecture, core, constraints = _shared_fact_generation()
    payload = spec.model_dump(mode="json")
    payload["subproblems"].append({
        "id": "problem2", "objective": "An independent problem.",
        "required_fact_ids": ["fact.other_subproblem"],
        "required_uncertainty_ids": [], "depends_on_subproblem_ids": [],
    })
    payload["locked_facts"].append({
        **payload["locked_facts"][0], "fact_id": "fact.other_subproblem",
        "subproblem_ids": ["problem2"], "semantic_key": "other.demand",
    })
    spec = ProblemSpec.model_validate(payload)
    node = {
        "architecture": architecture["subproblems"][0]["operators"][0],
        "indexed_core": core["parameters"][0],
        "indexed_constraints": constraints["constraints"][0],
    }[stage]
    node["source_fact_ids"].append(invalid_fact)
    llm = _StageLLM(architecture, core, constraints)

    result = asyncio.run(LLMModeler(
        llm, available_operator_ids=["data.scalar_constant", "optimization.milp"],
    ).model(problem_spec=spec, task={}, work_dir=tmp_path))

    assert result.subproblems[0].execution_status == "blocked"
    diagnostic = json.loads((tmp_path / "modeling_failure_problem1.json").read_text(encoding="utf-8"))
    assert "unknown locked fact ids" in diagnostic["error"]
    assert invalid_fact in diagnostic["error"]
    for call in llm.calls:
        if call["agent_name"].startswith("GuidanceModeler_problem1"):
            sent = json.loads(call["history"][1]["content"])
            scoped = sent.get("original_input", sent)["locked_facts"]
            assert [fact["fact_id"] for fact in scoped] == ["fact.parameter"]


@pytest.mark.parametrize("cover_at", ["architecture", "indexed_core", "indexed_constraints", None])
def test_final_coverage_uses_union_of_all_stages(tmp_path, cover_at):
    from app.guidance.modeler import LLMModeler

    spec, architecture, core, constraints = _shared_fact_generation()
    nodes = {
        "architecture": architecture["subproblems"][0]["operators"][0],
        "indexed_core": core["parameters"][0],
        "indexed_constraints": constraints["constraints"][0],
    }
    # 用表格核验参数，避免把来源核验失败误当作覆盖缺口。
    core["parameters"][0]["binding"] = {
        "kind": "table_column", "source_ref": "inputs/problem.docx#table-1", "column": "value",
    }
    evidence = {"evidence": [{
        "evidence_id": "table1", "kind": "embedded_table",
        "source_ref": "inputs/problem.docx#table-1", "source_file": "inputs/problem.docx",
        "labels": [], "sha256": "a" * 64, "size_bytes": 10,
        "rows": [["value"], [3]], "profile": {},
    }]}
    for stage, node in nodes.items():
        node["source_fact_ids"] = ["fact.parameter"] if stage == cover_at else []

    result = asyncio.run(LLMModeler(
        _StageLLM(architecture, core, constraints),
        available_operator_ids=["data.scalar_constant", "optimization.milp"],
    ).model(problem_spec=spec, evidence_profile=evidence, task={}, work_dir=tmp_path))

    if cover_at is None:
        assert result.subproblems[0].execution_status == "blocked"
        diagnostic = json.loads((tmp_path / "modeling_failure_problem1.json").read_text(encoding="utf-8"))
        assert "fact coverage mismatch" in diagnostic["error"]
    else:
        assert result.subproblems[0].execution_status == "solvable", result.model_dump()


def test_rejected_core_attempt_cannot_satisfy_final_coverage(tmp_path):
    from app.guidance.contracts import ProblemSpec
    from app.guidance.modeler import LLMModeler

    spec, architecture, core, constraints = _shared_fact_generation()
    payload = spec.model_dump(mode="json")
    payload["subproblems"][0]["required_fact_ids"].append("fact.rule")
    payload["locked_facts"].append({
        **payload["locked_facts"][0], "fact_id": "fact.rule", "semantic_key": "rule.limit",
    })
    spec = ProblemSpec.model_validate(payload)
    rejected_core = deepcopy(core)
    rejected_core["parameters"][0]["source_fact_ids"].append("fact.rule")
    rejected_core["parameters"][0]["index_sets"] = ["missing_period"]
    llm = _StageLLM(architecture, [rejected_core, core], constraints)

    result = asyncio.run(LLMModeler(
        llm, available_operator_ids=["data.scalar_constant", "optimization.milp"],
    ).model(problem_spec=spec, task={}, work_dir=tmp_path))

    assert result.subproblems[0].execution_status == "blocked"
    assert llm.attempts["indexed_core"] == 2
    constraint_payload = json.loads(llm.calls[3]["history"][1]["content"])
    assert constraint_payload["fact_coverage"]["uncovered_fact_ids"] == ["fact.rule"]
    diagnostic = json.loads((tmp_path / "modeling_failure_problem1.json").read_text(encoding="utf-8"))
    assert "fact coverage mismatch" in diagnostic["error"]
    assert "fact.rule" in diagnostic["error"]
