import asyncio
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from app.artifacts.evidence_profile import build_evidence_profile
from app.guidance import workflow
from app.services.source_ingest import ingest_question_source
from math_tool_fixtures import scripted_model_reply
from scripts.verify_ortools_simple_mip import audit_run, check_solution
from test_scipy_milp_guidance import _proposal


QUESTION = Path(__file__).parent / "fixtures/ortools_simple_mip/question.txt"


@pytest.fixture
def solution():
    # 列顺序是 y, x。第一行取反并倍乘，第二条题面约束以整数上界表示。
    return {"decision_values": {"a": 3, "b": 2}, "objective_value": 23,
            "compiled_model": {"variable_labels": ["b", "a"], "integrality": [1, 1],
                "lower_bounds": [0, 0], "upper_bounds": [None, 3], "objective_sense": "maximize",
                "objective": [10, 1], "matrix": [[-14, -2]], "constraint_lower": [-34],
                "constraint_upper": [None]}}


def test_equivalent_reordered_integer_formulation(solution):
    check = check_solution(solution, "a", "b")
    assert check["feasible_point_count"] == 12
    assert check["optimum"] == 23 and check["optimal_points"] == [[3, 2]]
    assert check["constraint_slacks"] == [0.5, 0.5]


@pytest.mark.parametrize(("field", "value"), [
    ("objective", [10, 0]), ("integrality", [1, 0]), ("upper_bounds", [None, None]),
    ("lower_bounds", [0, None]), ("matrix", [[-12, -2]]),
    ("matrix", [[-14, -2], [1, 0]]), ("constraint_lower", [-33]),
])
def test_incorrect_or_incomplete_compiled_model_with_same_reported_answer(solution, field, value):
    solution["compiled_model"][field] = value
    with pytest.raises(ValueError):
        check_solution(solution, "a", "b")


def test_extra_row_that_excludes_an_original_feasible_point(solution):
    model = solution["compiled_model"]
    model["matrix"].append([0, 1])
    model["constraint_lower"].append(2)
    model["constraint_upper"].append(None)
    with pytest.raises(ValueError, match="feasible point"):
        check_solution(solution, "a", "b")


@pytest.mark.parametrize("value", [True, float("nan"), 2.5, -1, "3"])
def test_invalid_decision_value(solution, value):
    solution["decision_values"]["a"] = value
    with pytest.raises(ValueError):
        check_solution(solution, "a", "b")


def test_mismatched_decision_keys_or_registered_objective(solution):
    for x_key, y_key in (("a", "a"), ("b", "a")):
        with pytest.raises(ValueError):
            check_solution(solution, x_key, y_key)
    solution["objective_value"] = 24
    with pytest.raises(ValueError):
        check_solution(solution, "a", "b")


class OfflineExample:
    def __init__(self, **settings):
        self.__dict__.update(settings)

    async def chat(self, *, agent_name, history, **kwargs):
        context = json.loads(history[1]["content"])
        if agent_name == "GuidanceDecomposerStructure":
            reply = {"subproblems": [{"id": "integer_optimum", "objective": "求最优解与目标值",
                                      "depends_on_subproblem_ids": []}]}
        elif agent_name == "GuidanceDecomposerFacts":
            row = next(b for b in context["input_catalog"]["blocks"] if "17.5" in b["text"])
            reply = {"locked_facts": [
                {"fact_id": key, "semantic_key": key, "value": rhs, "unit": "", "statement": row["text"],
                 "source_refs": [row["source_ref"]], "subproblem_ids": ["integer_optimum"]}
                for key, rhs in (("capacity", 17.5), ("x_limit", 3.5))], "uncertainties": []}
        elif agent_name == "GuidanceModeler":
            def term(variable, coefficient):
                return {"variable": {"variable_id": variable}, "coefficient": {"kind": "constant", "value": coefficient}}
            if any(m["role"] == "tool" for m in history):
                reply = {"variables": [{"id": k, "domain": "integer", "lower_bound": 0} for k in ("x", "y")],
                         "objective": {"sense": "maximize", "terms": [term("x", 1), term("y", 10)]},
                         "constraints": [{"id": k, "relation": "less_equal", "terms": terms,
                                          "rhs": {"kind": "parameter", "parameter_id": k}}
                                         for k, terms in (("capacity", [term("x", 1), term("y", 7)]),
                                                          ("x_limit", [term("x", 1)]))]}
            else:
                reply = _proposal(context["locked_facts"])
                reply["subproblems"][0]["objective"] = "最大化 x + 10y"
        elif agent_name == "GuidanceModelReviewer_integer_optimum":
            base = "/execution_components/0/model"
            reply = {"subproblem_id": "integer_optimum", "status": "approved", "mathematical_checks": [
                {"requirement_id": key, "model_paths": [f"{base}/{path}"], "rationale": "与题面核对相符。",
                 "status": "passed"} for key, path in (("capacity", "constraints/0"), ("x_limit", "constraints/1"),
                                                        ("$objective", "objective"), ("$formulation", "variables"))]}
        else:
            raise AssertionError(agent_name)
        return scripted_model_reply(reply, kwargs)


def test_independent_audit_of_published_offline_run_and_tampering(tmp_path, monkeypatch):
    source = tmp_path / "inputs/question/question.txt"
    source.parent.mkdir(parents=True)
    shutil.copyfile(QUESTION, source)
    task = {"task_id": tmp_path.name, "task_type": "guidance", "workflow_mode": "standard", "work_dir": str(tmp_path)}
    task.update(ingest_question_source(str(source), task))
    build_evidence_profile(tmp_path)
    monkeypatch.setattr(workflow, "LLM", OfflineExample)

    async def run():
        return [event async for event in workflow.build_default_guidance_pipeline().run(tmp_path.name, task)]

    events = asyncio.run(run())
    assert events[-1]["data"]["status"] == "completed", events[-1]["data"]

    def save(name, data):
        (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    save("pipeline_events.json", events)
    terminal = events[-1]["data"]
    save("run_summary.json", {"status": "completed", "pipeline_result": terminal})
    def audit():
        return audit_run(tmp_path, "optimum", "x", "y")
    receipt = audit()
    assert receipt["passed"], receipt
    assert receipt["decisions"] == {"x": 3, "y": 2} and receipt["objective"] == 23
    save("run_summary.json", {"status": "blocked", "pipeline_result": terminal})
    assert not audit()["passed"]
    save("run_summary.json", {"status": "completed", "pipeline_result": terminal})

    markdown = tmp_path / "guidance.md"
    original_bytes = markdown.read_bytes()
    original = original_bytes.decode("utf-8")
    report = json.loads((tmp_path / "guidance_audit_report.json").read_bytes())
    valid_row = next(line for line in original.splitlines() if "| x | 3.0 |" in line)
    for changed in (valid_row.replace("3.0", "13.0"), valid_row + "\n" + valid_row.replace("3.0", "13.0"),
                    valid_row.replace("#optimum", "#another_result"), ""):
        markdown.write_text(original.replace(valid_row, changed), encoding="utf-8")
        save("guidance_audit_report.json", {**report, "guidance_sha256": hashlib.sha256(markdown.read_bytes()).hexdigest()})
        failed = audit()
        assert not failed["passed"] and "answer table" in failed["failures"][0], failed
    markdown.write_bytes(original_bytes)
    save("guidance_audit_report.json", report)
    assert audit()["passed"]
