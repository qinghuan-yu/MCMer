from copy import deepcopy

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.operator_catalog import build_default_operator_registry
from test_scipy_milp_guidance import ROW_IDS, _formulation, _proposal


def make_workspace():
    from app.guidance.math_workspace import MathWorkspace

    facts = [{"fact_id": name, "semantic_key": name, "value": value,
              "unit": "", "statement": f"题面声明 {name} = {value}",
              "source_refs": ["inputs/question.txt#paragraph-1"], "subproblem_ids": ["integer_optimum"]}
             for name, value in [*zip(ROW_IDS, (1, 12, 12)), ("variable_names", "x,y"),
                                 ("integrality", "integer"), ("goal", "y"), ("relation", "<=")]]
    spec = ProblemSpec(task_summary="Solve the bounded integer program.", locked_facts=facts, subproblems=[{
        "id": "integer_optimum", "depends_on_subproblem_ids": [], "required_uncertainty_ids": [],
        "required_fact_ids": [f["fact_id"] for f in facts],
    }])
    ws = MathWorkspace(problem_spec=spec, subproblem_id="integer_optimum", evidence_profile={"evidence": []},
                       registry=build_default_operator_registry())
    proposal = _proposal(facts[:3])
    data = proposal.pop("indexed_data")
    return ws, proposal, data


def test_incremental_edit_distinguishes_structural_facts_and_preserves_math():
    ws, architecture, data = make_workspace()
    variable_edit = {"variables": [{"id": name, "domain": "integer", "source_fact_ids": ["variable_names", "integrality"]}
                                   for name in ("x", "y")]}
    assert ws.execute("edit_model", {"architecture": architecture, **data, **variable_edit})["ok"]
    snapshot = ws.execute("inspect_model", {})
    invalid = deepcopy(data["parameters"][0])
    invalid["binding"]["fact_id"] = "variable_names"
    bad = ws.execute("edit_model", {"parameters": [invalid], "variables": [{"id": "z", "domain": "binary"}]})
    assert not bad["ok"]
    assert all(word in bad["error"] for word in ("difference", "variable_names", "variables"))
    assert ws.execute("inspect_model", {}) == snapshot
    assert ws.result is None

    form = _formulation()
    form["objective"]["source_fact_ids"] = ["goal"]
    for row in form["constraints"]:
        row["source_fact_ids"] = ["relation"]
    assert ws.execute("edit_model", {"objective": form["objective"], "constraints": form["constraints"]})["ok"]
    assert ws.execute("submit_model", {})["ok"]
    sub = ws.result.subproblems[0]
    paths = {b.fact_id: b.model_paths for b in sub.fact_bindings}
    assert paths["integrality"] == ["/execution_components/0/model/variables/0", "/execution_components/0/model/variables/1"]
    assert paths["goal"] == ["/execution_components/0/model/objective"]
    assert paths["relation"] == [f"/execution_components/0/model/constraints/{i}" for i in range(3)]
    assert sub.execution_components[0].model.parameters[0].values[0].value == 1
    assert not ws.execute("edit_model", {"variables": []})["ok"]


def test_repair_deletes_objects_and_submit_does_not_accept_uncovered_facts():
    ws, architecture, data = make_workspace()
    assert ws.execute("edit_model", {"architecture": architecture, **data, **_formulation()})["ok"]
    assert not ws.execute("submit_model", {})["ok"]
    assert ws.result is None
    assert ws.execute("edit_model", {"variables": [{"id": "extra", "domain": "integer"}]})["ok"]
    assert ws.execute("edit_model", {"remove": [{"section": "variables", "id": "extra"}]})["ok"]
    assert not ws.execute("edit_model", {"remove": [{"section": "variables", "id": "extra"}]})["ok"]
    assert not ws.execute("inspect_operators", {"operator_ids": ["not_a_registered_operator"]})["ok"]
    listed = ws.execute("inspect_operators", {"operator_ids": ["optimization.milp"]})
    assert listed["ok"] and "parameters_schema" not in listed["contracts"][0]


def test_materialized_parameter_preview_and_typed_edit_schema_are_available():
    ws, _, data = make_workspace()
    observed = ws.execute("edit_model", data)
    assert observed["ok"]
    assert observed["parameters"][0]["values"][0]["value"] == 1
    schema = ws.execute("inspect_model", {"schema": "variables"})
    assert schema["ok"] and "domain" in str(schema["schema"])
    assert not ws.execute("edit_model", {"not_a_field": 1})["ok"]


def test_scalar_core_and_formulation_context_do_not_require_an_unused_axis():
    from app.guidance.model_ir_drafts import FormulationContext, IndexedLinearCoreDraft

    core = IndexedLinearCoreDraft(index_sets=[], parameters=[]).promote_core()
    assert FormulationContext.from_core(core).index_sets == ()


def test_rejected_replacement_cannot_submit_a_stale_complete_candidate():
    ws, architecture, data = make_workspace()
    form = _formulation()
    form["variables"][0]["source_fact_ids"] = ["variable_names", "integrality"]
    form["objective"]["source_fact_ids"] = ["goal"]
    form["constraints"][0]["source_fact_ids"] = ["relation"]
    assert ws.execute("edit_model", {"architecture": architecture, **data, **form})["ok"]
    damaged = deepcopy(form["objective"])
    damaged["terms"][0]["variable"]["variable_id"] = "unregistered"
    assert not ws.execute("edit_model", {"objective": damaged})["ok"]
    assert not ws.execute("submit_model", {})["ok"]
    assert ws.result is None
    assert not ws.execute("edit_model", {"variables": []})["ok"]
    assert not ws.execute("submit_model", {})["ok"]
    assert ws.execute("edit_model", {"objective": form["objective"]})["ok"]
    assert ws.execute("submit_model", {})["ok"]


def test_partial_draft_rejects_stale_variable_and_parameter_references():
    ws, _, _ = make_workspace()
    assert ws.execute("edit_model", {"variables": [{"id": "i", "domain": "binary"}]})["ok"]
    objective = _formulation()["objective"]
    invalid = ws.execute("edit_model", {"objective": objective})
    assert not invalid["ok"] and "variable" in invalid["error"] and "y" in invalid["error"]
    objective["terms"][0] = {"variable": {"variable_id": "i"}, "coefficient": {"kind": "parameter", "parameter_id": "p"}}
    invalid = ws.execute("edit_model", {"objective": objective})
    assert not invalid["ok"] and "parameter" in invalid["error"] and "p" in invalid["error"]


def test_free_variable_remains_unbounded_across_independent_edits():
    ws, _, _ = make_workspace()
    assert ws.execute("edit_model", {"variables": [{"id": "x", "domain": "continuous", "lower_bound": None}]})["ok"]
    assert ws.execute("edit_model", {"variables": [{"id": "y", "domain": "continuous"}]})["ok"]
    variables = ws.execute("inspect_model", {})["draft"]["variables"]
    assert variables[0]["id"] == "x" and variables[0]["lower_bound"] is None
    assert variables[1]["lower_bound"] == 0


def test_materialized_values_can_be_read_past_edit_preview():
    ws, _, data = make_workspace()
    data["index_sets"] = [{"id": "period", "values": list(range(72))}]
    data["parameters"][0]["index_sets"] = ["period"]
    preview = ws.execute("edit_model", data)["parameters"][0]
    assert len(preview["values"]) == 8 and preview["total_values"] == 72
    middle = ws.execute("inspect_model", {"parameter_id": "difference", "offset": 32})["parameter"]
    assert [v["coordinates"] for v in middle["values"]] == [[n] for n in range(32, 64)]
    assert all(v["value"] == 1 and "locked_facts/difference" in v["source_ref"] for v in middle["values"])
    last = ws.execute("inspect_model", {"parameter_id": "difference", "offset": 64})["parameter"]
    assert len(last["values"]) == 8 and last["total_values"] == 72


@pytest.mark.parametrize("bad_bound", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_bound_cannot_enter_tool_history_or_model(bad_bound):
    ws, _, _ = make_workspace()
    result = ws.execute("edit_model", {"variables": [{"id": "x", "domain": "integer", "lower_bound": bad_bound}]})
    assert not result["ok"] and "finite" in result["error"]
    assert ws.execute("inspect_model", {})["draft"]["variables"] == []


def test_indexed_answer_evidence_returns_actionable_refs_and_preserves_model_until_repaired():
    ws, architecture, data = make_workspace()
    form = _formulation()
    form["variables"][0]["source_fact_ids"] = ["variable_names", "integrality"]
    form["objective"]["source_fact_ids"] = ["goal"]
    form["constraints"][0]["source_fact_ids"] = ["relation"]
    answer = architecture["subproblems"][0]["final_answers"][0]
    answer["evidence_ids"] = ["milp_solution", "goal"]
    assert ws.execute("edit_model", {"architecture": architecture, **data, **form})["ok"]
    before = ws.execute("inspect_model", {})
    denied = ws.execute("submit_model", {})
    assert not denied["ok"] and ws.result is None
    assert denied["invalid_evidence_ids"] == {"optimum": ["goal", "milp_solution"]}
    assert denied["available_evidence_ids"] == ["solve_indexed_model"]
    assert ws.execute("inspect_model", {}) == before
    answer["evidence_ids"] = denied["available_evidence_ids"]
    assert ws.execute("edit_model", {"architecture": architecture})["ok"]
    assert ws.execute("submit_model", {})["ok"]
    assert ws.result.subproblems[0].final_answers[0].evidence_ids == ["solve_indexed_model"]


def test_graph_nodes_and_unique_declared_source_ids_are_available_to_each_answer():
    from app.guidance.math_workspace import MathWorkspace
    from test_guidance_contract_agents import _operator_graph_draft, _subproblem

    architecture = _operator_graph_draft()
    sub = architecture["subproblems"][0]
    answer = sub["final_answers"][0]
    declared = sub["data"][0]["source_ref"]
    def entry(evidence_id, source=declared):
        return {"evidence_id": evidence_id, "source_ref": source, "source_file": source, "kind": "uploaded_file",
                "sha256": "0" * 64, "size_bytes": 1}
    source = entry("source.workbook")
    duplicate = entry("duplicate_id")
    collision = entry("rk18")
    profile = {"evidence": [source, entry("not_declared", "data/other"), collision, duplicate, duplicate]}
    spec = ProblemSpec(task_summary="Estimate the grouped model.", subproblems=[_subproblem(sub["subproblem_id"])])
    ws = MathWorkspace(problem_spec=spec, subproblem_id=sub["subproblem_id"], evidence_profile=profile,
                       registry=build_default_operator_registry())
    unknown = ["duplicate_id", "not_declared", "result_K_18", "rk18"]
    answer["evidence_ids"] = [*unknown, "figk"]
    assert ws.execute("edit_model", {"architecture": architecture})["ok"]
    denied = ws.execute("submit_model", {})
    assert not denied["ok"] and denied["invalid_evidence_ids"] == {answer["id"]: unknown}
    expected = {"source.workbook", *(op["node_id"] for op in sub["operators"])} - {"rk18"}
    assert set(denied["available_evidence_ids"]) == expected
    answer["evidence_ids"] = ["fitk", "figk", "source.workbook"]
    assert ws.execute("edit_model", {"architecture": architecture})["ok"]
    assert ws.execute("submit_model", {})["ok"]
