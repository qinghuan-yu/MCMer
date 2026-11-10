"""operator_runtimes: optimization 领域实现与合同。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize


def _quadratic_components(spec: dict, dimension: int) -> tuple[np.ndarray, np.ndarray, float]:
    quadratic = np.asarray(spec["quadratic"], dtype=float)
    linear = np.asarray(spec["linear"], dtype=float).reshape(-1)
    constant = float(spec["constant"])
    if quadratic.shape != (dimension, dimension) or linear.shape != (dimension,):
        raise ValueError("quadratic expression dimensions do not match the decision vector")
    if not np.allclose(quadratic, quadratic.T, atol=1e-12):
        raise ValueError("quadratic matrix must be symmetric")
    return quadratic, linear, constant

def _quadratic_value(
    components: tuple[np.ndarray, np.ndarray, float],
    value: np.ndarray,
) -> float:
    quadratic, linear, constant = components
    return float(0.5 * value @ quadratic @ value + linear @ value + constant)

def _constraint_violation(
    value: np.ndarray,
    *,
    lower: np.ndarray,
    upper: np.ndarray,
    a_ub: np.ndarray,
    b_ub: np.ndarray,
    a_eq: np.ndarray,
    b_eq: np.ndarray,
    quadratic_constraints: list[tuple[tuple[np.ndarray, np.ndarray, float], float]],
) -> dict:
    bound_violation = float(max(
        np.max(lower - value, initial=0.0),
        np.max(value - upper, initial=0.0),
        0.0,
    ))
    linear_inequality = float(np.max(a_ub @ value - b_ub, initial=0.0)) if len(a_ub) else 0.0
    linear_equality = float(np.max(np.abs(a_eq @ value - b_eq), initial=0.0)) if len(a_eq) else 0.0
    quadratic_values = [
        _quadratic_value(components, value) - limit
        for components, limit in quadratic_constraints
    ]
    quadratic_violation = max([0.0, *quadratic_values])
    return {
        "bound_violation": bound_violation,
        "linear_inequality_violation": linear_inequality,
        "linear_equality_violation": linear_equality,
        "quadratic_constraint_violation": float(quadratic_violation),
        "max_violation": float(max(
            bound_violation,
            linear_inequality,
            linear_equality,
            quadratic_violation,
        )),
    }

def constrained_quadratic_multistart(
    *,
    inputs: dict,
    parameters: dict,
    random_seed,
    work_dir: Path,
) -> dict:
    del random_seed, work_dir
    data = np.asarray(inputs["data"], dtype=float).reshape(-1)
    if not len(data) or not np.all(np.isfinite(data)):
        raise ValueError("optimization data bindings must be a finite non-empty vector")

    def bind(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$data."):
            index_text = value.removeprefix("$data.")
            if not index_text.isdecimal() or int(index_text) >= len(data):
                raise ValueError(f"invalid optimization data binding: {value}")
            return float(data[int(index_text)])
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    parameters = bind(parameters)
    starts = np.asarray(parameters["starts"], dtype=float)
    if starts.ndim != 2 or len(starts) < 2:
        raise ValueError("starts must contain at least two decision vectors")
    dimension = starts.shape[1]
    objective_components = _quadratic_components(parameters["objective"], dimension)
    objective_quadratic, objective_linear, _ = objective_components
    eigenvalues = np.linalg.eigvalsh(objective_quadratic)
    if float(np.min(eigenvalues)) <= 0.0:
        raise ValueError("objective quadratic matrix must be positive definite")

    bounds_spec = parameters["bounds"]
    lower = np.asarray(bounds_spec["lower"], dtype=float).reshape(-1)
    upper = np.asarray(bounds_spec["upper"], dtype=float).reshape(-1)
    if lower.shape != (dimension,) or upper.shape != (dimension,) or np.any(lower > upper):
        raise ValueError("bounds do not match the decision vector")

    linear = parameters["linear_constraints"]
    a_ub = np.asarray(linear["a_ub"], dtype=float).reshape(-1, dimension)
    b_ub = np.asarray(linear["b_ub"], dtype=float).reshape(-1)
    a_eq = np.asarray(linear["a_eq"], dtype=float).reshape(-1, dimension)
    b_eq = np.asarray(linear["b_eq"], dtype=float).reshape(-1)
    if len(a_ub) != len(b_ub) or len(a_eq) != len(b_eq):
        raise ValueError("linear constraint matrices and bounds have inconsistent rows")

    quadratic_constraints = [
        (
            _quadratic_components(spec, dimension),
            float(spec["upper_bound"]),
        )
        for spec in parameters["quadratic_constraints"]
    ]
    tolerance = float(parameters["constraint_tolerance"])
    multistart_tolerance = float(parameters["multistart_tolerance"])

    def violation(value: np.ndarray) -> dict:
        return _constraint_violation(
            value,
            lower=lower,
            upper=upper,
            a_ub=a_ub,
            b_ub=b_ub,
            a_eq=a_eq,
            b_eq=b_eq,
            quadratic_constraints=quadratic_constraints,
        )

    baseline_violation = violation(starts[0])
    if baseline_violation["max_violation"] > tolerance:
        raise ValueError("the first start must be a feasible baseline candidate")
    baseline_objective = _quadratic_value(objective_components, starts[0])

    scipy_constraints: list[dict] = []
    for row, limit in zip(a_ub, b_ub):
        scipy_constraints.append({
            "type": "ineq",
            "fun": lambda value, row=row, limit=limit: float(limit - row @ value),
        })
    for row, target in zip(a_eq, b_eq):
        scipy_constraints.append({
            "type": "eq",
            "fun": lambda value, row=row, target=target: float(row @ value - target),
        })
    for components, limit in quadratic_constraints:
        scipy_constraints.append({
            "type": "ineq",
            "fun": lambda value, components=components, limit=limit: float(
                limit - _quadratic_value(components, value)
            ),
        })

    runs: list[dict] = []
    for start in starts:
        result = minimize(
            fun=lambda value: _quadratic_value(objective_components, value),
            jac=lambda value: objective_quadratic @ value + objective_linear,
            x0=start,
            method="SLSQP",
            bounds=list(zip(lower, upper)),
            constraints=scipy_constraints,
            options={"ftol": 1e-12, "maxiter": 1000},
        )
        run_violation = violation(np.asarray(result.x, dtype=float))
        if result.success and run_violation["max_violation"] <= tolerance:
            runs.append({
                "x": np.asarray(result.x, dtype=float),
                "objective": float(result.fun),
                "iterations": int(result.nit),
            })
    if len(runs) != len(starts):
        raise RuntimeError("not every optimization start converged to a feasible solution")
    best = min(runs, key=lambda item: item["objective"])
    solution = best["x"]
    objective_value = best["objective"]
    feasibility = violation(solution)
    feasibility["constraints_satisfied"] = feasibility["max_violation"] <= tolerance

    unconstrained = np.linalg.solve(objective_quadratic, -objective_linear)
    lower_bound = _quadratic_value(objective_components, unconstrained)
    objective_values = [item["objective"] for item in runs]
    solution_scale = max(float(np.linalg.norm(solution)), np.finfo(float).eps)
    solution_spread = max(
        float(np.linalg.norm(item["x"] - solution) / solution_scale)
        for item in runs
    )
    problem = {
        "objective": parameters["objective"],
        "linear_constraints": linear,
        "quadratic_constraints": parameters["quadratic_constraints"],
        "bounds": bounds_spec,
        "constraint_tolerance": tolerance,
    }
    return {"solution": {
        "x": solution.tolist(),
        "objective_value": objective_value,
        "baseline_objective": baseline_objective,
        "problem": problem,
        "feasibility_certificate": feasibility,
        "constraints_satisfied": feasibility["constraints_satisfied"],
        "bound_certificate": {
            "lower_bound": lower_bound,
            "upper_bound": objective_value,
            "absolute_gap": objective_value - lower_bound,
            "valid": lower_bound <= objective_value + tolerance,
        },
        "multistart": {
            "objective_values": objective_values,
            "solutions": [item["x"].tolist() for item in runs],
            "tolerance": multistart_tolerance,
        },
        "sensitivity": {
            "max_relative_change": solution_spread,
            "threshold": float(parameters["sensitivity_threshold"]),
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(solution))),
            "constraints_satisfied": feasibility["constraints_satisfied"],
        },
    }}
