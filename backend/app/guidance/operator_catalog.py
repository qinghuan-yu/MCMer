"""Problem-independent numerical operators available to compiled Model IR."""

from __future__ import annotations

import heapq
import math
from copy import deepcopy
from collections import deque
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from statistics import NormalDist
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import Bounds, LinearConstraint, milp, minimize

from app.guidance.indexed_linear import expand_indexed_linear_model
from app.guidance.model_ir import IndexedLinearModelSpec
from app.guidance.operators import OperatorDefinition, OperatorPort, OperatorRegistry


DEFAULT_OPERATOR_IDS = {
    "artifact.extract_mapping",
    "artifact.extract_matrix",
    "artifact.extract_scalar",
    "artifact.extract_vector",
    "data.read_excel_table",
    "data.scalar_constant",
    "decision.mcda_weighted_ranking",
    "design.periodic_key_moments",
    "diagnostics.design_rank",
    "diagnostics.leave_one_out",
    "dynamics.affine_ode_rk4",
    "forecast.robust_regularized_time_split",
    "forecast.future_basis_rolling_origin",
    "graph.shortest_path_max_flow",
    "model_selection.continuous_hinge_bic",
    "optimization.constrained_quadratic_multistart",
    "optimization.milp",
    "plot.xy_evidence",
    "regression.basis_least_squares",
    "regression.zero_intercept_grouped",
}


def _port(kind: str) -> OperatorPort:
    return OperatorPort(value_kind=kind, unit="unspecified")


def _valid_mapping_output(name: str) -> Callable:
    def validate(*, outputs: dict) -> list[str]:
        if set(outputs) != {name}:
            return [f"expected exactly one output named {name}"]
        if not isinstance(outputs[name], dict):
            return [f"output {name} must be a mapping"]
        return []

    return validate


def build_default_operator_registry() -> OperatorRegistry:
    registry = OperatorRegistry()
    registrations = [
        (
            "artifact.extract_mapping",
            {"artifact": _port("artifact")},
            {"value": _port("artifact")},
            _extract_mapping,
            _valid_mapping_output("value"),
        ),
        (
            "artifact.extract_matrix",
            {"artifact": _port("artifact")},
            {"value": _port("matrix")},
            _extract_matrix,
            _valid_matrix_output,
        ),
        (
            "artifact.extract_scalar",
            {"artifact": _port("artifact")},
            {"value": _port("scalar")},
            _extract_scalar,
            _valid_scalar_output,
        ),
        (
            "artifact.extract_vector",
            {"artifact": _port("artifact")},
            {"value": _port("vector")},
            _extract_vector,
            _valid_vector_output,
        ),
        (
            "data.scalar_constant",
            {},
            {"value": _port("scalar")},
            _scalar_constant,
            _valid_scalar_output,
        ),
        (
            "design.periodic_key_moments",
            {},
            {"design": _port("artifact")},
            _periodic_key_moments,
            _valid_mapping_output("design"),
        ),
        (
            "data.read_excel_table",
            {"source": _port("artifact")},
            {"table": _port("table")},
            _read_excel_table,
            _valid_mapping_output("table"),
        ),
        (
            "regression.zero_intercept_grouped",
            {"table": _port("table")},
            {"fit": _port("artifact")},
            _zero_intercept_grouped,
            _valid_mapping_output("fit"),
        ),
        (
            "regression.basis_least_squares",
            {"x": _port("vector"), "y": _port("vector")},
            {"fit": _port("artifact")},
            _basis_least_squares,
            _valid_mapping_output("fit"),
        ),
        (
            "forecast.robust_regularized_time_split",
            {
                "time": _port("vector"),
                "features": _port("matrix"),
                "target": _port("vector"),
            },
            {"evaluation": _port("artifact")},
            _robust_regularized_time_split,
            _valid_mapping_output("evaluation"),
        ),
        (
            "forecast.future_basis_rolling_origin",
            {"time": _port("vector"), "target": _port("vector")},
            {"forecast": _port("artifact")},
            _future_basis_rolling_origin,
            _valid_mapping_output("forecast"),
        ),
        (
            "model_selection.continuous_hinge_bic",
            {"x": _port("vector"), "y": _port("matrix")},
            {"comparison": _port("artifact")},
            _continuous_hinge_bic,
            _valid_mapping_output("comparison"),
        ),
        (
            "optimization.constrained_quadratic_multistart",
            {"data": _port("vector")},
            {"solution": _port("artifact")},
            _constrained_quadratic_multistart,
            _valid_mapping_output("solution"),
        ),
        (
            "optimization.milp",
            {},
            {"solution": _port("artifact")},
            _milp,
            _valid_milp_output,
        ),
        (
            "decision.mcda_weighted_ranking",
            {"matrix": _port("matrix")},
            {"decision": _port("artifact")},
            _mcda_weighted_ranking,
            _valid_mapping_output("decision"),
        ),
        (
            "dynamics.affine_ode_rk4",
            {"time": _port("vector"), "states": _port("matrix")},
            {"simulation": _port("artifact")},
            _affine_ode_rk4,
            _valid_mapping_output("simulation"),
        ),
        (
            "graph.shortest_path_max_flow",
            {"metrics": _port("matrix")},
            {"analysis": _port("artifact")},
            _shortest_path_max_flow,
            _valid_mapping_output("analysis"),
        ),
        (
            "diagnostics.design_rank",
            {"matrix": _port("matrix")},
            {"diagnostic": _port("artifact")},
            _design_rank,
            _valid_mapping_output("diagnostic"),
        ),
        (
            "diagnostics.leave_one_out",
            {"x": _port("vector"), "y": _port("vector")},
            {"diagnostic": _port("artifact")},
            _leave_one_out,
            _valid_mapping_output("diagnostic"),
        ),
        (
            "plot.xy_evidence",
            {
                "x": _port("vector"),
                "observed": _port("vector"),
                "predicted": _port("vector"),
            },
            {"figure": _port("artifact")},
            _xy_evidence,
            _valid_mapping_output("figure"),
        ),
    ]
    for operator_id, inputs, outputs, executor, validator in registrations:
        registry.register(
            OperatorDefinition(
                operator_id=operator_id,
                version="1.0.0",
                inputs=inputs,
                outputs=outputs,
                preconditions=[],
                deterministic=True,
                validator_id=f"{operator_id}.validator.v1",
            ),
            executor=executor,
            validator=validator,
        )
    return registry


def _valid_scalar_output(*, outputs: dict) -> list[str]:
    if set(outputs) != {"value"}:
        return ["expected exactly one output named value"]
    if not isinstance(outputs["value"], (int, float, str, bool)):
        return ["output value must be scalar"]
    return []


def _valid_vector_output(*, outputs: dict) -> list[str]:
    if set(outputs) != {"value"}:
        return ["expected exactly one output named value"]
    if not isinstance(outputs["value"], list):
        return ["output value must be a list"]
    return []


def _valid_matrix_output(*, outputs: dict) -> list[str]:
    if set(outputs) != {"value"}:
        return ["expected exactly one output named value"]
    value = outputs["value"]
    if not isinstance(value, list) or not value or not all(isinstance(row, list) for row in value):
        return ["output value must be a non-empty matrix"]
    column_count = len(value[0])
    if column_count == 0 or any(len(row) != column_count for row in value):
        return ["output matrix must be non-empty and rectangular"]
    try:
        matrix = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return ["output matrix must be numeric"]
    if not np.all(np.isfinite(matrix)):
        return ["output matrix must contain finite values"]
    return []


def _valid_milp_output(*, outputs: dict) -> list[str]:
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


def _milp(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    if set(parameters) == {"compiled_model"} and isinstance(parameters["compiled_model"], dict):
        model = parameters["compiled_model"]
    elif (
        set(parameters) == {"indexed_model_template"}
        and isinstance(parameters["indexed_model_template"], dict)
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


def _path_value(artifact: object, path: str) -> object:
    value = artifact
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        if isinstance(value, list) and part.isdecimal():
            index = int(part)
            if index < len(value):
                value = value[index]
                continue
        if not part:
            raise ValueError(f"artifact path does not exist: {path}")
        raise ValueError(f"artifact path does not exist: {path}")
    return value


def _extract_mapping(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    fields = parameters.get("fields")
    if fields is not None:
        if not isinstance(fields, dict) or not fields:
            raise ValueError("mapping projection fields must be a non-empty mapping")
        value = {
            str(name): _path_value(inputs["artifact"], str(path))
            for name, path in fields.items()
        }
    else:
        value = _path_value(inputs["artifact"], parameters["path"])
    if not isinstance(value, dict):
        raise ValueError("selected artifact value is not a mapping")
    return {"value": value}


def _extract_matrix(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    value = _path_value(inputs["artifact"], parameters["path"])
    if not isinstance(value, list):
        raise ValueError("selected artifact value is not a matrix")
    return {"value": value}


def _extract_scalar(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    value = _path_value(inputs["artifact"], parameters["path"])
    indices = parameters.get("indices")
    if indices is not None:
        if not isinstance(value, list) or not isinstance(indices, list) or not indices:
            raise ValueError("indexed scalar projection requires a non-empty list")
        selected = []
        for index in indices:
            if not isinstance(index, int) or index < 0 or index >= len(value):
                raise ValueError("scalar projection index is out of bounds")
            selected.append(value[index])
        value = selected
    reducer = parameters.get("reducer")
    if reducer == "sum":
        value = sum(value)
    elif reducer is not None:
        raise ValueError(f"unsupported scalar reducer: {reducer}")
    if not isinstance(value, (int, float, str, bool)):
        raise ValueError("selected artifact value is not scalar")
    return {"value": value}


def _extract_vector(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    value = _path_value(inputs["artifact"], parameters["path"])
    if not isinstance(value, list):
        raise ValueError("selected artifact value is not a vector")
    indices = parameters.get("indices")
    if indices is not None:
        if not isinstance(indices, list) or not indices:
            raise ValueError("vector projection indices must be a non-empty list")
        selected: list[Any] = []
        for index in indices:
            if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(value):
                raise ValueError("vector projection index is out of bounds")
            selected.append(value[index])
        value = selected
    return {"value": value}


def _scalar_constant(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del inputs, random_seed, work_dir
    value = parameters["value"]
    if not isinstance(value, (int, float, str, bool)):
        raise ValueError("constant value must be scalar")
    return {"value": value}


def _periodic_key_moments(
    *, inputs: dict, parameters: dict, random_seed, work_dir: Path
) -> dict:
    del inputs, random_seed, work_dir
    sample_count = int(parameters["sample_count"])
    period = int(parameters["period"])
    end_offset = int(parameters["window_end_offset"])
    moments = {int(value) for value in parameters.get("fixed_moments", [])}
    for start in parameters.get("periodic_starts", []):
        for value in range(int(start), sample_count, period):
            moments.add(value)
            moments.add(min(value + end_offset, sample_count - 1))
    selected = sorted(value for value in moments if 0 <= value < sample_count)
    count = float(len(selected))
    return {"design": {
        "objective_value": count,
        "monitoring_count": count,
        "monitoring_times": ", ".join(str(value) for value in selected),
        "selected_moments": selected,
    }}


def _workspace_path(reference: object, work_dir: Path) -> Path:
    if not isinstance(reference, str):
        raise ValueError("data source must be a workspace-relative path")
    normalized = reference.split("#", 1)[0].replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or ".." in parsed.parts or (parsed.parts and ":" in parsed.parts[0]):
        raise ValueError("data source escapes the workspace")
    resolved = (work_dir / Path(*parsed.parts)).resolve()
    if work_dir.resolve() not in (resolved, *resolved.parents):
        raise ValueError("data source escapes the workspace")
    return resolved


def _read_excel_table(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed
    frame = pd.read_excel(
        _workspace_path(inputs["source"], work_dir),
        sheet_name=parameters.get("sheet", 0),
    )
    row_limit = parameters.get("row_limit")
    if row_limit is not None:
        if not isinstance(row_limit, int) or isinstance(row_limit, bool) or row_limit <= 0:
            raise ValueError("row_limit must be a positive integer")
        frame = frame.iloc[:row_limit].copy()
    columns = parameters.get("columns")
    if columns:
        frame = frame.loc[:, columns]
    frame = frame.rename(columns=parameters.get("rename", {}))
    for column in parameters.get("forward_fill", []):
        frame[column] = frame[column].ffill()
    table = {str(column): frame[column].tolist() for column in frame.columns}
    matrix_columns = parameters.get("matrix_columns")
    if matrix_columns:
        matrix_name = str(parameters.get("matrix_name", "matrix"))
        table[matrix_name] = frame.loc[:, matrix_columns].to_numpy(dtype=float).tolist()
    return {"table": table}


def _metrics(residuals: np.ndarray) -> dict[str, float]:
    flat = np.asarray(residuals, dtype=float).reshape(-1)
    return {
        "rmse": float(np.sqrt(np.mean(np.square(flat)))),
        "max_abs_residual": float(np.max(np.abs(flat))),
    }


def _group_key(value: Any) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _zero_intercept_grouped(
    *, inputs: dict, parameters: dict, random_seed, work_dir: Path
) -> dict:
    del random_seed, work_dir
    frame = pd.DataFrame(inputs["table"])
    group_by = parameters["group_by"]
    response = parameters["response"]
    predictors = parameters["predictor_product"]
    groups: dict[str, dict] = {}
    for group_value, group in frame.groupby(group_by, sort=True):
        x = np.ones(len(group), dtype=float)
        for column in predictors:
            x *= group[column].to_numpy(dtype=float)
        y = group[response].to_numpy(dtype=float)
        denominator = float(x @ x)
        if denominator <= 0.0:
            raise ValueError("zero-intercept predictor has no nonzero observations")
        coefficient = float(x @ y / denominator)
        predicted = coefficient * x
        residuals = y - predicted
        leave_one_out: list[float] = []
        if len(x) > 1:
            for index in range(len(x)):
                reduced_x = np.delete(x, index)
                reduced_y = np.delete(y, index)
                reduced_denominator = float(reduced_x @ reduced_x)
                if reduced_denominator > 0.0:
                    leave_one_out.append(float(reduced_x @ reduced_y / reduced_denominator))
        relative_change = (
            max(abs(value - coefficient) for value in leave_one_out) / abs(coefficient)
            if leave_one_out and coefficient != 0.0
            else 0.0
        )
        groups[_group_key(group_value)] = {
            "coefficient": coefficient,
            "x": x.tolist(),
            "observed": y.tolist(),
            "predicted": predicted.tolist(),
            "residuals": residuals.tolist(),
            "metrics": _metrics(residuals),
            "leave_one_out_max_relative_change": float(relative_change),
        }
    return {"fit": {"groups": groups}}


def _basis_column(x: np.ndarray, spec: dict, candidate: float | None) -> np.ndarray:
    def number(name: str, default: float | None = None) -> float:
        value = spec.get(name, default)
        if value == "$candidate":
            if candidate is None:
                raise ValueError("basis references a candidate outside grid search")
            return candidate
        if value is None:
            raise ValueError(f"basis term requires {name}")
        return float(value)

    kind = spec.get("kind")
    if kind == "constant":
        return np.ones_like(x)
    if kind == "identity":
        return x
    if kind == "hinge":
        return np.maximum(x - number("location"), 0.0)
    if kind == "capped_identity":
        return np.minimum(x, number("cap"))
    if kind == "shifted_ramp":
        return np.maximum(x - number("shift"), 0.0)
    if kind == "capped_shifted_ramp":
        shifted = np.maximum(x - number("shift", 0.0), 0.0)
        return np.minimum(shifted, number("cap"))
    if kind == "triangular":
        location = number("location")
        return np.where(x <= location, x, 2.0 * location - x)
    if kind == "sin":
        return np.sin(2.0 * np.pi * x / number("period"))
    if kind == "cos":
        return np.cos(2.0 * np.pi * x / number("period"))
    if kind in {"periodic_mask", "periodic_mask_times_x"}:
        position = (x - number("start")) % number("period")
        mask = (position < number("duration")).astype(float)
        return mask if kind == "periodic_mask" else mask * x
    raise ValueError(f"unsupported basis kind: {kind}")


def _fit_basis(x: np.ndarray, y: np.ndarray, basis: list[dict], candidate=None) -> dict:
    matrix = np.column_stack([_basis_column(x, item, candidate) for item in basis])
    coefficients, _, rank, _ = np.linalg.lstsq(matrix, y, rcond=None)
    predicted = matrix @ coefficients
    residuals = y - predicted
    return {
        "coefficients": coefficients.tolist(),
        "predicted": predicted.tolist(),
        "residuals": residuals.tolist(),
        "metrics": _metrics(residuals),
        "rank": int(rank),
        "design_matrix": matrix.tolist(),
    }


def _basis_least_squares(
    *, inputs: dict, parameters: dict, random_seed, work_dir: Path
) -> dict:
    del random_seed, work_dir
    x = np.asarray(inputs["x"], dtype=float)
    y = np.asarray(inputs["y"], dtype=float)
    search = parameters.get("search")
    if not search:
        return {"fit": _fit_basis(x, y, parameters["basis"])}
    candidates = [float(value) for value in search["candidates"]]
    fits = [_fit_basis(x, y, parameters["basis"], candidate) for candidate in candidates]
    best_index = min(range(len(fits)), key=lambda index: fits[index]["metrics"]["rmse"])
    best = fits[best_index]
    best["selected_candidate"] = candidates[best_index]
    best["candidate_scores"] = [item["metrics"]["rmse"] for item in fits]
    return {"fit": best}


def _ridge_huber_fit(
    design: np.ndarray,
    target: np.ndarray,
    *,
    alpha: float,
    huber_delta: float,
    max_iterations: int,
    tolerance: float,
) -> tuple[np.ndarray, bool, int]:
    penalty = np.eye(design.shape[1], dtype=float)
    penalty[0, 0] = 0.0

    def solve(weights: np.ndarray) -> np.ndarray:
        weighted_design = design * weights[:, None]
        return np.linalg.solve(
            design.T @ weighted_design + alpha * penalty,
            design.T @ (weights * target),
        )

    coefficients = solve(np.ones(len(target), dtype=float))
    converged = False
    iteration = 0
    for iteration in range(1, max_iterations + 1):
        residuals = target - design @ coefficients
        median = float(np.median(residuals))
        scale = float(np.median(np.abs(residuals - median)) / 0.6744897501960817)
        scale = max(scale, np.finfo(float).eps)
        cutoff = huber_delta * scale
        absolute = np.abs(residuals)
        weights = np.ones(len(target), dtype=float)
        outside = absolute > cutoff
        weights[outside] = cutoff / absolute[outside]
        updated = solve(weights)
        relative_change = float(
            np.linalg.norm(updated - coefficients)
            / max(np.linalg.norm(coefficients), np.finfo(float).eps)
        )
        coefficients = updated
        if relative_change <= tolerance:
            converged = True
            break
    return coefficients, converged, iteration


def _future_basis_matrix(time: np.ndarray, period: float | None) -> np.ndarray:
    columns = [np.ones_like(time), time]
    if period is not None:
        angle = 2.0 * np.pi * time / period
        columns.extend([np.sin(angle), np.cos(angle)])
    return np.column_stack(columns)


def _fit_future_basis(
    train_time: np.ndarray,
    train_target: np.ndarray,
    predict_time: np.ndarray,
    period: float | None,
) -> tuple[np.ndarray, np.ndarray, int]:
    design = _future_basis_matrix(train_time, period)
    coefficients, _, rank, _ = np.linalg.lstsq(design, train_target, rcond=None)
    predictions = _future_basis_matrix(predict_time, period) @ coefficients
    return coefficients, predictions, int(rank)


def _period_label(period: float) -> str:
    return str(int(period)) if period.is_integer() else str(period)


def _seasonal_future(target: list[float], season_length: int, horizon: int) -> list[float]:
    extended = list(target)
    predictions: list[float] = []
    for _ in range(horizon):
        value = float(extended[-season_length])
        predictions.append(value)
        extended.append(value)
    return predictions


def _future_basis_rolling_origin(
    *,
    inputs: dict,
    parameters: dict,
    random_seed,
    work_dir: Path,
) -> dict:
    del random_seed, work_dir
    try:
        time = np.asarray(inputs["time"], dtype=float).reshape(-1)
        target = np.asarray(inputs["target"], dtype=float).reshape(-1)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("forecast time and target must be numeric vectors") from error
    if len(time) != len(target) or not len(time):
        raise ValueError("forecast time and target must have the same nonzero length")
    if not np.all(np.isfinite(time)) or not np.all(np.isfinite(target)):
        raise ValueError("forecast inputs must be finite")
    if np.any(np.diff(time) <= 0.0):
        raise ValueError("historical time must be strictly increasing")

    if "future_time" not in parameters:
        raise ValueError("future_time is required")
    try:
        future_time = np.asarray(parameters["future_time"], dtype=float).reshape(-1)
    except (TypeError, ValueError) as error:
        raise ValueError("future_time must be a numeric vector") from error
    if (
        not len(future_time)
        or not np.all(np.isfinite(future_time))
        or np.any(np.diff(future_time) <= 0.0)
        or future_time[0] <= time[-1]
    ):
        raise ValueError("future_time must be finite, strictly increasing, and after history")

    try:
        periods = [float(value) for value in parameters["candidate_periods"]]
        season_length = int(parameters["season_length"])
        initial_train_size = int(parameters["initial_train_size"])
        interval_level = float(parameters["interval_level"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("forecast candidate, season, training, and interval parameters are required") from error
    if (
        not periods
        or any(not math.isfinite(value) or value <= 0.0 for value in periods)
        or len(periods) != len(set(periods))
    ):
        raise ValueError("candidate_periods must be unique positive finite values")
    if season_length <= 0 or initial_train_size < max(4, season_length):
        raise ValueError("forecast samples must cover the seasonal baseline and basis rank")
    if len(target) - initial_train_size < 3:
        raise ValueError("forecast requires at least three rolling-origin validation samples")
    if not 0.0 < interval_level < 1.0:
        raise ValueError("interval_level must be between zero and one")

    candidate_specs: list[tuple[str, float | None]] = [("linear_trend", None)]
    candidate_specs.extend(
        (f"trend_period_{_period_label(period)}", period)
        for period in periods
    )
    origins = list(range(initial_train_size, len(target)))
    actual = target[origins]
    candidates: list[dict[str, Any]] = []
    for candidate_id, period in candidate_specs:
        rolling_predictions: list[float] = []
        folds: list[dict[str, float | int]] = []
        for origin in origins:
            _, prediction, _ = _fit_future_basis(
                time[:origin],
                target[:origin],
                time[origin : origin + 1],
                period,
            )
            rolling_predictions.append(float(prediction[0]))
            folds.append({
                "origin_index": origin,
                "max_train_time": float(time[origin - 1]),
                "validation_time": float(time[origin]),
            })
        residuals = actual - np.asarray(rolling_predictions)
        candidates.append({
            "id": candidate_id,
            "period": period,
            "rolling_predictions": rolling_predictions,
            "rolling_rmse": _metrics(residuals)["rmse"],
            "folds": folds,
        })

    baseline_rolling = np.asarray([target[origin - season_length] for origin in origins])
    baseline_metrics = _metrics(actual - baseline_rolling)
    baseline_future = _seasonal_future(target.tolist(), season_length, len(future_time))

    selected = min(candidates, key=lambda item: (item["rolling_rmse"], item["id"]))
    selected_period = selected["period"]
    if baseline_metrics["rmse"] <= selected["rolling_rmse"]:
        selected_id = "seasonal_naive"
        selected_period = None
        coefficients = np.asarray([], dtype=float)
        rank = 0
        selected_rolling = baseline_rolling
        future_predictions = np.asarray(baseline_future, dtype=float)
        perturbed_target = target.copy()
        perturbation = max(abs(float(perturbed_target[-1])) * 0.01, 1e-6)
        perturbed_target[-1] += perturbation
        reduced_predictions = np.asarray(
            _seasonal_future(perturbed_target.tolist(), season_length, len(future_time)),
            dtype=float,
        )
        reduced_coefficients = np.asarray([], dtype=float)
        sensitivity_method = "last_observation_perturbation"
    else:
        selected_id = selected["id"]
        coefficients, future_predictions, rank = _fit_future_basis(
            time,
            target,
            future_time,
            selected_period,
        )
        selected_rolling = np.asarray(selected["rolling_predictions"], dtype=float)
        reduced_coefficients, reduced_predictions, _ = _fit_future_basis(
            time[:-1],
            target[:-1],
            future_time,
            selected_period,
        )
        sensitivity_method = "drop_last_observation"
    rolling_residuals = actual - selected_rolling
    metrics = _metrics(rolling_residuals)

    degrees_of_freedom = max(len(rolling_residuals) - 1, 1)
    residual_scale = float(np.sqrt(np.sum(np.square(rolling_residuals)) / degrees_of_freedom))
    quantile = NormalDist().inv_cdf(0.5 + interval_level / 2.0)
    margin = quantile * residual_scale
    lower = future_predictions - margin
    upper = future_predictions + margin

    denominator = np.maximum(np.abs(future_predictions), 1e-10)
    sensitivity = float(np.max(np.abs(reduced_predictions - future_predictions) / denominator))
    sensitivity_threshold = float(parameters.get("sensitivity_threshold", 0.2))

    return {"forecast": {
        "history_time": time.tolist(),
        "history_target": target.tolist(),
        "future_time": future_time.tolist(),
        "future_predictions": future_predictions.tolist(),
        "candidates": candidates,
        "selected_model": {
            "id": selected_id,
            "period": selected_period,
            "coefficients": coefficients.tolist(),
            "design_rank": rank,
        },
        "rolling_origin": {
            "initial_train_size": initial_train_size,
            "origins": origins,
            "actual": actual.tolist(),
            "folds": selected["folds"],
        },
        "observed": actual.tolist(),
        "predicted": selected_rolling.tolist(),
        "residuals": rolling_residuals.tolist(),
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
        "baseline": {
            "method": "seasonal_naive",
            "season_length": season_length,
            "rolling_predictions": baseline_rolling.tolist(),
            "future_predictions": baseline_future,
        },
        "prediction_interval": {
            "level": interval_level,
            "lower": lower.tolist(),
            "upper": upper.tolist(),
            "residual_scale": residual_scale,
        },
        "cross_validation": {
            "kind": "rolling_origin_one_step",
            "fold_rmse": [item["rolling_rmse"] for item in candidates] + [baseline_metrics["rmse"]],
            "max_allowed_rmse": baseline_metrics["rmse"],
        },
        "leakage_check": {
            "passed": all(fold["max_train_time"] < fold["validation_time"] for fold in selected["folds"]),
            "folds": selected["folds"],
        },
        "sensitivity": {
            "method": sensitivity_method,
            "reference_coefficients": reduced_coefficients.tolist(),
            "max_relative_change": sensitivity,
            "threshold": sensitivity_threshold,
            "passed": sensitivity <= sensitivity_threshold,
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(future_predictions))),
            "constraints_satisfied": bool(np.all(lower <= future_predictions) and np.all(future_predictions <= upper)),
        },
    }}


def _robust_regularized_time_split(
    *,
    inputs: dict,
    parameters: dict,
    random_seed,
    work_dir: Path,
) -> dict:
    del random_seed, work_dir
    time = np.asarray(inputs["time"], dtype=float).reshape(-1)
    features = np.asarray(inputs["features"], dtype=float)
    target = np.asarray(inputs["target"], dtype=float).reshape(-1)
    if features.ndim == 1:
        features = features[:, None]
    if len(time) != len(target) or features.shape[0] != len(target):
        raise ValueError("time, features, and target must have the same row count")
    if not np.all(np.isfinite(time)) or not np.all(np.isfinite(features)) or not np.all(np.isfinite(target)):
        raise ValueError("forecast inputs must be finite")

    train_size = int(parameters["train_size"])
    if train_size < 3 or train_size >= len(target):
        raise ValueError("train_size must leave at least three training rows and one holdout row")
    alpha = float(parameters["alpha"])
    huber_delta = float(parameters["huber_delta"])
    interval_level = float(parameters["interval_level"])
    max_iterations = int(parameters.get("max_iterations", 100))
    tolerance = float(parameters.get("tolerance", 1e-8))
    if alpha < 0 or huber_delta <= 0 or not 0 < interval_level < 1:
        raise ValueError("invalid regularization, Huber, or interval parameters")

    train_features = features[:train_size]
    holdout_features = features[train_size:]
    train_target = target[:train_size]
    holdout_target = target[train_size:]
    train_design = np.column_stack([np.ones(train_size), train_features])
    holdout_design = np.column_stack([np.ones(len(holdout_target)), holdout_features])
    coefficients, converged, iterations = _ridge_huber_fit(
        train_design,
        train_target,
        alpha=alpha,
        huber_delta=huber_delta,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    train_predictions = train_design @ coefficients
    predictions = holdout_design @ coefficients
    residuals = holdout_target - predictions
    metrics = _metrics(residuals)
    baseline_predictions = np.full(len(holdout_target), train_target[-1], dtype=float)
    baseline_metrics = _metrics(holdout_target - baseline_predictions)

    degrees_of_freedom = max(train_size - train_design.shape[1], 1)
    residual_scale = float(
        np.sqrt(np.sum(np.square(train_target - train_predictions)) / degrees_of_freedom)
    )
    quantile = NormalDist().inv_cdf(0.5 + interval_level / 2.0)
    margin = quantile * residual_scale
    lower = predictions - margin
    upper = predictions + margin
    coverage = float(np.mean((holdout_target >= lower) & (holdout_target <= upper)))

    perturbed_alpha = alpha * 1.1 if alpha > 0 else 1e-8
    perturbed, _, _ = _ridge_huber_fit(
        train_design,
        train_target,
        alpha=perturbed_alpha,
        huber_delta=huber_delta,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    coefficient_scale = max(float(np.linalg.norm(coefficients)), np.finfo(float).eps)
    sensitivity = float(np.linalg.norm(perturbed - coefficients) / coefficient_scale)
    leakage_passed = bool(
        np.all(np.diff(time[:train_size]) >= 0)
        and np.all(np.diff(time[train_size:]) >= 0)
        and np.max(time[:train_size]) < np.min(time[train_size:])
    )

    return {"evaluation": {
        "coefficients": coefficients.tolist(),
        "observed": holdout_target.tolist(),
        "predicted": predictions.tolist(),
        "residuals": residuals.tolist(),
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
        "baseline_predicted": baseline_predictions.tolist(),
        "split": {
            "train_indices": list(range(train_size)),
            "holdout_indices": list(range(train_size, len(target))),
        },
        "leakage_check": {
            "passed": leakage_passed,
            "max_train_time": float(np.max(time[:train_size])),
            "min_holdout_time": float(np.min(time[train_size:])),
        },
        "regularization": {"kind": "ridge", "alpha": alpha},
        "robust_fit": {
            "kind": "huber_irls",
            "delta": huber_delta,
            "converged": converged,
            "iterations": iterations,
        },
        "prediction_interval": {
            "level": interval_level,
            "lower": lower.tolist(),
            "upper": upper.tolist(),
            "empirical_coverage": coverage,
        },
        "cross_validation": {
            "kind": "temporal_holdout",
            "fold_rmse": [metrics["rmse"]],
            "max_allowed_rmse": baseline_metrics["rmse"],
        },
        "sensitivity": {
            "max_relative_change": sensitivity,
            "threshold": float(parameters.get("sensitivity_threshold", 0.1)),
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(predictions))),
            "constraints_satisfied": True,
        },
    }}


def _linear_fit(design: np.ndarray, observed: np.ndarray) -> tuple[np.ndarray, float]:
    coefficients = np.linalg.lstsq(design, observed, rcond=None)[0]
    residuals = observed - design @ coefficients
    return design @ coefficients, float(residuals @ residuals)


def _quadratic_components(spec: dict, dimension: int) -> tuple[np.ndarray, np.ndarray, float]:
    quadratic = np.asarray(spec["quadratic"], dtype=float)
    linear = np.asarray(spec["linear"], dtype=float).reshape(-1)
    constant = float(spec.get("constant", 0.0))
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


def _constrained_quadratic_multistart(
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

    linear = parameters.get("linear_constraints", {})
    a_ub = np.asarray(linear.get("a_ub", []), dtype=float).reshape(-1, dimension)
    b_ub = np.asarray(linear.get("b_ub", []), dtype=float).reshape(-1)
    a_eq = np.asarray(linear.get("a_eq", []), dtype=float).reshape(-1, dimension)
    b_eq = np.asarray(linear.get("b_eq", []), dtype=float).reshape(-1)
    if len(a_ub) != len(b_ub) or len(a_eq) != len(b_eq):
        raise ValueError("linear constraint matrices and bounds have inconsistent rows")

    quadratic_constraints = [
        (
            _quadratic_components(spec, dimension),
            float(spec["upper_bound"]),
        )
        for spec in parameters.get("quadratic_constraints", [])
    ]
    tolerance = float(parameters.get("constraint_tolerance", 1e-7))
    multistart_tolerance = float(parameters.get("multistart_tolerance", 1e-6))

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
        "quadratic_constraints": parameters.get("quadratic_constraints", []),
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
            "threshold": float(parameters.get("sensitivity_threshold", 1e-4)),
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(solution))),
            "constraints_satisfied": feasibility["constraints_satisfied"],
        },
    }}


def _mcda_normalize(
    matrix: np.ndarray,
    criterion_types: list[str],
) -> np.ndarray:
    if matrix.ndim != 2 or matrix.shape[0] < 2 or matrix.shape[1] < 1:
        raise ValueError("decision matrix must contain at least two alternatives")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("decision matrix must contain only finite values")
    if len(criterion_types) != matrix.shape[1] or any(
        item not in {"benefit", "cost"} for item in criterion_types
    ):
        raise ValueError("criterion_types must declare benefit or cost for every column")
    minima = np.min(matrix, axis=0)
    maxima = np.max(matrix, axis=0)
    spans = maxima - minima
    if np.any(spans <= 0.0):
        raise ValueError("every criterion must discriminate between alternatives")
    normalized = (matrix - minima) / spans
    for column, criterion_type in enumerate(criterion_types):
        if criterion_type == "cost":
            normalized[:, column] = 1.0 - normalized[:, column]
    return normalized


def _mcda_ranking(scores: np.ndarray) -> list[int]:
    alternative_ids = np.arange(len(scores))
    return np.lexsort((alternative_ids, -scores)).astype(int).tolist()


def _mcda_weighted_ranking(
    *,
    inputs: dict,
    parameters: dict,
    random_seed,
    work_dir: Path,
) -> dict:
    del random_seed, work_dir
    matrix = np.asarray(inputs["matrix"], dtype=float)
    criterion_types = list(parameters["criterion_types"])
    normalized = _mcda_normalize(matrix, criterion_types)
    method = parameters["weight_method"]
    if method == "provided":
        weights = np.asarray(parameters.get("weights", []), dtype=float).reshape(-1)
    elif method == "entropy":
        probabilities = normalized / np.sum(normalized, axis=0)
        positive = probabilities > 0.0
        entropy = -np.sum(
            np.where(positive, probabilities * np.log(np.where(positive, probabilities, 1.0)), 0.0),
            axis=0,
        ) / np.log(matrix.shape[0])
        weights = 1.0 - entropy
    else:
        raise ValueError("weight_method must be provided or entropy")
    if (
        weights.shape != (matrix.shape[1],)
        or not np.all(np.isfinite(weights))
        or np.any(weights < 0.0)
        or float(np.sum(weights)) <= 0.0
    ):
        raise ValueError("weights must be finite, non-negative, and match the criteria")
    weights = weights / np.sum(weights)
    scores = normalized @ weights
    ranking = _mcda_ranking(scores)

    fraction = float(parameters.get("perturbation_fraction", 0.05))
    stability_threshold = float(parameters.get("stability_threshold", 0.8))
    if not 0.0 < fraction < 1.0:
        raise ValueError("perturbation_fraction must be between zero and one")
    if not 0.0 <= stability_threshold <= 1.0:
        raise ValueError("stability_threshold must be between zero and one")
    base_positions = np.empty(len(ranking), dtype=float)
    base_positions[ranking] = np.arange(len(ranking), dtype=float)
    runs: list[dict] = []
    correlations: list[float] = []
    for criterion in range(len(weights)):
        for direction in (-1.0, 1.0):
            perturbed = weights.copy()
            perturbed[criterion] *= 1.0 + direction * fraction
            perturbed /= np.sum(perturbed)
            perturbed_ranking = _mcda_ranking(normalized @ perturbed)
            positions = np.empty(len(ranking), dtype=float)
            positions[perturbed_ranking] = np.arange(len(ranking), dtype=float)
            correlations.append(float(np.corrcoef(base_positions, positions)[0, 1]))
            runs.append({
                "weights": perturbed.tolist(),
                "ranking": perturbed_ranking,
            })
    top_retention = sum(run["ranking"][0] == ranking[0] for run in runs) / len(runs)
    minimum_correlation = min(correlations)
    stable = top_retention >= stability_threshold and minimum_correlation >= stability_threshold
    return {"decision": {
        "decision_matrix": matrix.tolist(),
        "criterion_types": criterion_types,
        "normalized_matrix": normalized.tolist(),
        "weight_method": method,
        "weights": weights.tolist(),
        "scores": scores.tolist(),
        "ranking": ranking,
        "weight_perturbation": {
            "fraction": fraction,
            "runs": runs,
            "top_rank_retention_rate": top_retention,
            "min_rank_correlation": minimum_correlation,
            "threshold": stability_threshold,
            "stable": stable,
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(scores))),
            "constraints_satisfied": bool(
                np.all(weights >= 0.0) and np.isclose(np.sum(weights), 1.0)
            ),
        },
    }}


def _rk4_affine_path(
    time: np.ndarray,
    initial_state: np.ndarray,
    matrix: np.ndarray,
    offset: np.ndarray,
    substeps: int,
) -> np.ndarray:
    states = [np.asarray(initial_state, dtype=float)]
    for start, stop in zip(time[:-1], time[1:]):
        step = float(stop - start) / substeps
        state = states[-1].copy()
        for _ in range(substeps):
            derivative = lambda value: matrix @ value + offset
            k1 = derivative(state)
            k2 = derivative(state + 0.5 * step * k1)
            k3 = derivative(state + 0.5 * step * k2)
            k4 = derivative(state + step * k3)
            state = state + step * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        states.append(state)
    return np.asarray(states, dtype=float)


def _affine_ode_rk4(
    *,
    inputs: dict,
    parameters: dict,
    random_seed,
    work_dir: Path,
) -> dict:
    del random_seed, work_dir
    time = np.asarray(inputs["time"], dtype=float).reshape(-1)
    observed = np.asarray(inputs["states"], dtype=float)
    if (
        time.ndim != 1
        or len(time) < 3
        or observed.ndim != 2
        or observed.shape[0] != len(time)
        or observed.shape[1] < 1
        or not np.all(np.isfinite(time))
        or not np.all(np.isfinite(observed))
        or np.any(np.diff(time) <= 0.0)
    ):
        raise ValueError("ODE observations require finite states at at least three increasing times")
    dimension = observed.shape[1]
    intervals = np.diff(time)
    midpoint_states = 0.5 * (observed[:-1] + observed[1:])
    derivatives = np.diff(observed, axis=0) / intervals[:, None]
    include_offset = parameters.get("include_offset", True)
    if not isinstance(include_offset, bool):
        raise ValueError("include_offset must be a boolean")
    design = (
        np.column_stack([midpoint_states, np.ones(len(midpoint_states))])
        if include_offset
        else midpoint_states
    )
    coefficients, _, rank, _ = np.linalg.lstsq(design, derivatives, rcond=None)
    if rank < min(design.shape):
        raise ValueError("affine ODE parameter design is rank deficient")
    condition_number = float(np.linalg.cond(design))
    max_condition_number = float(parameters.get("max_condition_number", 1e10))
    if not np.isfinite(max_condition_number) or max_condition_number <= 1.0:
        raise ValueError("max_condition_number must be finite and greater than one")
    if not np.isfinite(condition_number) or condition_number > max_condition_number:
        raise ValueError(
            "affine ODE parameter design is ill-conditioned: "
            f"condition_number={condition_number:.6g} exceeds {max_condition_number:.6g}"
        )
    dynamics_matrix = coefficients[:-1].T if include_offset else coefficients.T
    offset = coefficients[-1] if include_offset else np.zeros(dimension, dtype=float)

    substeps = parameters.get("integration_substeps", 1)
    if not isinstance(substeps, int) or isinstance(substeps, bool) or substeps < 1:
        raise ValueError("integration_substeps must be a positive integer")
    simulated = _rk4_affine_path(
        time,
        observed[0],
        dynamics_matrix,
        offset,
        substeps,
    )
    refined_substeps = substeps * 2
    refined = _rk4_affine_path(
        time,
        observed[0],
        dynamics_matrix,
        offset,
        refined_substeps,
    )
    residuals = observed - simulated
    baseline = np.repeat(observed[[0]], len(observed), axis=0)
    baseline_residuals = observed - baseline

    lower = np.asarray(parameters.get("lower_bounds", []), dtype=float)
    upper = np.asarray(parameters.get("upper_bounds", []), dtype=float)
    if (
        lower.shape != (dimension,)
        or upper.shape != (dimension,)
        or not np.all(np.isfinite(lower))
        or not np.all(np.isfinite(upper))
        or np.any(lower > upper)
    ):
        raise ValueError(
            "ODE simulation requires finite lower_bounds and upper_bounds for every state"
        )
    boundary_violation = float(max(
        np.max(lower - observed, initial=0.0),
        np.max(observed - upper, initial=0.0),
        np.max(lower - simulated, initial=0.0),
        np.max(simulated - upper, initial=0.0),
        0.0,
    ))
    boundary_tolerance = float(parameters.get("boundary_tolerance", 1e-8))

    raw_vectors = parameters.get("conservation_vectors", [])
    conservation_vectors = np.asarray(raw_vectors, dtype=float)
    conservation_tolerance = float(parameters.get("conservation_tolerance", 1e-8))
    if raw_vectors:
        conservation_vectors = conservation_vectors.reshape(-1, dimension)
        observed_totals = observed @ conservation_vectors.T
        simulated_totals = simulated @ conservation_vectors.T
        observed_drift = float(np.max(np.abs(observed_totals - observed_totals[[0]])))
        simulated_drift = float(np.max(np.abs(simulated_totals - simulated_totals[[0]])))
        conservation_applicable = True
        conservation_passed = max(observed_drift, simulated_drift) <= conservation_tolerance
        conservation_reason = "declared conservation vectors checked"
    else:
        observed_drift = 0.0
        simulated_drift = 0.0
        conservation_applicable = False
        conservation_passed = True
        conservation_reason = "no conservation vector declared"

    scale = max(float(np.max(np.abs(refined))), np.finfo(float).eps)
    step_change = float(np.max(np.abs(simulated - refined)) / scale)
    step_threshold = float(parameters.get("step_sensitivity_threshold", 1e-3))
    if boundary_tolerance < 0.0 or conservation_tolerance < 0.0 or step_threshold < 0.0:
        raise ValueError("ODE validation tolerances must be non-negative")
    constraints_satisfied = (
        boundary_violation <= boundary_tolerance and conservation_passed
    )
    return {"simulation": {
        "time": time.tolist(),
        "observed_states": observed.tolist(),
        "parameter_estimate": {
            "matrix": dynamics_matrix.tolist(),
            "offset": offset.tolist(),
            "include_offset": include_offset,
        },
        "identifiability": {
            "design_rank": int(rank),
            "parameter_count_per_state": int(design.shape[1]),
            "identifiable": bool(rank == design.shape[1]),
            "condition_number": condition_number,
            "max_condition_number": max_condition_number,
        },
        "simulated_states": simulated.tolist(),
        "residuals": residuals.tolist(),
        "metrics": _metrics(residuals),
        "baseline_metrics": _metrics(baseline_residuals),
        "integration": {"method": "rk4", "substeps": substeps},
        "boundary_check": {
            "lower": lower.tolist(),
            "upper": upper.tolist(),
            "tolerance": boundary_tolerance,
            "max_violation": boundary_violation,
            "passed": boundary_violation <= boundary_tolerance,
        },
        "conservation_check": {
            "applicable": conservation_applicable,
            "reason": conservation_reason,
            "vectors": conservation_vectors.tolist() if raw_vectors else [],
            "observed_max_drift": observed_drift,
            "simulated_max_drift": simulated_drift,
            "tolerance": conservation_tolerance,
            "passed": conservation_passed,
        },
        "step_sensitivity": {
            "refined_substeps": refined_substeps,
            "max_relative_change": step_change,
            "threshold": step_threshold,
            "passed": step_change <= step_threshold,
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(simulated))),
            "constraints_satisfied": constraints_satisfied,
        },
    }}


def _graph_reachable(
    start: int,
    adjacency: list[list[int]],
) -> list[int]:
    visited = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return sorted(visited)


def _shortest_path_max_flow(
    *,
    inputs: dict,
    parameters: dict,
    random_seed,
    work_dir: Path,
) -> dict:
    del random_seed, work_dir
    metrics = np.asarray(inputs["metrics"], dtype=float)
    endpoints = np.asarray(parameters.get("endpoints", []), dtype=float)
    if (
        metrics.ndim != 2
        or metrics.shape[1] != 2
        or len(metrics) < 1
        or endpoints.shape != metrics.shape
        or not np.all(np.isfinite(metrics))
        or not np.all(np.isfinite(endpoints))
    ):
        raise ValueError(
            "graph metrics and endpoints must be aligned finite matrices with two columns"
        )
    raw_edges = np.column_stack([endpoints, metrics])
    node_count = parameters.get("node_count")
    source = parameters.get("source")
    sink = parameters.get("sink")
    if (
        not isinstance(node_count, int)
        or isinstance(node_count, bool)
        or node_count < 2
        or not isinstance(source, int)
        or isinstance(source, bool)
        or not isinstance(sink, int)
        or isinstance(sink, bool)
        or source == sink
        or source not in range(node_count)
        or sink not in range(node_count)
    ):
        raise ValueError("graph node_count, source, and sink are invalid")
    endpoints = raw_edges[:, :2]
    if (
        not np.all(endpoints == np.floor(endpoints))
        or np.any(endpoints < 0)
        or np.any(endpoints >= node_count)
        or np.any(endpoints[:, 0] == endpoints[:, 1])
        or np.any(raw_edges[:, 2:] < 0.0)
    ):
        raise ValueError("graph edges contain invalid endpoints, weights, or capacities")
    starts = endpoints[:, 0].astype(int)
    targets = endpoints[:, 1].astype(int)
    weights = raw_edges[:, 2]
    capacities = raw_edges[:, 3]

    forward: list[list[tuple[int, float, int]]] = [[] for _ in range(node_count)]
    reverse: list[list[int]] = [[] for _ in range(node_count)]
    weak: list[list[int]] = [[] for _ in range(node_count)]
    for edge_id, (start, target, weight) in enumerate(zip(starts, targets, weights)):
        start_node = int(start)
        target_node = int(target)
        forward[start_node].append((target_node, float(weight), edge_id))
        reverse[target_node].append(start_node)
        weak[start_node].append(target_node)
        weak[target_node].append(start_node)

    distances = np.full(node_count, np.inf)
    distances[source] = 0.0
    predecessor_node = np.full(node_count, -1, dtype=int)
    predecessor_edge = np.full(node_count, -1, dtype=int)
    queue: list[tuple[float, int]] = [(0.0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > distances[node]:
            continue
        for neighbor, weight, edge_id in forward[node]:
            candidate = distance + weight
            if candidate < distances[neighbor]:
                distances[neighbor] = candidate
                predecessor_node[neighbor] = node
                predecessor_edge[neighbor] = edge_id
                heapq.heappush(queue, (candidate, neighbor))
    if np.isfinite(distances[sink]):
        path = [sink]
        path_edges: list[int] = []
        current = sink
        while current != source:
            path_edges.append(int(predecessor_edge[current]))
            current = int(predecessor_node[current])
            path.append(current)
        path.reverse()
        path_edges.reverse()
        shortest_distance: float | None = float(distances[sink])
    else:
        path = []
        path_edges = []
        shortest_distance = None

    hop_predecessor = np.full(node_count, -1, dtype=int)
    hop_edge = np.full(node_count, -1, dtype=int)
    hop_seen = {source}
    hop_queue = deque([source])
    while hop_queue and sink not in hop_seen:
        node = hop_queue.popleft()
        for neighbor, _, edge_id in forward[node]:
            if neighbor not in hop_seen:
                hop_seen.add(neighbor)
                hop_predecessor[neighbor] = node
                hop_edge[neighbor] = edge_id
                hop_queue.append(neighbor)
    if sink in hop_seen:
        baseline_edges: list[int] = []
        current = sink
        while current != source:
            baseline_edges.append(int(hop_edge[current]))
            current = int(hop_predecessor[current])
        baseline_distance: float | None = float(np.sum(weights[baseline_edges]))
    else:
        baseline_distance = None

    flows = np.zeros(len(raw_edges), dtype=float)

    def residual_arcs(node: int):
        for edge_id, (start, target, capacity) in enumerate(zip(starts, targets, capacities)):
            if start == node and capacity - flows[edge_id] > 1e-12:
                yield int(target), edge_id, 1, float(capacity - flows[edge_id])
            if target == node and flows[edge_id] > 1e-12:
                yield int(start), edge_id, -1, float(flows[edge_id])

    while True:
        parent: dict[int, tuple[int, int, int, float]] = {}
        seen = {source}
        flow_queue = deque([source])
        while flow_queue and sink not in seen:
            node = flow_queue.popleft()
            for neighbor, edge_id, direction, residual_capacity in residual_arcs(node):
                if neighbor not in seen:
                    seen.add(neighbor)
                    parent[neighbor] = (node, edge_id, direction, residual_capacity)
                    flow_queue.append(neighbor)
        if sink not in seen:
            break
        augmentation = float("inf")
        current = sink
        while current != source:
            previous, _, _, residual_capacity = parent[current]
            augmentation = min(augmentation, residual_capacity)
            current = previous
        current = sink
        while current != source:
            previous, edge_id, direction, _ = parent[current]
            flows[edge_id] += direction * augmentation
            current = previous

    residual_adjacency: list[list[int]] = [[] for _ in range(node_count)]
    for node in range(node_count):
        residual_adjacency[node] = [neighbor for neighbor, _, _, _ in residual_arcs(node)]
    source_side = _graph_reachable(source, residual_adjacency)
    source_set = set(source_side)
    sink_side = [node for node in range(node_count) if node not in source_set]
    cut_edge_ids = [
        edge_id
        for edge_id, (start, target) in enumerate(zip(starts, targets))
        if start in source_set and target not in source_set
    ]
    cut_capacity = float(np.sum(capacities[cut_edge_ids]))
    balances = np.zeros(node_count, dtype=float)
    for start, target, flow in zip(starts, targets, flows):
        balances[start] -= flow
        balances[target] += flow
    flow_value = float(-balances[source])
    internal = [node for node in range(node_count) if node not in {source, sink}]
    conservation_violation = float(np.max(np.abs(balances[internal]), initial=0.0))
    capacity_violation = float(max(
        np.max(-flows, initial=0.0),
        np.max(flows - capacities, initial=0.0),
        0.0,
    ))
    reachable_from_source = _graph_reachable(
        source,
        [[neighbor for neighbor, _, _ in items] for items in forward],
    )
    forward_plain = [[neighbor for neighbor, _, _ in items] for items in forward]
    strongly_connected = (
        len(_graph_reachable(0, forward_plain)) == node_count
        and len(_graph_reachable(0, reverse)) == node_count
    )
    edges = [
        {
            "id": edge_id,
            "source": int(start),
            "target": int(target),
            "weight": float(weight),
            "capacity": float(capacity),
        }
        for edge_id, (start, target, weight, capacity) in enumerate(
            zip(starts, targets, weights, capacities)
        )
    ]
    return {"analysis": {
        "node_count": node_count,
        "source": source,
        "sink": sink,
        "edges": edges,
        "connectivity": {
            "reachable_from_source": reachable_from_source,
            "weakly_connected": len(_graph_reachable(0, weak)) == node_count,
            "strongly_connected": strongly_connected,
        },
        "baseline": {
            "shortest_hop_distance": baseline_distance,
            "flow_value": 0.0,
        },
        "shortest_path": {
            "reachable": shortest_distance is not None,
            "path": path,
            "edge_ids": path_edges,
            "distance": shortest_distance,
            "distance_labels": [float(value) if np.isfinite(value) else None for value in distances],
        },
        "max_flow": {
            "value": flow_value,
            "edge_flows": flows.tolist(),
            "capacity_violation": capacity_violation,
            "conservation_violation": conservation_violation,
            "min_cut": {
                "source_side": source_side,
                "sink_side": sink_side,
                "edge_ids": cut_edge_ids,
                "capacity": cut_capacity,
            },
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(flows))),
            "constraints_satisfied": bool(
                capacity_violation <= 1e-10
                and conservation_violation <= 1e-10
                and math.isclose(flow_value, cut_capacity, rel_tol=1e-10, abs_tol=1e-10)
            ),
        },
    }}


def _continuous_hinge_bic(
    *, inputs: dict, parameters: dict, random_seed, work_dir: Path
) -> dict:
    del random_seed, work_dir
    x = np.asarray(inputs["x"], dtype=float)
    y = np.asarray(inputs["y"], dtype=float)
    if y.ndim == 1:
        y = y[:, None]
    measurement_count = y.shape[1]
    observation_count = y.size
    baseline_design = np.column_stack([np.ones(len(x)), x])
    baseline_predictions = np.empty_like(y)
    baseline_sse = 0.0
    for column in range(measurement_count):
        predicted, sse = _linear_fit(baseline_design, y[:, column])
        baseline_predictions[:, column] = predicted
        baseline_sse += sse
    epsilon = np.finfo(float).tiny
    baseline_bic = float(
        observation_count * np.log(max(baseline_sse, epsilon) / observation_count)
        + 2 * measurement_count * np.log(observation_count)
    )
    candidates: list[dict] = []
    for candidate in parameters["candidates"]:
        candidate = float(candidate)
        design = np.column_stack([np.ones(len(x)), x, np.maximum(x - candidate, 0.0)])
        predictions = np.empty_like(y)
        total_sse = 0.0
        for column in range(measurement_count):
            predicted, sse = _linear_fit(design, y[:, column])
            predictions[:, column] = predicted
            total_sse += sse
        bic = float(
            observation_count * np.log(max(total_sse, epsilon) / observation_count)
            + (3 * measurement_count + 1) * np.log(observation_count)
        )
        candidates.append({"candidate": candidate, "bic": bic, "predicted": predictions})
    best = min(candidates, key=lambda item: item["bic"])
    improvement = float(baseline_bic - best["bic"])
    supported = improvement > float(parameters.get("support_threshold", 2.0))
    selected = best["predicted"] if supported else baseline_predictions
    residuals = y - selected
    near_optimal = [item["candidate"] for item in candidates if item["bic"] <= best["bic"] + 2.0]
    comparison = {
        **_metrics(residuals),
        "single_line_bic": baseline_bic,
        "best_hinge_bic": float(best["bic"]),
        "bic_improvement": improvement,
        "best_breakpoint_candidate": float(best["candidate"]),
        "candidate_lower": float(min(near_optimal)),
        "candidate_upper": float(max(near_optimal)),
        "breakpoint_supported": supported,
        "candidate_grid": [item["candidate"] for item in candidates],
        "candidate_bic": [float(item["bic"]) for item in candidates],
        "observed": y.tolist(),
        "predicted": selected.tolist(),
        "residuals": residuals.tolist(),
    }
    return {"comparison": comparison}


def _design_rank(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del parameters, random_seed, work_dir
    matrix = np.asarray(inputs["matrix"], dtype=float)
    rank = int(np.linalg.matrix_rank(matrix))
    parameter_count = int(matrix.shape[1])
    return {"diagnostic": {
        "rank": rank,
        "parameter_count": parameter_count,
        "full_column_rank": rank == parameter_count,
    }}


def _leave_one_out(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    x = np.asarray(inputs["x"], dtype=float)
    y = np.asarray(inputs["y"], dtype=float)
    zero_intercept = bool(parameters.get("zero_intercept", False))

    def coefficients(x_values: np.ndarray, y_values: np.ndarray) -> np.ndarray:
        design = x_values[:, None] if zero_intercept else np.column_stack([np.ones(len(x_values)), x_values])
        return np.linalg.lstsq(design, y_values, rcond=None)[0]

    full = coefficients(x, y)
    scale = max(float(np.linalg.norm(full)), np.finfo(float).eps)
    changes = [
        float(np.linalg.norm(coefficients(np.delete(x, index), np.delete(y, index)) - full) / scale)
        for index in range(len(x))
    ]
    return {"diagnostic": {
        "max_relative_change": max(changes, default=0.0),
        "leave_one_out_estimates": changes,
    }}


def _xy_evidence(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed
    relative_path = _workspace_path(parameters["path"], work_dir)
    relative_path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(inputs["x"], inputs["observed"], label="observed", linewidth=2)
    axis.plot(inputs["x"], inputs["predicted"], label="fitted", linewidth=2)
    axis.set_title(str(parameters.get("title", "Observed and fitted")))
    axis.set_xlabel(str(parameters.get("x_label", "x")))
    axis.set_ylabel(str(parameters.get("y_label", "y")))
    axis.legend()
    figure.tight_layout()
    figure.savefig(relative_path, dpi=int(parameters.get("dpi", 150)))
    plt.close(figure)
    return {"figure": {
        "path": PurePosixPath(parameters["path"]).as_posix(),
        "role": str(parameters.get("role", "result_fit")),
        "linked_result_ids": list(parameters.get("linked_result_ids", [])),
    }}
