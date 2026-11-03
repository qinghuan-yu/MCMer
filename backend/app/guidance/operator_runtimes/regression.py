"""operator_runtimes: regression 领域实现与合同。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.guidance.operator_runtimes.residual_metrics import residual_metrics


def _group_key(value: Any) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)

def zero_intercept_grouped(
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
            "metrics": residual_metrics(residuals),
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
        "metrics": residual_metrics(residuals),
        "rank": int(rank),
        "design_matrix": matrix.tolist(),
    }

def basis_least_squares(
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

def _linear_fit(design: np.ndarray, observed: np.ndarray) -> tuple[np.ndarray, float]:
    coefficients = np.linalg.lstsq(design, observed, rcond=None)[0]
    residuals = observed - design @ coefficients
    return design @ coefficients, float(residuals @ residuals)

def continuous_hinge_bic(
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
        **residual_metrics(residuals),
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
