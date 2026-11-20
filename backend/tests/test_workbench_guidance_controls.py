"""同一整数规划的成对验收：缺少资源上限时阻断，提供上限后经修订求解。"""
import asyncio
import hashlib
from itertools import product
import json
import re
from unittest.mock import Mock

from app.artifacts.evidence_profile import build_evidence_profile
from app.guidance import workflow
from app.guidance.operator_runtimes import milp
from app.services.source_ingest import ingest_question_source
from math_tool_fixtures import scripted_model_reply, tool_reply
from test_scipy_milp_guidance import QUESTION, ROW_IDS, ScriptedIntegerExample, _formulation, _proposal


MISSING_RHS = "第三条约束：2x + 3y <= B。车间尚未提供可用工时 B 的数值。"
MISSING_INPUT = "车间的可用工时 B（第三条约束右端）"
RECOVERY = "请车间提供可用工时 B 的数值，再重新求解。"


class RepairingIntegerExample(ScriptedIntegerExample):
    async def chat(self, *, agent_name, history, **kwargs):
        context = json.loads(history[1]["content"])
        if agent_name == "GuidanceDecomposerFacts":
            rows = [block for block in context["input_catalog"]["blocks"] if "<=" in block["text"]]
            facts = []
            for name, row in zip(ROW_IDS, rows, strict=True):
                rhs = row["text"].split("<=")[1].strip(" 。")
                if rhs.isdigit():
                    facts.append({"fact_id": name, "semantic_key": name, "value": int(rhs),
                                  "unit": "", "statement": row["text"], "source_refs": [row["source_ref"]],
                                  "subproblem_ids": ["integer_optimum"]})
            return scripted_model_reply({"locked_facts": facts, "uncertainties": []}, kwargs)
        if agent_name != "GuidanceModeler":
            return await super().chat(agent_name=agent_name, history=history, **kwargs)

        observations = [json.loads(msg["content"]) for msg in history if msg["role"] == "tool"]
        initial = _proposal([f for f in context["locked_facts"] if f["fact_id"] != "resource_b"])
        core = initial.pop("indexed_data")
        formulation = _formulation()
        variables = formulation.pop("variables")
        if not observations:
            return tool_reply(("edit_model", {"architecture": initial, **core, "variables": variables}))
        if len(observations) == 1:
            assert observations[0]["changed"]["variables"] == ["x", "y"]
            unknown = {"id": "resource_b", "source_kind": "static",
                       "binding": {"kind": "fact", "fact_id": "unreported_capacity"}}
            reply = tool_reply(("edit_model", {"parameters": [unknown]}))
            reply.choices[0].message.content = "初步猜测：最大目标值为 731。"
            return reply
        assert len(observations) == 2
        assert not observations[-1]["ok"] and "unreported_capacity" in observations[-1]["error"]
        if any(f["fact_id"] == "resource_b" for f in context["locked_facts"]):
            correction = {"id": "resource_b", "source_kind": "static",
                          "binding": {"kind": "fact", "fact_id": "resource_b"}}
            return tool_reply(("edit_model", {"parameters": [correction], **formulation}), ("submit_model", {}))

        blocked = {"model_id": "missing_capacity", "subproblems": [{
            "execution_status": "blocked", "subproblem_id": "integer_optimum", "objective": "最大化 y",
            "blocking_reason": "缺少工时上限，不能确定最大目标值。", "missing_inputs": [MISSING_INPUT],
            "recovery_action": RECOVERY, "expected_outputs": ["一个最优整数解及目标值"],
        }]}
        remove = [{"section": section, "id": name}
                  for section in ("parameters", "index_sets", "variables")
                  for name in observations[0]["changed"][section]]
        return tool_reply(("edit_model", {"architecture": blocked, "remove": remove}), ("submit_model", {}))


class RepairingAnswerEvidence(ScriptedIntegerExample):
    async def chat(self, *, agent_name, history, **kwargs):
        if agent_name != "GuidanceModeler":
            return await super().chat(agent_name=agent_name, history=history, **kwargs)
        architecture = _proposal(json.loads(history[1]["content"])["locked_facts"])
        core = architecture.pop("indexed_data")
        answer, = architecture["subproblems"][0]["final_answers"]
        observations = [json.loads(msg["content"]) for msg in history if msg["role"] == "tool"]
        if not observations:
            answer["evidence_ids"] = ["milp_solution"]
            return tool_reply(("edit_model", {"architecture": architecture, **core, **_formulation()}),
                              ("submit_model", {}))
        edit, rejection = observations
        assert edit["ok"] and not rejection["ok"]
        assert rejection["invalid_evidence_ids"] == {answer["id"]: ["milp_solution"]}
        solver_node, = [node for node in rejection["available_evidence_ids"] if node.startswith("solve_")]
        answer["evidence_ids"] = [solver_node]
        return tool_reply(("edit_model", {"architecture": architecture}), ("submit_model", {}))


def run_example(root, monkeypatch, *, capacity_missing, provider=RepairingIntegerExample, rejected_tool="edit_model"):
    source = root / "inputs/question/capacity.txt"
    source.parent.mkdir(parents=True)
    text = QUESTION.read_text(encoding="utf-8")
    if capacity_missing:
        original = "第三条约束：2x + 3y <= 12。"
        assert text.count(original) == 1
        text = text.replace(original, MISSING_RHS)
    source.write_text(text, encoding="utf-8")
    task = {"task_id": root.name, "task_type": "guidance", "workflow_mode": "standard", "work_dir": str(root)}
    task.update(ingest_question_source(str(source), task))
    build_evidence_profile(root)
    monkeypatch.setattr(workflow, "LLM", provider)

    async def run():
        return [event async for event in workflow.build_default_guidance_pipeline().run(root.name, task)]

    events = asyncio.run(run())
    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    audit = read("guidance_audit_report.json")
    markdown = (root / "guidance.md").read_bytes()
    assert hashlib.sha256(markdown).hexdigest() == audit["guidance_sha256"]
    report = markdown.decode("utf-8")
    assert not re.search(r"(?<![0-9a-z])731(?![0-9a-z])", report)
    receipt = read("contract_diagnostics/GuidanceModeler.json")
    assert receipt["engine"] == "deepseek-harness" and receipt["status"] == "submitted", receipt
    assert [(a["name"], a["ok"]) for a in receipt["actions"]] == [
        ("edit_model", True), (rejected_tool, False), ("edit_model", True), ("submit_model", True),
    ]
    return events[-1]["data"]["status"], audit, report, read


def feasible_points(capacity):
    # 3x + 2y ≤ 12 和非负性给出 x ≤ 4、y ≤ 6；枚举覆盖整个定义域。
    return [(x, y) for x, y in product(range(5), range(7))
            if -x + y <= 1 and 3*x + 2*y <= 12 and 2*x + 3*y <= capacity]


def test_missing_resource_limit_is_actionably_blocked_before_solver(tmp_path, monkeypatch):
    solver = Mock(side_effect=AssertionError("a problem without a resource limit reached the solver"))
    monkeypatch.setattr(milp, "milp", solver)
    status, audit, report, read = run_example(tmp_path, monkeypatch, capacity_missing=True)
    solver.assert_not_called()
    assert [max(y for _, y in feasible_points(capacity)) for capacity in (0, 12)] == [0, 2]
    assert status == audit["publication_status"] == "blocked"
    assert read("verification_report.json")["execution_verified"]["status"] == "not_assessed"
    assert not (tmp_path / "execution_manifest.json").exists()
    assert read("result_registry.json")["verified_results"] == []
    assert MISSING_INPUT in report and RECOVERY in report
    assert read("model_ir.json")["subproblems"][0]["execution_status"] == "blocked"


def test_corrected_source_binding_uses_retained_variables_to_solve(tmp_path, monkeypatch):
    status, audit, report, read = run_example(tmp_path, monkeypatch, capacity_missing=False)
    assert status == "completed" and audit["publication_status"] == read("verification_report.json")["status"] == "verified"
    model = read("model_ir.json")["subproblems"][0]["execution_components"][0]["model"]
    assert [v["id"] for v in model["variables"]] == ["x", "y"]
    result, = read("result_registry.json")["verified_results"]
    values = result["value"]["decision_values"]
    observed = (values["x"], values["y"])
    feasible = feasible_points(12)
    assert observed in feasible
    assert result["value"]["objective_value"] == observed[1] == max(y for _, y in feasible)
    assert f"| objective_value | {observed[1]} |" in report


def test_submit_returns_usable_answer_evidence_before_review_and_publication(tmp_path, monkeypatch):
    status, audit, report, read = run_example(tmp_path, monkeypatch, capacity_missing=False,
                                              provider=RepairingAnswerEvidence, rejected_tool="submit_model")
    assert status == "completed" and audit["status"] == "PASS" and audit["publication_status"] == "verified"
    assert read("verification_report.json")["status"] == "verified"
    approval = read("approved_model_ir.json")
    assert approval["review_status"] == "approved"
    answer, = approval["approved_model_ir"]["subproblems"][0]["final_answers"]
    result, = read("result_registry.json")["verified_results"]
    assert answer["evidence_ids"] == result["evidence_ids"] == read("execution_manifest.json")["executed_node_ids"]
    assert answer["evidence_ids"] and "milp_solution" not in answer["evidence_ids"]
    best = max(y for _, y in feasible_points(12))
    assert result["value"]["objective_value"] == best
    assert f"| objective_value | {result['value']['objective_value']} |" in report
