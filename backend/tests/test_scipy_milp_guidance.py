"""SciPy milp 公开例题。脚本仅提供模型回复，最优解集合在计算结束后独立穷举。"""
import asyncio
import hashlib
from itertools import product
import json
from pathlib import Path
import shutil
from math_tool_fixtures import scripted_model_reply

from app.artifacts.evidence_profile import build_evidence_profile
from app.guidance import workflow
from app.services.source_ingest import ingest_question_source


QUESTION = Path(__file__).parent / "fixtures/scipy_milp/question.txt"
ROW_IDS = ("difference", "resource_a", "resource_b")


def _proposal(facts):
    return {
        "model_id": "integer_program",
        "subproblems": [{
            "execution_status": "solvable", "subproblem_id": "integer_optimum",
            "objective": "最大化 y", "execution_components": ["indexed_linear_model"],
            "assumptions": [], "data": [{"id": "constraints", "source_ref": facts[0]["source_refs"][0],
                                          "fields": {"row": "约束"}}],
            "candidate_models": [{"id": role, "role": role, "selection_metric": "objective_value",
                                  "applicability_boundary": "两个变量均为非负整数。"}
                                 for role in ("baseline", "recommended")],
            "validation": {"model_families": ["mixed_integer_linear_optimization"],
                           "identifiability_checks": ["bounded_domain"], "evidence_checks": ["source_rows"],
                           "robustness_checks": ["integer_feasibility"]},
            "final_answers": [{"id": "optimum", "item": "最优整数解及目标值", "value_ref": "solution",
                               "status": "candidate_design", "evidence_ids": ["solve_indexed_model"]}],
        }],
        "indexed_data": {"index_sets": [{"id": "axes", "values": ["x", "y"]}],
                         "parameters": [{"id": f["fact_id"], "source_kind": "static",
                                         "binding": {"kind": "fact", "fact_id": f["fact_id"]}} for f in facts]},
    }


def _formulation():
    def term(variable, coefficient):
        return {"coefficient": {"kind": "constant", "value": coefficient},
                "variable": {"variable_id": variable}}

    return {
        "variables": [{"id": name, "domain": "integer", "lower_bound": 0} for name in ("x", "y")],
        "objective": {"sense": "maximize", "terms": [term("y", 1)]},
        "constraints": [{"id": row, "relation": "less_equal", "terms": [term("x", a), term("y", b)],
                         "rhs": {"kind": "parameter", "parameter_id": row}}
                        for row, (a, b) in zip(ROW_IDS, [(-1, 1), (3, 2), (2, 3)], strict=True)],
    }


class ScriptedIntegerExample:
    def __init__(self, **settings):
        self.__dict__.update(settings)

    async def chat(self, *, agent_name, history, **kwargs):
        payload = json.loads(history[1]["content"])
        assert "validation_error" not in payload, payload.get("validation_error")
        if agent_name == "GuidanceDecomposerStructure":
            reply = {"subproblems": [{"id": "integer_optimum", "objective": "求解非负整数规划",
                                      "depends_on_subproblem_ids": []}]}
        elif agent_name == "GuidanceDecomposerFacts":
            rows = [block for block in payload["input_catalog"]["blocks"] if "<=" in block["text"]]
            facts = [{"fact_id": key, "semantic_key": key, "value": int(row["text"].split("<=")[1].strip(" 。")),
                      "unit": "", "statement": row["text"], "source_refs": [row["source_ref"]],
                      "subproblem_ids": ["integer_optimum"]} for key, row in zip(ROW_IDS, rows, strict=True)]
            reply = {"locked_facts": facts, "uncertainties": []}
        elif agent_name == "GuidanceModeler":
            observed = any(message["role"] == "tool" for message in history)
            reply = _formulation() if observed else _proposal(payload["locked_facts"])
        elif agent_name == "GuidanceModelReviewer_integer_optimum":
            base = "/execution_components/0/model"
            checks = [{"requirement_id": row, "model_paths": [f"{base}/parameters/{i}", f"{base}/constraints/{i}"],
                       "rationale": "不等式右端与对应来源一致。", "status": "passed"} for i, row in enumerate(ROW_IDS)]
            checks.extend([
                {"requirement_id": "$objective", "model_paths": [base + "/objective"],
                 "rationale": "仅最大化 y。", "status": "passed"},
                {"requirement_id": "$formulation", "model_paths": [base + "/variables", base + "/constraints"],
                 "rationale": "两个非负整数变量满足全部三个不等式。", "status": "passed"},
            ])
            reply = {"subproblem_id": "integer_optimum", "status": "approved", "mathematical_checks": checks}
        else:
            raise AssertionError(agent_name)
        return scripted_model_reply(reply, kwargs)


def test_integer_optimum_matches_exhaustive_enumeration(tmp_path, monkeypatch):
    source = tmp_path / "inputs/question" / QUESTION.name
    source.parent.mkdir(parents=True)
    shutil.copyfile(QUESTION, source)
    task = {"task_id": tmp_path.name, "task_type": "guidance", "workflow_mode": "standard", "work_dir": str(tmp_path)}
    task.update(ingest_question_source(str(source), task))
    build_evidence_profile(tmp_path)
    monkeypatch.setattr(workflow, "LLM", ScriptedIntegerExample)

    async def run():
        return [event async for event in workflow.build_default_guidance_pipeline().run(tmp_path.name, task)]

    events = asyncio.run(run())
    read = lambda name: json.loads((tmp_path / name).read_text(encoding="utf-8"))
    audit = read("guidance_audit_report.json")
    assert events[-1]["data"]["status"] == "completed", audit
    assert read("verification_report.json")["status"] == audit["publication_status"] == "verified"
    markdown = (tmp_path / "guidance.md").read_bytes()
    assert audit["status"] == "PASS" and hashlib.sha256(markdown).hexdigest() == audit["guidance_sha256"]
    result, = read("result_registry.json")["verified_results"]
    solution = result["value"]
    x, y = (solution["decision_values"][name] for name in ("x", "y"))
    # 非负性和后两条约束分别给出 x <= 4、y <= 4，下面覆盖整个整数域。
    feasible = [(i, j) for i, j in product(range(5), repeat=2) if -i + j <= 1 and 3*i + 2*j <= 12 and 2*i + 3*j <= 12]
    assert (x, y) in feasible and solution["objective_value"] == y == max(j for i, j in feasible)
    for key, value in (("x", x), ("y", y), ("objective_value", y)):
        assert f"| {key} | {value} |" in markdown.decode("utf-8")
