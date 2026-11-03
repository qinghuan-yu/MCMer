"""model_validation: optimization 领域实现与合同。"""

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


def optimization_baseline(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "objective_value", "baseline_objective")
    if node is None:
        return not_assessed("recommended and baseline objectives were not both recorded")
    recommended = node["objective_value"]
    baseline = node["baseline_objective"]
    if not finite_number(recommended) or not finite_number(baseline):
        return failed("baseline comparison contains a non-finite objective")
    if recommended > baseline:
        return failed("recommended objective is worse than the minimization baseline")
    return passed_check("recommended objective is no worse than the minimization baseline")

def multistart(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "multistart")
    if node is None:
        return not_assessed("multi-start evidence was not recorded")
    evidence = node["multistart"]
    if not isinstance(evidence, Mapping):
        return failed("multi-start evidence must be a mapping")
    objectives = evidence.get("objective_values")
    tolerance = evidence.get("tolerance")
    if (
        not isinstance(objectives, list)
        or len(objectives) < 2
        or not all(finite_number(value) for value in objectives)
        or not finite_number(tolerance)
        or tolerance < 0
    ):
        return failed("multi-start evidence is incomplete or non-finite")
    if max(objectives) - min(objectives) > tolerance:
        return failed("multi-start objectives do not converge within tolerance")
    return passed_check("multi-start objectives converge within tolerance")

def _optimization_problem(
    node: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray, Mapping[str, Any], float] | None:
    try:
        value = np.asarray(node["x"], dtype=float).reshape(-1)
        problem = node["problem"]
        objective = problem["objective"]
        quadratic = np.asarray(objective["quadratic"], dtype=float)
        linear = np.asarray(objective["linear"], dtype=float).reshape(-1)
        constant = float(objective.get("constant", 0.0))
        tolerance = float(problem.get("constraint_tolerance", 1e-7))
    except (KeyError, TypeError, ValueError):
        return None
    dimension = len(value)
    if (
        dimension == 0
        or quadratic.shape != (dimension, dimension)
        or linear.shape != (dimension,)
        or not isinstance(problem, Mapping)
        or not np.all(np.isfinite(value))
        or not np.all(np.isfinite(quadratic))
        or not np.all(np.isfinite(linear))
        or not math.isfinite(constant)
        or not math.isfinite(tolerance)
        or tolerance < 0
    ):
        return None
    return quadratic, linear, constant, value, problem, tolerance

def optimization_feasibility(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "x", "objective_value", "problem", "feasibility_certificate")
    if node is None:
        return not_assessed("optimization feasibility evidence was not recorded")
    parsed = _optimization_problem(node)
    certificate = node["feasibility_certificate"]
    if parsed is None or not isinstance(certificate, Mapping):
        return failed("optimization problem or feasibility certificate is malformed")
    quadratic, linear, constant, value, problem, tolerance = parsed
    recomputed_objective = float(0.5 * value @ quadratic @ value + linear @ value + constant)
    recorded_objective = node["objective_value"]
    if not finite_number(recorded_objective) or not math.isclose(
        recorded_objective,
        recomputed_objective,
        rel_tol=1e-8,
        abs_tol=1e-8,
    ):
        return failed("recorded objective does not match independent recomputation")

    dimension = len(value)
    try:
        bounds = problem["bounds"]
        lower = np.asarray(bounds["lower"], dtype=float).reshape(-1)
        upper = np.asarray(bounds["upper"], dtype=float).reshape(-1)
        linear_constraints = problem.get("linear_constraints", {})
        raw_a_ub = np.asarray(linear_constraints.get("a_ub", []), dtype=float)
        raw_a_eq = np.asarray(linear_constraints.get("a_eq", []), dtype=float)
        a_ub = raw_a_ub.reshape(-1, dimension)
        a_eq = raw_a_eq.reshape(-1, dimension)
        b_ub = np.asarray(linear_constraints.get("b_ub", []), dtype=float).reshape(-1)
        b_eq = np.asarray(linear_constraints.get("b_eq", []), dtype=float).reshape(-1)
    except (KeyError, TypeError, ValueError):
        return failed("optimization bounds or linear constraints are malformed")
    if lower.shape != (dimension,) or upper.shape != (dimension,) or len(a_ub) != len(b_ub) or len(a_eq) != len(b_eq):
        return failed("optimization constraint dimensions do not match the solution")

    bound_violation = float(max(
        np.max(lower - value, initial=0.0),
        np.max(value - upper, initial=0.0),
        0.0,
    ))
    inequality_violation = float(np.max(a_ub @ value - b_ub, initial=0.0)) if len(a_ub) else 0.0
    equality_violation = float(np.max(np.abs(a_eq @ value - b_eq), initial=0.0)) if len(a_eq) else 0.0
    quadratic_violation = 0.0
    for constraint in problem.get("quadratic_constraints", []):
        try:
            matrix = np.asarray(constraint["quadratic"], dtype=float)
            vector = np.asarray(constraint["linear"], dtype=float).reshape(-1)
            offset = float(constraint.get("constant", 0.0))
            limit = float(constraint["upper_bound"])
        except (KeyError, TypeError, ValueError):
            return failed("quadratic constraint evidence is malformed")
        if matrix.shape != (dimension, dimension) or vector.shape != (dimension,):
            return failed("quadratic constraint dimensions do not match the solution")
        violation = float(0.5 * value @ matrix @ value + vector @ value + offset - limit)
        quadratic_violation = max(quadratic_violation, violation)
    recomputed = {
        "bound_violation": bound_violation,
        "linear_inequality_violation": inequality_violation,
        "linear_equality_violation": equality_violation,
        "quadratic_constraint_violation": quadratic_violation,
    }
    recomputed_max = max(recomputed.values())
    for name, expected in {**recomputed, "max_violation": recomputed_max}.items():
        recorded = certificate.get(name)
        if not finite_number(recorded) or not math.isclose(recorded, expected, rel_tol=1e-8, abs_tol=1e-8):
            return failed(f"feasibility certificate mismatch: {name}")
    satisfied = recomputed_max <= tolerance
    if certificate.get("constraints_satisfied") is not satisfied:
        return failed("feasibility status disagrees with independently recomputed violations")
    if not satisfied:
        return failed("recommended solution violates at least one constraint")
    return passed_check("objective and all constraint violations were independently recomputed")

def optimization_bounds(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "x", "objective_value", "problem", "bound_certificate")
    if node is None:
        return not_assessed("optimization bound evidence was not recorded")
    parsed = _optimization_problem(node)
    certificate = node["bound_certificate"]
    if parsed is None or not isinstance(certificate, Mapping):
        return failed("optimization problem or bound certificate is malformed")
    quadratic, linear, constant, _, _, tolerance = parsed
    eigenvalues = np.linalg.eigvalsh(quadratic)
    if float(np.min(eigenvalues)) <= 0.0:
        return failed("a global lower bound requires a positive-definite objective")
    unconstrained = np.linalg.solve(quadratic, -linear)
    lower_bound = float(0.5 * unconstrained @ quadratic @ unconstrained + linear @ unconstrained + constant)
    upper_bound = node["objective_value"]
    if not finite_number(upper_bound):
        return failed("optimization upper bound is non-finite")
    expected = {
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "absolute_gap": upper_bound - lower_bound,
    }
    for name, value in expected.items():
        recorded = certificate.get(name)
        if not finite_number(recorded) or not math.isclose(recorded, value, rel_tol=1e-8, abs_tol=1e-8):
            return failed(f"bound certificate mismatch: {name}")
    valid = lower_bound <= upper_bound + tolerance
    if certificate.get("valid") is not valid or not valid:
        return failed("optimization lower and upper bounds are inconsistent")
    return passed_check("global objective lower bound and feasible upper bound were independently recomputed")
