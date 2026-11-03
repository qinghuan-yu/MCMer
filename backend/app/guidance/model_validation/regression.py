"""model_validation: regression 领域实现与合同。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.guidance.model_validation.contracts import (
    ValidationCheck,
    failed,
    find_evidence,
    finite_number,
    not_assessed,
    passed_check,
)


def regression_baseline(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "metrics", "baseline_metrics")
    if node is None:
        return not_assessed("recommended and baseline metrics were not both recorded")
    try:
        recommended = node["metrics"]["rmse"]
        baseline = node["baseline_metrics"]["rmse"]
    except (KeyError, TypeError):
        return failed("baseline comparison requires numeric RMSE values")
    if not finite_number(recommended) or not finite_number(baseline):
        return failed("baseline comparison contains a non-finite RMSE")
    if recommended >= baseline:
        return failed("recommended RMSE does not improve on the baseline")
    return passed_check("recommended RMSE improves on the recorded baseline")

def residual_recompute(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "observed", "predicted", "residuals", "metrics")
    if node is None:
        return not_assessed("observations, predictions, residuals, and metrics were not all recorded")
    observed = node["observed"]
    predicted = node["predicted"]
    residuals = node["residuals"]
    if not all(isinstance(values, list) for values in (observed, predicted, residuals)):
        return failed("residual evidence must use numeric arrays")
    if not observed or len(observed) != len(predicted) or len(observed) != len(residuals):
        return failed("residual evidence arrays have inconsistent lengths")
    if not all(finite_number(value) for value in (observed + predicted + residuals)):
        return failed("residual evidence contains non-finite values")

    recomputed = [actual - estimate for actual, estimate in zip(observed, predicted)]
    if any(
        not math.isclose(actual, recorded, rel_tol=1e-8, abs_tol=1e-8)
        for actual, recorded in zip(recomputed, residuals)
    ):
        return failed("recorded residuals do not equal observed minus predicted")
    recomputed_rmse = math.sqrt(sum(value * value for value in recomputed) / len(recomputed))
    try:
        recorded_rmse = node["metrics"]["rmse"]
    except (KeyError, TypeError):
        return failed("recorded metrics do not contain RMSE")
    if not finite_number(recorded_rmse) or not math.isclose(
        recomputed_rmse, recorded_rmse, rel_tol=1e-8, abs_tol=1e-8
    ):
        return failed("recorded RMSE does not match independently recomputed RMSE")
    return passed_check("residuals and RMSE were independently recomputed")

def cross_validation(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "cross_validation")
    if node is None:
        return not_assessed("cross-validation evidence was not recorded")
    evidence = node["cross_validation"]
    if not isinstance(evidence, Mapping):
        return failed("cross-validation evidence must be a mapping")
    values = evidence.get("fold_rmse")
    threshold = evidence.get("max_allowed_rmse")
    if (
        not isinstance(values, list)
        or not values
        or not all(finite_number(value) for value in values)
        or not finite_number(threshold)
    ):
        return failed("cross-validation evidence is incomplete or non-finite")
    if max(values) > threshold:
        return failed("at least one validation fold exceeds the RMSE threshold")
    return passed_check("all validation folds satisfy the RMSE threshold")
