"""model_validation: milp 领域实现与合同。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from app.guidance.model_validation.contracts import (
    ValidationCheck,
    failed,
    find_evidence,
    finite_number,
    not_assessed,
    passed_check,
)


def _milp_problem(nodes: list[Mapping[str, Any]]) -> tuple | None:
    node = find_evidence(
        nodes,
        "x",
        "objective_value",
        "compiled_model",
        "baseline",
        "solver",
        "bound_certificate",
    )
    if node is None or not isinstance(node["compiled_model"], Mapping):
        return None
    model = node["compiled_model"]
    try:
        x = np.asarray(node["x"], dtype=float)
        objective = np.asarray(model["objective"], dtype=float)
        integrality = np.asarray(model["integrality"], dtype=int)
        matrix = np.asarray(model["matrix"], dtype=float)
        lower = model["lower_bounds"]
        upper = model["upper_bounds"]
        constraint_lower = model["constraint_lower"]
        constraint_upper = model["constraint_upper"]
        sense = model["objective_sense"]
    except (KeyError, TypeError, ValueError):
        return None
    if (
        x.ndim != 1
        or objective.shape != x.shape
        or integrality.shape != x.shape
        or matrix.ndim != 2
        or matrix.shape[1] != x.size
        or len(lower) != x.size
        or len(upper) != x.size
        or len(constraint_lower) != matrix.shape[0]
        or len(constraint_upper) != matrix.shape[0]
        or not np.all(np.isfinite(x))
        or not np.all(np.isfinite(objective))
        or not np.all(np.isfinite(matrix))
        or sense not in {"minimize", "maximize"}
    ):
        return None
    return (
        node,
        model,
        x,
        objective,
        integrality,
        matrix,
        lower,
        upper,
        constraint_lower,
        constraint_upper,
        sense,
    )

def _milp_constraint_violation(parsed: tuple, x: np.ndarray) -> float | None:
    _, _, _, _, _, matrix, lower, upper, constraint_lower, constraint_upper, _ = parsed
    violation = 0.0
    for value, low, high in zip(x, lower, upper):
        if low is not None:
            if not finite_number(low):
                return None
            violation = max(violation, float(low) - float(value))
        if high is not None:
            if not finite_number(high):
                return None
            violation = max(violation, float(value) - float(high))
    activity = matrix @ x
    for value, low, high in zip(activity, constraint_lower, constraint_upper):
        if low is not None:
            if not finite_number(low):
                return None
            violation = max(violation, float(low) - float(value))
        if high is not None:
            if not finite_number(high):
                return None
            violation = max(violation, float(value) - float(high))
    return max(0.0, violation)

def _milp_integrality_violation(parsed: tuple, x: np.ndarray) -> float | None:
    integrality = parsed[4]
    if any(value not in (0, 1) for value in integrality):
        return None
    positions = np.flatnonzero(integrality)
    if not positions.size:
        return 0.0
    return float(np.max(np.abs(x[positions] - np.rint(x[positions]))))

def milp_objective(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _milp_problem(nodes)
    if parsed is None:
        return not_assessed("compiled MILP objective evidence was not recorded")
    node, _, x, objective, *_ = parsed
    recorded = node["objective_value"]
    recomputed = float(objective @ x)
    if not finite_number(recorded) or not math.isclose(
        float(recorded), recomputed, rel_tol=1e-9, abs_tol=1e-9
    ):
        return failed("MILP objective does not match independent matrix recomputation")
    return passed_check("MILP objective was independently recomputed from c and x")

def milp_feasibility(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _milp_problem(nodes)
    if parsed is None:
        return not_assessed("compiled MILP feasibility evidence was not recorded")
    node, _, x, *_ = parsed
    violation = _milp_constraint_violation(parsed, x)
    certificate = node.get("feasibility_certificate")
    recorded = certificate.get("max_constraint_violation") if isinstance(certificate, Mapping) else None
    if violation is None or not finite_number(recorded):
        return failed("MILP bounds or conservation rows are malformed")
    if not math.isclose(float(recorded), violation, rel_tol=1e-9, abs_tol=1e-9):
        return failed("MILP constraint certificate disagrees with independent recomputation")
    if violation > 1e-7:
        return failed("MILP solution violates variable bounds or linear conservation constraints")
    return passed_check("MILP bounds and all linear conservation rows were independently verified")

def milp_integrality(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _milp_problem(nodes)
    if parsed is None:
        return not_assessed("compiled MILP integrality evidence was not recorded")
    node, _, x, *_ = parsed
    violation = _milp_integrality_violation(parsed, x)
    certificate = node.get("feasibility_certificate")
    recorded = certificate.get("max_integrality_violation") if isinstance(certificate, Mapping) else None
    if violation is None or not finite_number(recorded):
        return failed("MILP integrality codes or certificate are malformed")
    if not math.isclose(float(recorded), violation, rel_tol=1e-9, abs_tol=1e-9):
        return failed("MILP integrality certificate disagrees with independent recomputation")
    if violation > 1e-7:
        return failed("MILP solution contains non-integral integer decisions")
    return passed_check("MILP integer decisions were independently checked")

def milp_bounds(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _milp_problem(nodes)
    if parsed is None:
        return not_assessed("compiled MILP bound evidence was not recorded")
    node, _, _, _, _, _, _, _, _, _, sense = parsed
    objective_value = node["objective_value"]
    certificate = node["bound_certificate"]
    solver = node["solver"]
    if not isinstance(certificate, Mapping) or not isinstance(solver, Mapping):
        return failed("MILP solver bound evidence is malformed")
    primal = certificate.get("primal_bound")
    dual = certificate.get("dual_bound")
    gap = solver.get("mip_gap")
    if not all(finite_number(value) for value in (objective_value, primal, dual, gap)):
        return failed("MILP primal bound, dual bound, or gap is non-finite")
    if not math.isclose(float(primal), float(objective_value), rel_tol=1e-9, abs_tol=1e-9):
        return failed("MILP primal bound disagrees with the objective")
    if sense == "minimize" and float(dual) > float(primal) + 1e-7:
        return failed("MILP minimization dual bound exceeds the primal bound")
    if sense == "maximize" and float(dual) < float(primal) - 1e-7:
        return failed("MILP maximization dual bound is below the primal bound")
    recomputed_gap = abs(float(primal) - float(dual)) / max(abs(float(primal)), 1e-10)
    if not math.isclose(float(gap), recomputed_gap, rel_tol=1e-6, abs_tol=1e-8):
        return failed("MILP solver gap disagrees with bound recomputation")
    return passed_check("MILP primal bound, dual bound, and relative gap were independently checked")

def _bounds_only_optimum(parsed: tuple) -> float | None:
    """仅凭变量界的线性目标最优值（逐坐标闭式求解）；无界返回 None。"""
    _, _, _, objective, integrality, _, lower, upper, _, _, sense = parsed
    direction = 1.0 if sense == "minimize" else -1.0
    total = 0.0
    for coefficient, low, high, integer in zip(objective, lower, upper, integrality):
        effective = direction * float(coefficient)
        if effective == 0.0:
            continue
        bound = low if effective > 0 else high
        if bound is None or not finite_number(bound):
            return None
        bound = float(bound)
        if integer:
            bound = math.ceil(bound - 1e-9) if effective > 0 else math.floor(bound + 1e-9)
        total += float(coefficient) * bound
    return total


def milp_constraint_relevance(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _milp_problem(nodes)
    if parsed is None:
        return not_assessed("compiled MILP relevance evidence was not recorded")
    node, _, _, objective, *_ = parsed
    if not np.any(objective):
        return passed_check(
            "constant objective: optimality is defined by feasibility, so constraint "
            "relevance to the optimum is vacuous"
        )
    relaxed = _bounds_only_optimum(parsed)
    if relaxed is None:
        return passed_check(
            "linear constraints are required to bound the objective beyond variable bounds"
        )
    optimum = float(node["objective_value"])
    if math.isclose(optimum, relaxed, rel_tol=1e-9, abs_tol=1e-9):
        return failed(
            f"MILP optimum {optimum:g} equals the optimum from variable bounds alone; "
            "no linear constraint shapes the solution, so linking, balance or demand "
            "equations are likely missing"
        )
    return passed_check(
        f"linear constraints move the optimum from {relaxed:g} (variable bounds alone) "
        f"to {optimum:g}"
    )


def milp_baseline(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _milp_problem(nodes)
    if parsed is None:
        return not_assessed("compiled MILP baseline evidence was not recorded")
    node, _, _, objective, _, _, _, _, _, _, sense = parsed
    baseline = node["baseline"]
    if not isinstance(baseline, Mapping):
        return failed("MILP baseline evidence is malformed")
    try:
        baseline_x = np.asarray(baseline["x"], dtype=float)
    except (KeyError, TypeError, ValueError):
        return failed("MILP baseline vector is malformed")
    if baseline_x.shape != objective.shape or not np.all(np.isfinite(baseline_x)):
        return failed("MILP baseline vector has invalid dimensions or values")
    feasibility = _milp_constraint_violation(parsed, baseline_x)
    integrality = _milp_integrality_violation(parsed, baseline_x)
    recorded = baseline.get("objective_value")
    recomputed = float(objective @ baseline_x)
    if feasibility is None or integrality is None or max(feasibility, integrality) > 1e-7:
        return failed("MILP baseline is not independently feasible")
    if baseline.get("feasible") is not True:
        return failed("MILP baseline is not declared feasible")
    if not finite_number(recorded) or not math.isclose(
        float(recorded), recomputed, rel_tol=1e-9, abs_tol=1e-9
    ):
        return failed("MILP baseline objective disagrees with independent recomputation")
    recommended = float(node["objective_value"])
    if sense == "minimize" and recommended > recomputed + 1e-7:
        return failed("recommended MILP objective is worse than the feasible baseline")
    if sense == "maximize" and recommended < recomputed - 1e-7:
        return failed("recommended MILP objective is worse than the feasible baseline")
    return passed_check("recommended MILP result was compared with an independently feasible baseline")
