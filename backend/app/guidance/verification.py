"""Independent verification strategies for typed solver evidence."""

from __future__ import annotations

import math
from typing import Protocol

from app.guidance.artifacts import (
    ForecastEvidence,
    OptimizationEvidence,
    RegressionEvidence,
    ResultRegistry,
    SolverEvidence,
    SolverResults,
)


class EvidenceVerifier(Protocol):
    def verify(self, evidence: SolverEvidence, registered_metrics: dict[str, float]) -> dict: ...


class RegressionEvidenceVerifier:
    def verify(self, evidence: RegressionEvidence, registered_metrics: dict[str, float]) -> dict:
        issues: list[str] = []
        same_length = len(evidence.observed) == len(evidence.predicted) == len(evidence.residuals)
        expected = [actual - predicted for actual, predicted in zip(evidence.observed, evidence.predicted)]
        residual_difference = max(
            (abs(calculated - recorded) for calculated, recorded in zip(expected, evidence.residuals)),
            default=math.inf,
        )
        equations_match = same_length and residual_difference <= 1e-8
        if not equations_match:
            issues.append(f"{evidence.result_id}: residual evidence is inconsistent")

        metrics = {
            "rmse": math.sqrt(sum(value * value for value in expected) / len(expected)),
            "max_abs_residual": max(abs(value) for value in expected),
        }
        metrics_match = _metrics_match(metrics, registered_metrics)
        if not metrics_match:
            issues.append(f"{evidence.result_id}: registered regression metrics do not match")
        constraints_hold = min(evidence.constraint_values) >= -1e-8
        if not constraints_hold:
            issues.append(f"{evidence.result_id}: recorded nonnegative constraints fail")
        stability_passed = (
            evidence.parameter_stability.max_relative_change <= evidence.parameter_stability.threshold
        )
        if not stability_passed:
            issues.append(f"{evidence.result_id}: parameter stability exceeds threshold")
        return {
            "equation_check": {"status": "passed" if equations_match else "failed", "result_id": evidence.result_id},
            "constraint_check": {"status": "passed" if constraints_hold else "failed", "result_id": evidence.result_id},
            "metric_check": {
                "status": "passed" if metrics_match else "failed",
                "result_id": evidence.result_id,
                "registered_metrics": registered_metrics,
                "recomputed_metrics": metrics,
            },
            "sensitivity_check": {
                "status": "passed" if stability_passed else "failed",
                "result_id": evidence.result_id,
                **evidence.parameter_stability.model_dump(),
            },
            "issues": issues,
        }


class OptimizationEvidenceVerifier:
    def verify(self, evidence: OptimizationEvidence, registered_metrics: dict[str, float]) -> dict:
        issues: list[str] = []
        constraint_failures = [
            item.name
            for item in evidence.constraints
            if (item.lower_bound is not None and item.value < item.lower_bound - 1e-8)
            or (item.upper_bound is not None and item.value > item.upper_bound + 1e-8)
        ]
        constraints_hold = not constraint_failures
        if constraint_failures:
            issues.append(
                f"{evidence.result_id}: optimization constraints fail: {', '.join(constraint_failures)}"
            )
        objective_metrics = {"objective_value": evidence.objective_value}
        metrics_match = _metrics_match(objective_metrics, registered_metrics)
        if not metrics_match:
            issues.append(f"{evidence.result_id}: registered objective does not match solver evidence")
        improves_baseline = evidence.objective_value <= evidence.baseline_objective + 1e-8
        if not improves_baseline:
            issues.append(f"{evidence.result_id}: objective is worse than baseline")
        sensitivity_passed = evidence.sensitivity.max_relative_change <= evidence.sensitivity.threshold
        if not sensitivity_passed:
            issues.append(f"{evidence.result_id}: optimization sensitivity exceeds threshold")
        return {
            "equation_check": {
                "status": "passed" if improves_baseline else "failed",
                "result_id": evidence.result_id,
                "objective_value": evidence.objective_value,
                "baseline_objective": evidence.baseline_objective,
            },
            "constraint_check": {
                "status": "passed" if constraints_hold else "failed",
                "result_id": evidence.result_id,
                "failed_constraints": constraint_failures,
            },
            "metric_check": {
                "status": "passed" if metrics_match else "failed",
                "result_id": evidence.result_id,
                "registered_metrics": registered_metrics,
                "recomputed_metrics": objective_metrics,
            },
            "sensitivity_check": {
                "status": "passed" if sensitivity_passed else "failed",
                "result_id": evidence.result_id,
                **evidence.sensitivity.model_dump(),
            },
            "issues": issues,
        }


class ForecastEvidenceVerifier:
    def verify(self, evidence: ForecastEvidence, registered_metrics: dict[str, float]) -> dict:
        issues: list[str] = []
        train_same_length = len(evidence.train_observed) == len(evidence.train_predicted)
        holdout_same_length = len(evidence.holdout_observed) == len(evidence.holdout_predicted)
        forecast_same_length = len(evidence.forecast_periods) == len(evidence.forecast_values)
        values_are_finite = all(
            math.isfinite(value)
            for value in (
                evidence.train_observed
                + evidence.train_predicted
                + evidence.holdout_observed
                + evidence.holdout_predicted
                + evidence.forecast_periods
                + evidence.forecast_values
            )
        )
        equation_inputs_valid = (
            train_same_length and holdout_same_length and forecast_same_length and values_are_finite
        )
        if not equation_inputs_valid:
            issues.append(f"{evidence.result_id}: forecast evidence dimensions or values are invalid")

        train_errors = [
            observed - predicted
            for observed, predicted in zip(evidence.train_observed, evidence.train_predicted)
        ]
        holdout_errors = [
            observed - predicted
            for observed, predicted in zip(evidence.holdout_observed, evidence.holdout_predicted)
        ]
        train_rmse = _rmse(train_errors)
        holdout_rmse = _rmse(holdout_errors)
        metrics = {
            "forecast_value": evidence.forecast_values[0],
            "holdout_rmse": holdout_rmse,
            "train_rmse": train_rmse,
        }
        metrics_match = equation_inputs_valid and _metrics_match(metrics, registered_metrics)
        if not metrics_match:
            issues.append(f"{evidence.result_id}: registered forecast metrics do not match")

        max_holdout_error = max((abs(error) for error in holdout_errors), default=math.inf)
        holdout_passed = max_holdout_error <= evidence.holdout_tolerance
        forecasts_nonnegative = all(value >= -1e-8 for value in evidence.forecast_values)
        constraints_hold = holdout_passed and forecasts_nonnegative
        if not holdout_passed:
            issues.append(f"{evidence.result_id}: holdout error exceeds tolerance")
        if not forecasts_nonnegative:
            issues.append(f"{evidence.result_id}: forecast values violate nonnegative demand")

        stability_passed = evidence.stability.max_relative_change <= evidence.stability.threshold
        if not stability_passed:
            issues.append(f"{evidence.result_id}: forecast stability exceeds threshold")

        return {
            "equation_check": {
                "status": "passed" if equation_inputs_valid else "failed",
                "result_id": evidence.result_id,
            },
            "constraint_check": {
                "status": "passed" if constraints_hold else "failed",
                "result_id": evidence.result_id,
                "max_holdout_error": max_holdout_error,
                "holdout_tolerance": evidence.holdout_tolerance,
            },
            "metric_check": {
                "status": "passed" if metrics_match else "failed",
                "result_id": evidence.result_id,
                "registered_metrics": registered_metrics,
                "recomputed_metrics": metrics,
            },
            "sensitivity_check": {
                "status": "passed" if stability_passed else "failed",
                "result_id": evidence.result_id,
                **evidence.stability.model_dump(),
            },
            "issues": issues,
        }


_VERIFIERS: dict[str, EvidenceVerifier] = {
    "regression": RegressionEvidenceVerifier(),
    "optimization": OptimizationEvidenceVerifier(),
    "forecast": ForecastEvidenceVerifier(),
}


def verify_solver_results(solver_results: SolverResults, registry: ResultRegistry) -> dict:
    registered = {item.id: item for item in registry.verified_results}
    evidence_ids = {item.result_id for item in solver_results.evidence}
    issues: list[str] = []
    if evidence_ids != set(registered):
        issues.append(
            f"solver/result registry ID mismatch: evidence={sorted(evidence_ids)}; registered={sorted(registered)}"
        )

    outcomes = []
    for evidence in solver_results.evidence:
        result = registered.get(evidence.result_id)
        metrics = result.metrics if result is not None else {}
        outcome = _VERIFIERS[evidence.kind].verify(evidence, metrics)
        outcomes.append(outcome)
        issues.extend(outcome["issues"])
    return {
        "equation_checks": [item["equation_check"] for item in outcomes],
        "constraint_checks": [item["constraint_check"] for item in outcomes],
        "residual_checks": [item["metric_check"] for item in outcomes],
        "sensitivity_checks": [item["sensitivity_check"] for item in outcomes],
        "issues": issues,
    }


def _metrics_match(expected: dict[str, float], registered: dict[str, float]) -> bool:
    return all(
        name in registered and math.isclose(value, registered[name], rel_tol=1e-8, abs_tol=1e-8)
        for name, value in expected.items()
    )


def _rmse(errors: list[float]) -> float:
    if not errors:
        return math.inf
    return math.sqrt(sum(value * value for value in errors) / len(errors))
