"""封档四资产情景的离线核验；不调用模型，不授予生产结果状态。"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import sys


SOURCE_SHA256 = "8e06e89aefbf3dd0218c5899fe7880e5bb9f84c082ea85b666babbf2332c95a3"
BANK_RATE = Fraction(1, 20)


@dataclass(frozen=True)
class Asset:
    name: str
    return_rate: Fraction
    risk_rate: Fraction
    fee_rate: Fraction
    fee_threshold: Fraction


def evaluate_allocation(assets, amounts, bank, *, capital, risk_limit, recorded_objective=None):
    if len(amounts) != len(assets):
        return {"passed": False, "failures": ["dimension"]}
    numbers = [*amounts, bank, capital, risk_limit]
    if recorded_objective is not None:
        numbers.append(recorded_objective)
    if not all(math.isfinite(float(value)) for value in numbers):
        return {"passed": False, "failures": ["finite"]}
    amounts, bank = [float(x) for x in amounts], float(bank)
    fees = [0.0 if x == 0 else float(a.fee_rate) * max(x, float(a.fee_threshold))
            for a, x in zip(assets, amounts)]
    objective = sum(float(a.return_rate) * x for a, x in zip(assets, amounts)) + float(BANK_RATE) * bank - sum(fees)
    max_risk = max([float(a.risk_rate) * x for a, x in zip(assets, amounts)], default=0.0)
    cash_error = sum(amounts) + sum(fees) + bank - float(capital)
    failures = []
    if min([*amounts, bank]) < 0:
        failures.append("nonnegative")
    if abs(cash_error) > 1e-6:
        failures.append("cash_budget")
    if max_risk > float(risk_limit) + 1e-6:
        failures.append("risk_limit")
    if recorded_objective is not None and abs(objective - float(recorded_objective)) > 1e-6:
        failures.append("objective")
    return {"passed": not failures, "failures": failures, "fees": fees,
            "objective": objective, "max_risk": max_risk, "cash_error": cash_error}


def reference_optimum(assets, *, capital, risk_limit):
    # 固定 0/最低费/比例费区间后，消去银行变量，变成一个可精确求解的连续背包。
    capital, risk_limit = Fraction(capital), Fraction(risk_limit)
    best = None
    examined = 0
    for modes in itertools.product(range(3), repeat=len(assets)):
        examined += 1
        lower, upper, rates, fixed = [], [], [], []
        for asset, mode in zip(assets, modes):
            cap = min(capital, risk_limit / asset.risk_rate) if asset.risk_rate else capital
            lower.append(asset.fee_threshold if mode == 2 else Fraction(0))
            upper.append(Fraction(0) if mode == 0 else min(cap, asset.fee_threshold) if mode == 1 else cap)
            rates.append(asset.fee_rate if mode == 2 else Fraction(0))
            fixed.append(asset.fee_rate * asset.fee_threshold if mode == 1 else Fraction(0))
        if any(lo > hi for lo, hi in zip(lower, upper)):
            continue
        remaining = capital - sum(fixed) - sum((1 + p) * lo for p, lo in zip(rates, lower))
        if remaining < 0:
            continue
        amounts = lower.copy()
        marginal = [(a.return_rate - BANK_RATE - (1 + BANK_RATE) * p) / (1 + p)
                    for a, p in zip(assets, rates)]
        for index in sorted(range(len(assets)), key=lambda i: (-marginal[i], i)):
            if marginal[index] <= 0:
                continue
            addition = min(upper[index] - lower[index], remaining / (1 + rates[index]))
            amounts[index] += addition
            remaining -= addition * (1 + rates[index])
        fees = [a.fee_rate * max(x, a.fee_threshold) if x else Fraction(0)
                for a, x in zip(assets, amounts)]
        bank = capital - sum(amounts) - sum(fees)
        objective = sum(a.return_rate * x for a, x in zip(assets, amounts)) + BANK_RATE * bank - sum(fees)
        if best is None or objective > best["objective"]:
            best = {"amounts": amounts, "bank": bank, "fees": fees,
                    "objective": objective, "regimes": modes}
    if best is None:
        raise ValueError("no feasible reference allocation")
    return {**best, "regimes_examined": examined}


def load_assets(source: Path):
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("original source SHA mismatch")
    text = raw.decode("utf-8").split("2）", 1)[0]
    rows = re.findall(r"^S([1-4]) ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)$", text, re.M)
    if len(rows) != 4 or "0r =5%" not in text:
        raise ValueError("original four-asset table or bank rate was not found")
    return [Asset("S" + name, Fraction(r) / 100, Fraction(q) / 100, Fraction(p) / 100, Fraction(u))
            for name, r, q, p, u in rows]


def probe_model(assets):
    """人工能力探针专用；此模型和参考答案绝不作为真实模型请求的输入。"""
    def selector():
        return [{"binder": "i"}]

    def term(variable, coefficient, indexed=True, summed=False):
        return {"for_each": {"i": "asset"} if summed else {}, "coefficient": coefficient,
                "variable": {"variable_id": variable, "selectors": selector() if indexed else []}}

    def number(value):
        return {"kind": "constant", "value": value}

    def parameter(name, multiplier=1):
        return {"kind": "parameter", "parameter_id": name,
                "selectors": selector(), "multiplier": multiplier}

    parameters = [{"id": name, "index_sets": ["asset"], "values": [
        {"coordinates": [a.name], "value": float(getattr(a, attribute)),
         "source_ref": f"data/original.txt#{a.name}/{name}"} for a in assets]}
        for name, attribute in [("r", "return_rate"), ("q", "risk_rate"),
                                ("p", "fee_rate"), ("u", "fee_threshold")]]
    core = {
        "index_sets": [{"id": "asset", "values": [a.name for a in assets]}],
        "parameters": parameters,
        "variables": [
            {"id": name, "index_sets": ["asset"], "domain": domain, "lower_bound": 0}
            for name, domain in [("buy", "continuous"), ("fee_base", "continuous"), ("active", "binary")]
        ] + [{"id": "bank", "index_sets": [], "domain": "continuous", "lower_bound": 0}],
        "objective": {"sense": "maximize", "terms": [
            term("buy", parameter("r"), summed=True),
            term("fee_base", parameter("p", -1), summed=True),
            term("bank", number(float(BANK_RATE)), indexed=False),
        ]},
        "constraints": [
            {"id": "cash", "relation": "equal", "rhs": number(10000), "terms": [
                term("buy", number(1), summed=True), term("fee_base", parameter("p"), summed=True),
                term("bank", number(1), indexed=False)]},
            {"id": "risk", "for_each": {"i": "asset"}, "relation": "less_equal", "rhs": number(200),
             "terms": [term("buy", parameter("q"))]},
            {"id": "activation", "for_each": {"i": "asset"}, "relation": "less_equal", "rhs": number(0),
             "terms": [term("buy", number(1)), term("active", number(-10000))]},
            {"id": "minimum_fee", "for_each": {"i": "asset"}, "relation": "greater_equal", "rhs": number(0),
             "terms": [term("fee_base", number(1)), term("active", parameter("u", -1))]},
            {"id": "proportional_fee", "for_each": {"i": "asset"}, "relation": "greater_equal", "rhs": number(0),
             "terms": [term("fee_base", number(1)), term("buy", number(-1))]},
        ],
    }
    return {"schema_version": "1.0", "model_id": "manual_capability_probe_only", "subproblems": [{
        "execution_status": "solvable", "subproblem_id": "portfolio", "objective": "Conditional net profit",
        "data": [{"id": "original", "source_ref": "data/original.txt", "fields": {"data": "first table"}}],
        "assumptions": [], "fact_bindings": [],
        "execution_components": [{"kind": "indexed_linear", "component_id": "allocation",
            "model": core, "solver_node_id": "solve", "outputs": ["solution"]}],
        "candidate_models": [{"id": role, "role": role, "operator_node_ids": ["solve"],
            "selection_metric": "net_profit", "applicability_boundary": "Only the declared capital and risk scenario"}
            for role in ["baseline", "recommended"]],
        "validation": {"model_families": ["mixed_integer_linear_optimization"],
            "identifiability_checks": ["finite_feasible_domain"], "evidence_checks": ["source_table"],
            "robustness_checks": ["exact_fee_regime_enumeration"]},
        "final_answers": [{"id": "portfolio_solution", "item": "Capability probe only", "value_ref": "solution",
            "status": "candidate_design", "evidence_ids": ["solve"]}],
    }]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    assets = load_assets(args.source)
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "data").mkdir()
    shutil.copy2(args.source, root / "data/original.txt")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.model_validators import build_default_model_validator_registry
    from app.guidance.operator_catalog import build_default_operator_registry

    model = ModelIR.model_validate(probe_model(assets))
    registry = build_default_operator_registry()
    compiled = ModelIRCompiler(registry=registry).compile_to_file(model, output_path=root / "execution_plan.json")
    if compiled.status != "compiled" or compiled.execution_plan is None:
        raise RuntimeError(str(compiled.diagnostics))
    node = compiled.execution_plan.graph.nodes[0]
    solution = registry.runtime(node.operator_id).invoke(inputs={}, parameters=node.parameters,
        random_seed=None, work_dir=root)["solution"]
    assessment = build_default_model_validator_registry().assess(
        family="mixed_integer_linear_optimization", candidate_roles={"baseline", "recommended"}, evidence=[solution])
    decisions = solution["decision_values"]
    amounts = [decisions[f"buy[{a.name}]"] for a in assets]
    bank = decisions["bank"]
    audit = evaluate_allocation(assets, amounts, bank, capital=10000, risk_limit=200,
                               recorded_objective=solution["objective_value"])
    reference = reference_optimum(assets, capital=10000, risk_limit=200)
    objective_error = abs(solution["objective_value"] - float(reference["objective"]))
    report = {"scope": "manual offline capability probe; no model generation or production acceptance",
        "source_sha256": SOURCE_SHA256, "operator_contract_sha256": registry.contract_sha256(),
        "api_calls": 0, "compilation": compiled.status, "numerical_validation": assessment.model_dump(mode="json"),
        "allocation_audit": audit, "reference": reference, "solver_objective": solution["objective_value"],
        "objective_error": objective_error,
        "passed": audit["passed"] and assessment.status == "passed" and objective_error <= 1e-6}
    for name, payload in [("probe_model_ir.json", model.model_dump(mode="json")), ("solution.json", solution), ("probe.json", report)]:
        (root / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, default=str))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
