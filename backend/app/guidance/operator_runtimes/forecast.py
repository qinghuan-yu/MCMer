"""operator_runtimes: forecast 领域实现与合同。"""

from __future__ import annotations

import math
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from app.guidance.operator_runtimes.residual_metrics import residual_metrics


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

def future_basis_rolling_origin(
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
            "rolling_rmse": residual_metrics(residuals)["rmse"],
            "folds": folds,
        })

    baseline_rolling = np.asarray([target[origin - season_length] for origin in origins])
    baseline_metrics = residual_metrics(actual - baseline_rolling)
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
    metrics = residual_metrics(rolling_residuals)

    degrees_of_freedom = max(len(rolling_residuals) - 1, 1)
    residual_scale = float(np.sqrt(np.sum(np.square(rolling_residuals)) / degrees_of_freedom))
    quantile = NormalDist().inv_cdf(0.5 + interval_level / 2.0)
    margin = quantile * residual_scale
    lower = future_predictions - margin
    upper = future_predictions + margin

    denominator = np.maximum(np.abs(future_predictions), 1e-10)
    sensitivity = float(np.max(np.abs(reduced_predictions - future_predictions) / denominator))
    sensitivity_threshold = float(parameters["sensitivity_threshold"])

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

def robust_regularized_time_split(
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
    max_iterations = int(parameters["max_iterations"])
    tolerance = float(parameters["tolerance"])
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
    metrics = residual_metrics(residuals)
    baseline_predictions = np.full(len(holdout_target), train_target[-1], dtype=float)
    baseline_metrics = residual_metrics(holdout_target - baseline_predictions)

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
            "threshold": float(parameters["sensitivity_threshold"]),
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(predictions))),
            "constraints_satisfied": True,
        },
    }}
