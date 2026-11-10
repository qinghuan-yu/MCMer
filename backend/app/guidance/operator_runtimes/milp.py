"""operator_runtimes: milp 领域实现与合同。"""

from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path

import numpy as np
from scipy.optimize import (
    Bounds,
    LinearConstraint,
    milp,
)

from app.guidance.indexed_linear import expand_indexed_linear_model
from app.guidance.model_ir import IndexedLinearModelSpec


def valid_milp_output(*, outputs: dict) -> list[str]:
    if set(outputs) != {"solution"}:
        return ["expected exactly one output named solution"]
    solution = outputs["solution"]
    if not isinstance(solution, dict):
        return ["output solution must be a mapping"]
    required = {
        "x",
        "decision_values",
        "objective_value",
        "solver",
        "feasibility_certificate",
        "bound_certificate",
        "compiled_model",
        "baseline",
        "provenance",
    }
    if not required <= set(solution):
        return ["MILP solution is missing certificate fields"]
    x = solution["x"]
    if (
        not isinstance(x, list)
        or not x
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in x)
    ):
        return ["MILP solution vector must contain finite values"]
    if not isinstance(solution["objective_value"], (int, float)) or not math.isfinite(
        solution["objective_value"]
    ):
        return ["MILP objective value must be finite"]
    return []

def solve_milp(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    if parameters["compiled_model"] is not None:
        model = parameters["compiled_model"]
    elif (
        parameters["indexed_model_template"] is not None
    ):
        model = _materialize_indexed_model(
            parameters["indexed_model_template"],
            inputs=inputs,
        )
    else:
        raise ValueError(
            "optimization.milp requires one compiler-owned compiled_model "
            "or indexed_model_template"
        )
    required = {
        "objective_sense",
        "variable_labels",
        "objective",
        "integrality",
        "lower_bounds",
        "upper_bounds",
        "constraint_labels",
        "matrix",
        "constraint_lower",
        "constraint_upper",
        "provenance",
    }
    if set(model) != required:
        raise ValueError("compiled MILP matrix has missing or undeclared fields")

    labels = model["variable_labels"]
    objective = _finite_vector(model["objective"], "objective")
    variable_count = len(objective)
    if not isinstance(labels, list) or len(labels) != variable_count or not all(
        isinstance(label, str) and label for label in labels
    ):
        raise ValueError("MILP variable labels do not match objective dimension")
    if len(set(labels)) != len(labels):
        raise ValueError("MILP variable labels must be unique")
    integrality = model["integrality"]
    if (
        not isinstance(integrality, list)
        or len(integrality) != variable_count
        or any(isinstance(value, bool) or value not in (0, 1) for value in integrality)
    ):
        raise ValueError("MILP integrality must contain only continuous=0 or integer=1 codes")
    lower = _optional_finite_vector(model["lower_bounds"], variable_count, "lower bounds")
    upper = _optional_finite_vector(model["upper_bounds"], variable_count, "upper bounds")
    if any(
        low is not None and high is not None and low > high
        for low, high in zip(lower, upper)
    ):
        raise ValueError("MILP lower bound exceeds upper bound")

    matrix = np.asarray(model["matrix"], dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != variable_count or not np.all(np.isfinite(matrix)):
        raise ValueError("MILP constraint matrix must be rectangular, dimensionally valid, and finite")
    constraint_count = matrix.shape[0]
    constraint_labels = model["constraint_labels"]
    if not isinstance(constraint_labels, list) or len(constraint_labels) != constraint_count:
        raise ValueError("MILP constraint labels do not match matrix rows")
    constraint_lower = _optional_finite_vector(
        model["constraint_lower"], constraint_count, "constraint lower bounds"
    )
    constraint_upper = _optional_finite_vector(
        model["constraint_upper"], constraint_count, "constraint upper bounds"
    )
    if any(
        low is not None and high is not None and low > high
        for low, high in zip(constraint_lower, constraint_upper)
    ):
        raise ValueError("MILP constraint lower bound exceeds upper bound")
    if model["objective_sense"] not in {"minimize", "maximize"}:
        raise ValueError("MILP objective_sense must be minimize or maximize")
    if not isinstance(model["provenance"], list):
        raise ValueError("MILP provenance must be a list")

    sign = 1.0 if model["objective_sense"] == "minimize" else -1.0
    scipy_lower = np.array([-np.inf if value is None else value for value in lower], dtype=float)
    scipy_upper = np.array([np.inf if value is None else value for value in upper], dtype=float)
    constraints = None
    if constraint_count:
        constraints = LinearConstraint(
            matrix,
            np.array([-np.inf if value is None else value for value in constraint_lower]),
            np.array([np.inf if value is None else value for value in constraint_upper]),
        )
    result = milp(
        c=sign * objective,
        integrality=np.asarray(integrality, dtype=int),
        bounds=Bounds(scipy_lower, scipy_upper),
        constraints=constraints,
    )
    if result.status == 2:
        raise ValueError("MILP model is infeasible")
    if result.status == 3:
        raise ValueError("MILP model is unbounded")
    if not result.success or result.x is None:
        raise RuntimeError(f"MILP solver did not certify a solution: {result.message}")

    baseline_result = milp(
        c=np.zeros(variable_count, dtype=float),
        integrality=np.asarray(integrality, dtype=int),
        bounds=Bounds(scipy_lower, scipy_upper),
        constraints=constraints,
    )
    if not baseline_result.success or baseline_result.x is None:
        raise RuntimeError("MILP feasibility baseline could not be constructed")

    x = np.asarray(result.x, dtype=float)
    activity = matrix @ x
    lower_violation = max(
        (low - actual for low, actual in zip(constraint_lower, activity) if low is not None),
        default=0.0,
    )
    upper_violation = max(
        (actual - high for high, actual in zip(constraint_upper, activity) if high is not None),
        default=0.0,
    )
    integer_positions = np.flatnonzero(np.asarray(integrality, dtype=int))
    integrality_violation = (
        float(np.max(np.abs(x[integer_positions] - np.rint(x[integer_positions]))))
        if integer_positions.size
        else 0.0
    )
    objective_value = float(objective @ x)
    baseline_x = np.asarray(baseline_result.x, dtype=float)
    dual_bound = getattr(result, "mip_dual_bound", None)
    if isinstance(dual_bound, (int, float)) and math.isfinite(dual_bound):
        dual_bound = float(sign * dual_bound)
    else:
        dual_bound = None
    return {"solution": {
        "x": x.tolist(),
        "decision_values": dict(zip(labels, x.tolist())),
        "objective_value": objective_value,
        "solver": {
            "name": "scipy.optimize.milp",
            "status": int(result.status),
            "message": str(result.message),
            "success": bool(result.success),
            "mip_gap": _optional_finite_number(getattr(result, "mip_gap", None)),
            "mip_node_count": _optional_finite_number(getattr(result, "mip_node_count", None)),
        },
        "feasibility_certificate": {
            "constraint_activity": activity.tolist(),
            "max_constraint_violation": float(max(0.0, lower_violation, upper_violation)),
            "max_integrality_violation": integrality_violation,
        },
        "bound_certificate": {
            "dual_bound": dual_bound,
            "primal_bound": objective_value,
        },
        "compiled_model": model,
        "baseline": {
            "x": baseline_x.tolist(),
            "objective_value": float(objective @ baseline_x),
            "feasible": True,
            "method": "zero_objective_feasible_point",
        },
        "provenance": model["provenance"],
    }}

def _materialize_indexed_model(template: dict, *, inputs: dict) -> dict:
    payload = deepcopy(template)
    index_sizes = {
        index_set["id"]: len(index_set["values"])
        for index_set in payload.get("index_sets", [])
    }
    for parameter in payload.get("parameters", []):
        source = parameter.get("runtime_source")
        if source is None:
            continue
        parameter_id = parameter["id"]
        if parameter_id not in inputs:
            raise ValueError(f"runtime parameter {parameter_id!r} has no input artifact")
        value = _runtime_field_value(
            inputs[parameter_id],
            path=source["field_path"],
            parameter_id=parameter_id,
        )
        expected_length = math.prod(
            index_sizes[index_set_id]
            for index_set_id in parameter["index_sets"]
        )
        if not isinstance(value, list) or len(value) != expected_length:
            actual_length = len(value) if isinstance(value, list) else "non-list"
            raise ValueError(
                f"runtime parameter {parameter_id!r} expected {expected_length} "
                f"values, got {actual_length}"
            )
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            for item in value
        ):
            raise ValueError(
                f"runtime parameter {parameter_id!r} must contain only finite numeric values"
            )
        parameter.pop("runtime_source")
        parameter["dense_values"] = [float(item) for item in value]
        parameter["dense_source_ref"] = (
            f"artifact:{source['artifact_ref']}#/"
            + "/".join(_json_pointer_token(field) for field in source["field_path"])
        )

    indexed_model = IndexedLinearModelSpec.model_validate(payload)
    compiled_model, issues = expand_indexed_linear_model(indexed_model)
    if compiled_model is None or issues:
        details = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        raise ValueError(f"runtime indexed model expansion failed: {details}")
    return compiled_model

def _runtime_field_value(value: object, *, path: list[str], parameter_id: str) -> object:
    current = value
    for field in path:
        if isinstance(current, dict) and field in current:
            current = current[field]
            continue
        if isinstance(current, list) and field.isdigit():
            index = int(field)
            if 0 <= index < len(current):
                current = current[index]
                continue
        pointer = "/" + "/".join(_json_pointer_token(item) for item in path)
        raise ValueError(
            f"runtime parameter {parameter_id!r} cannot resolve field path {pointer}"
        )
    return current

def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")

def _finite_vector(value: object, name: str) -> np.ndarray:
    try:
        vector = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"MILP {name} must be a finite numeric vector") from error
    if vector.ndim != 1 or vector.size == 0 or not np.all(np.isfinite(vector)):
        raise ValueError(f"MILP {name} must be a non-empty finite numeric vector")
    return vector

def _optional_finite_vector(value: object, length: int, name: str) -> list[float | None]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"MILP {name} have invalid dimensions")
    parsed: list[float | None] = []
    for item in value:
        if item is None:
            parsed.append(None)
        elif isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
            parsed.append(float(item))
        else:
            raise ValueError(f"MILP {name} must contain finite numbers or null")
    return parsed

def _optional_finite_number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None
