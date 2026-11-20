"""冻结题目的离线集成验收。仅模型传输使用脚本化响应，参考解只在求解结束后计算。"""
import asyncio
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
from math_tool_fixtures import scripted_model_reply

import pytest

from app.guidance import workflow
from scripts.run_closeout import DEFAULT_CASE, prepare_run
from scripts.verify_closeout_portfolio import Asset, evaluate_allocation, reference_optimum


ASSET_IDS = ("S1", "S2", "S3", "S4")
FIELDS = ("return", "risk", "rate", "threshold")


def _facts(blocks):
    facts = []

    def add(key, value, unit, block):
        facts.append({"fact_id": key, "semantic_key": key, "value": value, "unit": unit,
                      "statement": block["text"], "source_refs": [block["source_ref"]],
                      "subproblem_ids": ["portfolio"]})

    for block in blocks:
        row = block["text"].split()
        if row and row[0] in ASSET_IDS:
            for field, value, unit in zip(FIELDS, row[1:], ("%", "%", "%", "元"), strict=True):
                add(f"{row[0]}_{field}", float(value), unit, block)
        elif "银行同期存款利率为 5%" in block["text"]:
            add("interest", 5, "%", block)
        elif "期初总资金为 10000 元" in block["text"]:
            add("capital", 10000, "元", block)
        elif "要求不超过 200 元" in block["text"]:
            add("risk_cap", 200, "元", block)
    assert len(facts) == 19
    return {"locked_facts": facts, "uncertainties": []}


def _proposal(facts):
    return {"model_id": "offline_four_assets", "subproblems": [{
        "execution_status": "solvable", "subproblem_id": "portfolio", "objective": "最大化期末净收益",
        "execution_components": ["indexed_linear_model"], "assumptions": [],
        "data": [{"id": "question", "source_ref": facts[0]["source_refs"][0],
                  "fields": {"assets": "题面第一张表"}}],
        "candidate_models": [{"id": role, "role": role, "selection_metric": "net_profit",
                              "applicability_boundary": "离线脚本化测试。仅适用于给定四资产情景，风险采用题设损失金额，不构成投资建议。"}
                             for role in ("baseline", "recommended")],
        "validation": {"model_families": ["mixed_integer_linear_optimization"],
                       "identifiability_checks": ["bounded_domain"], "evidence_checks": ["input_rows"],
                       "robustness_checks": ["compare_all_bank"]},
        "final_answers": [{"id": "portfolio_result", "item": "配置与净收益", "value_ref": "solution",
                           "status": "candidate_design", "evidence_ids": ["solve_indexed_model"]}],
    }], "indexed_data": {
        "index_sets": [{"id": "assets", "values": list(ASSET_IDS)}],
        "parameters": [{"id": f["fact_id"], "source_kind": "static",
                        "binding": {"kind": "fact", "fact_id": f["fact_id"]}} for f in facts],
    }}


def _formulation():
    def coefficient(value, multiplier=1):
        if isinstance(value, str):
            return {"kind": "parameter", "parameter_id": value, "multiplier": multiplier}
        return {"kind": "constant", "value": value * multiplier}

    def term(variable, value, asset=None, multiplier=1):
        return {"coefficient": coefficient(value, multiplier), "variable": {
            "variable_id": variable, "selectors": [{"value": asset}] if asset else [],
        }}

    def constraint(name, terms, relation, rhs=0):
        return {"id": name, "terms": terms, "relation": relation, "rhs": coefficient(rhs)}

    cash = [term("bank", 1)]
    profit = [term("bank", "interest")]
    constraints = [
        constraint("bank_only_capital", [term("bank_only_principal", 1)], "equal", "capital"),
        constraint("bank_only_profit", [term("all_bank_net_profit", 1), term("bank_only_principal", "interest", multiplier=-1)], "equal"),
        constraint("one_maximum", [term("largest_loss_asset", 1, a) for a in ASSET_IDS], "equal", 1),
    ]
    for asset in ASSET_IDS:
        cash.extend([term("amount", 1, asset), term("fee", 1, asset)])
        profit.extend([term("amount", asset + "_return", asset), term("fee", -1, asset)])
        constraints.extend([
            constraint(asset + "_risk", [term("amount", asset + "_risk", asset)], "less_equal", "risk_cap"),
            constraint(asset + "_active", [term("amount", 1, asset), term("active", "capital", asset, -1)], "less_equal"),
            constraint(asset + "_floor", [term("base", 1, asset), term("active", asset + "_threshold", asset, -1)], "greater_equal"),
            constraint(asset + "_base", [term("base", 1, asset), term("amount", -1, asset)], "greater_equal"),
            constraint(asset + "_fee", [term("fee", 1, asset), term("base", asset + "_rate", asset, -1)], "equal"),
            constraint(asset + "_loss_lower", [term("max_risk", 1), term("amount", asset + "_risk", asset, -1)], "greater_equal"),
            constraint(asset + "_loss_upper", [term("max_risk", 1), term("amount", asset + "_risk", asset, -1),
                                               term("largest_loss_asset", "risk_cap", asset)], "less_equal", "risk_cap"),
        ])
    scalars = [{"id": name, "domain": "continuous"} for name in
               ("bank", "bank_only_principal", "all_bank_net_profit", "max_risk")]
    return {"variables": [*scalars, *[
        {"id": name, "domain": domain, "index_sets": ["assets"]}
        for name, domain in (("amount", "continuous"), ("fee", "continuous"),
                             ("base", "continuous"), ("active", "binary"), ("largest_loss_asset", "binary"))
    ]], "objective": {"sense": "maximize", "terms": profit},
        "constraints": [constraint("cash_budget", cash, "equal", "capital"), *constraints]}


def _review(model):
    prefix = "/execution_components/0/model"
    checks = [{"requirement_id": p["id"], "model_paths": [f"{prefix}/parameters/{i}"],
               "rationale": f"核对来源绑定与单位换算 {p['id']}", "status": "passed"}
              for i, p in enumerate(model["execution_components"][0]["model"]["parameters"])]
    checks.extend([
        {"requirement_id": "$objective", "model_paths": [prefix + "/objective"], "status": "passed",
         "rationale": "各资产利息及银行利息合计减去实际交易费。"},
        {"requirement_id": "$formulation", "model_paths": [prefix + "/constraints/0", prefix + "/variables/0"],
         "status": "passed", "rationale": "所有资产、银行余额与交易费共用资金，逐资产限制损失。"},
    ])
    return {"subproblem_id": "portfolio", "status": "approved", "mathematical_checks": checks}


class OfflinePortfolioLLM:
    def __init__(self, **settings):
        self.__dict__.update(settings)

    async def chat(self, *, agent_name, history, **kwargs):
        payload = json.loads(history[1]["content"])
        assert "validation_error" not in payload, payload.get("validation_error")
        if agent_name == "GuidanceDecomposerStructure":
            reply = {"subproblems": [{"id": "portfolio", "objective": "最大化净收益",
                                       "depends_on_subproblem_ids": []}]}
        elif agent_name == "GuidanceDecomposerFacts":
            reply = _facts(payload["input_catalog"]["blocks"])
        elif agent_name == "GuidanceModeler":
            observed = any(message["role"] == "tool" for message in history)
            reply = _formulation() if observed else _proposal(payload["locked_facts"])
        elif agent_name == "GuidanceModelReviewer_portfolio":
            reply = _review(payload["subproblem_model"])
        else:
            raise AssertionError(f"unexpected offline model request: {agent_name}")
        return scripted_model_reply(reply, kwargs)


def test_frozen_portfolio_through_default_pipeline_and_independent_verifier(tmp_path, monkeypatch):
    root = tmp_path / "offline-portfolio"
    task = prepare_run(DEFAULT_CASE, root)
    monkeypatch.setattr(workflow, "LLM", OfflinePortfolioLLM)

    async def run():
        return [event async for event in workflow.build_default_guidance_pipeline().run(root.name, task)]

    events = asyncio.run(run())
    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    assert events[-1]["data"]["status"] == "completed", read("guidance_audit_report.json")
    markdown = (root / "guidance.md").read_text(encoding="utf-8")
    assert markdown.startswith("# 建模指导方案")
    assert all(name in markdown for name in ("portfolio_result", "amount[S1]", "fee[S4]", "objective_value"))
    assert "2517.647" in markdown
    assert read("verification_report.json")["status"] == "verified"
    result, = read("result_registry.json")["verified_results"]
    solution = result["value"]
    values = solution["decision_values"]
    amounts = [values[f"amount[{asset}]"] for asset in ASSET_IDS]
    fees = [values[f"fee[{asset}]"] for asset in ASSET_IDS]
    bank = values["bank"]

    # 独立读取经校验的冻结原题，参考答案在模型调用全部结束后才计算。
    rows = Path(DEFAULT_CASE).with_name("portfolio_task.txt").read_text(encoding="utf-8").splitlines()
    assets = []
    for line in rows:
        row = line.split()
        if row and row[0] in ASSET_IDS:
            r, q, p, u = map(Fraction, row[1:])
            assets.append(Asset(row[0], r / 100, q / 100, p / 100, u))
    assert [a.name for a in assets] == list(ASSET_IDS)
    audit = evaluate_allocation(assets, amounts, bank, capital=10000, risk_limit=200,
                                recorded_objective=solution["objective_value"])
    exact = reference_optimum(assets, capital=10000, risk_limit=200)
    assert audit["passed"], audit
    assert values["max_risk"] == pytest.approx(audit["max_risk"], abs=1e-6)
    assert values["all_bank_net_profit"] == pytest.approx(500)
    assert "max_risk" in markdown and "all_bank_net_profit" in markdown
    assert fees == pytest.approx(audit["fees"], abs=1e-6, rel=0)
    assert exact["regimes_examined"] == 81
    assert audit["objective"] == pytest.approx(float(exact["objective"]), rel=0, abs=1e-6)
    command = [sys.executable, "-X", "utf8", str(DEFAULT_CASE.parents[1] / "scripts/verify_closeout_portfolio.py"),
               "--audit-run", str(root), "--result-id", result["id"]]
    check = subprocess.run(command, capture_output=True, encoding="utf-8")
    assert check.returncode == 0, check.stderr
    audited = json.loads(check.stdout)
    assert audited["passed"] and audited["regimes_examined"] == 81
    assert audited["net_profit"] == pytest.approx(float(exact["objective"]), abs=1e-6)
    wrong = subprocess.run([*command, "--bank-key", "bank_only_principal"], capture_output=True, encoding="utf-8")
    assert wrong.returncode != 0 and "cash_budget" in wrong.stdout
    receipt = {**audited, "mode": "scripted model transport / offline integration test", "external_api_calls": 0}
    (root / "offline_case_check.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")


@pytest.mark.parametrize(("filename", "change"), [
    ("result_registry.json", "changed"), ("result_registry.json", "missing"),
    ("result_registry.json", "invalid_json"), ("verification_report.json", "missing"),
    ("verification_report.json", "invalid_json"),
])
def test_result_changed_after_verification_blocks_both_download_and_task(tmp_path, monkeypatch, filename, change):
    from app.guidance.guidance_stage import GuidanceStage

    original = GuidanceStage.run
    async def change_result(self, **kwargs):
        path = kwargs["output_path"].parent / filename
        if change == "missing":
            path.unlink()
        elif change == "invalid_json":
            path.write_text("{", encoding="utf-8")
        else:
            results = json.loads(path.read_text(encoding="utf-8"))
            results["verified_results"][0]["value"]["objective_value"] = 987654321
            path.write_text(json.dumps(results), encoding="utf-8")
        return await original(self, **kwargs)

    root = tmp_path / "stale-output"
    task = prepare_run(DEFAULT_CASE, root)
    monkeypatch.setattr(workflow, "LLM", OfflinePortfolioLLM)
    monkeypatch.setattr(GuidanceStage, "run", change_result)
    async def run():
        return [event async for event in workflow.build_default_guidance_pipeline().run(root.name, task)]
    events = asyncio.run(run())
    assert events[-1]["data"]["status"] == events[-2]["data"]["status"] == "blocked"
    text = (root / "guidance.md").read_text(encoding="utf-8")
    assert text.startswith("# 结果复核阻断诊断") and "987654321" not in text
