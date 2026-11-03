"""model_validation: forecast 领域实现与合同。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from statistics import NormalDist
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


def _future_evidence(nodes: list[Mapping[str, Any]]) -> tuple | None:
    node = find_evidence(
        nodes,
        "history_time",
        "history_target",
        "future_time",
        "future_predictions",
        "candidates",
        "selected_model",
        "rolling_origin",
        "baseline",
        "prediction_interval",
        "sensitivity",
    )
    if node is None:
        return None
    try:
        history_time = np.asarray(node["history_time"], dtype=float)
        history_target = np.asarray(node["history_target"], dtype=float)
        future_time = np.asarray(node["future_time"], dtype=float)
        future_predictions = np.asarray(node["future_predictions"], dtype=float)
    except (TypeError, ValueError):
        return None
    if (
        history_time.ndim != 1
        or history_target.shape != history_time.shape
        or future_time.ndim != 1
        or future_predictions.shape != future_time.shape
        or not len(history_time)
        or not len(future_time)
        or not all(np.all(np.isfinite(values)) for values in (
            history_time,
            history_target,
            future_time,
            future_predictions,
        ))
    ):
        return None
    return node, history_time, history_target, future_time, future_predictions

def _future_design(time: np.ndarray, period: float | None) -> np.ndarray:
    columns = [np.ones_like(time), time]
    if period is not None:
        angle = 2.0 * np.pi * time / period
        columns.extend([np.sin(angle), np.cos(angle)])
    return np.column_stack(columns)

def _future_fit(
    time: np.ndarray,
    target: np.ndarray,
    predict_time: np.ndarray,
    period: float | None,
) -> tuple[np.ndarray, np.ndarray, int]:
    design = _future_design(time, period)
    coefficients, _, rank, _ = np.linalg.lstsq(design, target, rcond=None)
    return coefficients, _future_design(predict_time, period) @ coefficients, int(rank)

def _future_seasonal(target: np.ndarray, season_length: int, horizon: int) -> np.ndarray:
    extended = target.tolist()
    predictions: list[float] = []
    for _ in range(horizon):
        value = float(extended[-season_length])
        predictions.append(value)
        extended.append(value)
    return np.asarray(predictions, dtype=float)

def _future_rmse(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(actual - predicted))))

def _future_candidate_predictions(
    evidence: tuple,
) -> tuple[dict[str, tuple[np.ndarray, float | None, float]], list[int]] | None:
    node, time, target, _, _ = evidence
    rolling = node["rolling_origin"]
    candidates = node["candidates"]
    if not isinstance(rolling, Mapping) or not isinstance(candidates, list):
        return None
    initial = rolling.get("initial_train_size")
    origins = rolling.get("origins")
    if not isinstance(initial, int) or isinstance(initial, bool):
        return None
    expected_origins = list(range(initial, len(target)))
    if origins != expected_origins:
        return None
    result: dict[str, tuple[np.ndarray, float | None, float]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            return None
        identifier = candidate.get("id")
        period = candidate.get("period")
        if not isinstance(identifier, str) or not identifier:
            return None
        if period is not None and not finite_number(period):
            return None
        predictions: list[float] = []
        for origin in expected_origins:
            _, predicted, _ = _future_fit(
                time[:origin],
                target[:origin],
                time[origin : origin + 1],
                None if period is None else float(period),
            )
            predictions.append(float(predicted[0]))
        array = np.asarray(predictions)
        score = _future_rmse(target[expected_origins], array)
        result[identifier] = (array, None if period is None else float(period), score)
    return result, expected_origins

def future_rolling_origin(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    evidence = _future_evidence(nodes)
    parsed = _future_candidate_predictions(evidence) if evidence is not None else None
    if evidence is None or parsed is None:
        return not_assessed("complete rolling-origin forecast evidence was not recorded")
    node, time, target, _, _ = evidence
    candidates, origins = parsed
    rolling = node["rolling_origin"]
    folds = rolling.get("folds")
    actual = rolling.get("actual")
    if not isinstance(folds, list) or len(folds) != len(origins):
        return failed("rolling-origin fold count is inconsistent")
    if not isinstance(actual, list) or not np.allclose(actual, target[origins], rtol=1e-9, atol=1e-9):
        return failed("rolling-origin actual values disagree with history")
    for fold, origin in zip(folds, origins):
        if not isinstance(fold, Mapping) or (
            fold.get("origin_index") != origin
            or not math.isclose(float(fold.get("max_train_time", math.nan)), float(time[origin - 1]))
            or not math.isclose(float(fold.get("validation_time", math.nan)), float(time[origin]))
            or float(fold.get("max_train_time", math.inf)) >= float(fold.get("validation_time", -math.inf))
        ):
            return failed("rolling-origin fold boundary is inconsistent or leaks future data")
    recorded_candidates = {item.get("id"): item for item in node["candidates"] if isinstance(item, Mapping)}
    for identifier, (predictions, _, score) in candidates.items():
        recorded = recorded_candidates[identifier]
        try:
            recorded_predictions = np.asarray(recorded["rolling_predictions"], dtype=float)
            recorded_score = float(recorded["rolling_rmse"])
        except (KeyError, TypeError, ValueError):
            return failed("candidate rolling evidence is malformed")
        if not np.allclose(recorded_predictions, predictions, rtol=1e-8, atol=1e-8):
            return failed(f"candidate {identifier} rolling predictions do not recompute")
        if not math.isclose(recorded_score, score, rel_tol=1e-8, abs_tol=1e-8):
            return failed(f"candidate {identifier} rolling RMSE does not recompute")
    return passed_check("all rolling-origin boundaries and candidate predictions were independently recomputed")

def future_index(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    evidence = _future_evidence(nodes)
    if evidence is None:
        return not_assessed("future forecast indices were not recorded")
    node, time, target, future_time, future_predictions = evidence
    if np.any(np.diff(time) <= 0.0) or np.any(np.diff(future_time) <= 0.0) or future_time[0] <= time[-1]:
        return failed("future indices are not strictly ordered after history")
    selected = node["selected_model"]
    if not isinstance(selected, Mapping) or not isinstance(selected.get("id"), str):
        return failed("selected future model is malformed")
    identifier = selected["id"]
    if identifier == "seasonal_naive":
        baseline = node["baseline"]
        season = baseline.get("season_length") if isinstance(baseline, Mapping) else None
        if not isinstance(season, int) or season <= 0 or season > len(target):
            return failed("seasonal future model has invalid season length")
        expected = _future_seasonal(target, season, len(future_time))
        expected_coefficients = np.asarray([])
        expected_rank = 0
    else:
        candidates = {
            item.get("id"): item
            for item in node["candidates"]
            if isinstance(item, Mapping)
        }
        candidate = candidates.get(identifier)
        if candidate is None:
            return failed("selected future model is not a recorded candidate")
        period = candidate.get("period")
        period = None if period is None else float(period)
        expected_coefficients, expected, expected_rank = _future_fit(
            time, target, future_time, period
        )
    try:
        coefficients = np.asarray(selected["coefficients"], dtype=float)
        rank = int(selected["design_rank"])
    except (KeyError, TypeError, ValueError):
        return failed("selected model coefficients are malformed")
    if not np.allclose(future_predictions, expected, rtol=1e-8, atol=1e-8):
        return failed("future predictions do not match the selected model refit")
    if not np.allclose(coefficients, expected_coefficients, rtol=1e-8, atol=1e-8) or rank != expected_rank:
        return failed("selected model coefficients or rank do not recompute")
    return passed_check("future indices and selected-model extrapolation were independently verified")

def future_errors(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    evidence = _future_evidence(nodes)
    parsed = _future_candidate_predictions(evidence) if evidence is not None else None
    if evidence is None or parsed is None:
        return not_assessed("future forecast error evidence was not recorded")
    node, _, target, _, _ = evidence
    candidates, origins = parsed
    actual = target[origins]
    baseline = node["baseline"]
    if not isinstance(baseline, Mapping):
        return failed("seasonal baseline evidence is malformed")
    season = baseline.get("season_length")
    if not isinstance(season, int) or season <= 0 or season > origins[0]:
        return failed("seasonal baseline length is invalid")
    baseline_predictions = np.asarray([target[origin - season] for origin in origins])
    baseline_rmse = _future_rmse(actual, baseline_predictions)
    try:
        recorded_baseline = np.asarray(baseline["rolling_predictions"], dtype=float)
        recorded_baseline_rmse = float(node["baseline_metrics"]["rmse"])
        observed = np.asarray(node["observed"], dtype=float)
        predicted = np.asarray(node["predicted"], dtype=float)
        residuals = np.asarray(node["residuals"], dtype=float)
        recorded_rmse = float(node["metrics"]["rmse"])
    except (KeyError, TypeError, ValueError):
        return failed("forecast error arrays are malformed")
    if not np.allclose(recorded_baseline, baseline_predictions, rtol=1e-9, atol=1e-9):
        return failed("seasonal baseline predictions do not recompute")
    if not math.isclose(recorded_baseline_rmse, baseline_rmse, rel_tol=1e-9, abs_tol=1e-9):
        return failed("seasonal baseline RMSE does not recompute")
    best_id, (best_predictions, _, best_score) = min(
        candidates.items(), key=lambda item: (item[1][2], item[0])
    )
    if baseline_rmse <= best_score:
        best_id, best_predictions, best_score = "seasonal_naive", baseline_predictions, baseline_rmse
    if node["selected_model"].get("id") != best_id:
        return failed("selected model is not the lowest rolling-origin RMSE candidate")
    expected_residuals = actual - best_predictions
    if (
        not np.allclose(observed, actual, rtol=1e-9, atol=1e-9)
        or not np.allclose(predicted, best_predictions, rtol=1e-9, atol=1e-9)
        or not np.allclose(residuals, expected_residuals, rtol=1e-9, atol=1e-9)
        or not math.isclose(recorded_rmse, best_score, rel_tol=1e-9, abs_tol=1e-9)
    ):
        return failed("selected forecast errors or RMSE do not independently recompute")
    return passed_check("selected and seasonal-baseline rolling errors were independently recomputed")

def future_interval(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    evidence = _future_evidence(nodes)
    if evidence is None:
        return not_assessed("future prediction interval evidence was not recorded")
    node, _, _, _, predictions = evidence
    interval = node["prediction_interval"]
    try:
        residuals = np.asarray(node["residuals"], dtype=float)
        level = float(interval["level"])
        lower = np.asarray(interval["lower"], dtype=float)
        upper = np.asarray(interval["upper"], dtype=float)
        scale = float(interval["residual_scale"])
    except (KeyError, TypeError, ValueError):
        return failed("future prediction interval evidence is malformed")
    if not 0.0 < level < 1.0 or lower.shape != predictions.shape or upper.shape != predictions.shape:
        return failed("future prediction interval level or dimensions are invalid")
    expected_scale = float(np.sqrt(np.sum(np.square(residuals)) / max(len(residuals) - 1, 1)))
    margin = NormalDist().inv_cdf(0.5 + level / 2.0) * expected_scale
    if not math.isclose(scale, expected_scale, rel_tol=1e-9, abs_tol=1e-9):
        return failed("future interval residual scale does not recompute")
    if not np.allclose(lower, predictions - margin, rtol=1e-8, atol=1e-8) or not np.allclose(
        upper, predictions + margin, rtol=1e-8, atol=1e-8
    ):
        return failed("future prediction interval bounds do not recompute")
    return passed_check("future prediction interval scale and bounds were independently recomputed")

def future_sensitivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    evidence = _future_evidence(nodes)
    if evidence is None:
        return not_assessed("future forecast sensitivity evidence was not recorded")
    node, time, target, future_time, predictions = evidence
    selected = node["selected_model"]
    sensitivity = node["sensitivity"]
    if not isinstance(selected, Mapping) or not isinstance(sensitivity, Mapping):
        return failed("future sensitivity evidence is malformed")
    if selected.get("id") == "seasonal_naive":
        season = node["baseline"].get("season_length")
        perturbed = target.copy()
        perturbed[-1] += max(abs(float(perturbed[-1])) * 0.01, 1e-6)
        reference_predictions = _future_seasonal(perturbed, season, len(future_time))
        reference_coefficients = np.asarray([])
        expected_method = "last_observation_perturbation"
    else:
        period = selected.get("period")
        period = None if period is None else float(period)
        reference_coefficients, reference_predictions, _ = _future_fit(
            time[:-1], target[:-1], future_time, period
        )
        expected_method = "drop_last_observation"
    expected_change = float(np.max(
        np.abs(reference_predictions - predictions) / np.maximum(np.abs(predictions), 1e-10)
    ))
    try:
        recorded_change = float(sensitivity["max_relative_change"])
        threshold = float(sensitivity["threshold"])
        recorded_coefficients = np.asarray(sensitivity["reference_coefficients"], dtype=float)
    except (KeyError, TypeError, ValueError):
        return failed("future sensitivity values are malformed")
    if sensitivity.get("method") != expected_method:
        return failed("future sensitivity method disagrees with selected model")
    if not np.allclose(recorded_coefficients, reference_coefficients, rtol=1e-8, atol=1e-8):
        return failed("future sensitivity reference fit does not recompute")
    if not math.isclose(recorded_change, expected_change, rel_tol=1e-8, abs_tol=1e-8):
        return failed("future sensitivity magnitude does not recompute")
    expected_passed = expected_change <= threshold
    if sensitivity.get("passed") is not expected_passed:
        return failed("future sensitivity pass status disagrees with threshold")
    if not expected_passed:
        return failed("future sensitivity change exceeds its declared threshold")
    return passed_check("future forecast parameter sensitivity was independently recomputed")

def temporal_leakage(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "leakage_check")
    if node is None:
        return not_assessed("temporal leakage evidence was not recorded")
    evidence = node["leakage_check"]
    if not isinstance(evidence, Mapping):
        return failed("temporal leakage evidence must be a mapping")
    train_max = evidence.get("max_train_time")
    holdout_min = evidence.get("min_holdout_time")
    if not finite_number(train_max) or not finite_number(holdout_min):
        return failed("temporal split boundaries are missing or non-finite")
    recomputed = train_max < holdout_min
    if evidence.get("passed") is not recomputed:
        return failed("reported leakage status disagrees with the split boundaries")
    if not recomputed:
        return failed("training time overlaps or follows the holdout period")
    return passed_check("training observations strictly precede the holdout period")

def prediction_interval(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "observed", "predicted", "prediction_interval")
    if node is None:
        return not_assessed("prediction interval evidence was not recorded")
    observed = node["observed"]
    predicted = node["predicted"]
    evidence = node["prediction_interval"]
    if not isinstance(observed, list) or not isinstance(predicted, list) or not isinstance(evidence, Mapping):
        return failed("prediction interval evidence has invalid types")
    lower = evidence.get("lower")
    upper = evidence.get("upper")
    level = evidence.get("level")
    recorded_coverage = evidence.get("empirical_coverage")
    if (
        not isinstance(lower, list)
        or not isinstance(upper, list)
        or not observed
        or len({len(observed), len(predicted), len(lower), len(upper)}) != 1
    ):
        return failed("prediction interval arrays have inconsistent lengths")
    values = [*observed, *predicted, *lower, *upper]
    if not all(finite_number(value) for value in values):
        return failed("prediction interval evidence contains non-finite values")
    if not finite_number(level) or not 0 < level < 1:
        return failed("prediction interval level must be between zero and one")
    if any(low > estimate or estimate > high for low, estimate, high in zip(lower, predicted, upper)):
        return failed("point predictions must lie inside their prediction intervals")
    recomputed_coverage = sum(
        low <= actual <= high
        for low, actual, high in zip(lower, observed, upper)
    ) / len(observed)
    if not finite_number(recorded_coverage) or not math.isclose(
        recorded_coverage,
        recomputed_coverage,
        rel_tol=1e-8,
        abs_tol=1e-8,
    ):
        return failed("recorded interval coverage does not match independent recomputation")
    return passed_check("prediction interval bounds and empirical coverage were independently recomputed")
