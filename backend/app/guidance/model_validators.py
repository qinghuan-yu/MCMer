from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


ValidationStatus = Literal["passed", "failed", "not_assessed"]


class ValidationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    reason: str


class ModelValidationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    checks: dict[str, ValidationCheck] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


Validator = Callable[[list[Mapping[str, Any]]], ValidationCheck]


class ModelValidatorRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, dict[str, Validator]] = {}

    def register(self, family: str, name: str, validator: Validator) -> None:
        family_validators = self._validators.setdefault(family, {})
        if name in family_validators:
            raise ValueError(f"validator already registered: {family}.{name}")
        family_validators[name] = validator

    def assess(
        self,
        *,
        family: str,
        candidate_roles: set[str],
        evidence: Iterable[Mapping[str, Any]],
    ) -> ModelValidationAssessment:
        validators = self._validators.get(family)
        if validators is None:
            return ModelValidationAssessment(
                status="not_assessed",
                reasons=[f"no validators registered for model family: {family}"],
            )

        evidence_nodes = list(_walk_mappings(evidence))
        checks = {
            name: validator(evidence_nodes)
            for name, validator in validators.items()
        }
        reasons: list[str] = []
        required_roles = {"baseline", "recommended"}
        missing_roles = sorted(required_roles - candidate_roles)
        if missing_roles:
            reasons.append(
                "candidate comparison is incomplete; missing roles: "
                + ", ".join(missing_roles)
            )

        failed_checks = [
            name for name, check in checks.items() if check.status == "failed"
        ]
        unassessed_checks = [
            name for name, check in checks.items() if check.status == "not_assessed"
        ]
        if failed_checks:
            reasons.append("failed checks: " + ", ".join(failed_checks))
        if unassessed_checks:
            reasons.append("unassessed checks: " + ", ".join(unassessed_checks))

        if missing_roles or failed_checks:
            status: ValidationStatus = "failed"
        elif unassessed_checks:
            status = "not_assessed"
        else:
            status = "passed"
        return ModelValidationAssessment(status=status, checks=checks, reasons=reasons)


def build_default_model_validator_registry() -> ModelValidatorRegistry:
    registry = ModelValidatorRegistry()
    for family in ("regression", "forecast"):
        registry.register(family, "baseline_comparison", _regression_baseline)
        registry.register(family, "residual_recompute", _residual_recompute)
        registry.register(family, "cross_validation", _cross_validation)
        registry.register(family, "sensitivity", _sensitivity)
        registry.register(family, "extreme_inputs", _extreme_inputs)
    registry.register("forecast", "temporal_leakage", _temporal_leakage)
    registry.register("forecast", "prediction_interval", _prediction_interval)

    registry.register("optimization", "baseline_comparison", _optimization_baseline)
    registry.register("optimization", "sensitivity", _sensitivity)
    registry.register("optimization", "extreme_inputs", _extreme_inputs)
    registry.register("optimization", "multistart", _multistart)
    registry.register("optimization", "feasibility_certificate", _optimization_feasibility)
    registry.register("optimization", "bound_certificate", _optimization_bounds)
    registry.register("evaluation", "normalization_recompute", _evaluation_normalization)
    registry.register("evaluation", "weight_recompute", _evaluation_weights)
    registry.register("evaluation", "ranking_recompute", _evaluation_ranking)
    registry.register("evaluation", "weight_sensitivity", _evaluation_sensitivity)
    registry.register("evaluation", "extreme_inputs", _extreme_inputs)
    registry.register("differential_equation", "baseline_comparison", _regression_baseline)
    registry.register("differential_equation", "parameter_recompute", _dynamics_parameters)
    registry.register("differential_equation", "integration_recompute", _dynamics_integration)
    registry.register("differential_equation", "boundary_check", _dynamics_boundary)
    registry.register("differential_equation", "conservation_check", _dynamics_conservation)
    registry.register("differential_equation", "step_sensitivity", _dynamics_step_sensitivity)
    registry.register("differential_equation", "extreme_inputs", _extreme_inputs)
    registry.register("graph", "baseline_comparison", _graph_baseline)
    registry.register("graph", "connectivity_certificate", _graph_connectivity)
    registry.register("graph", "shortest_path_certificate", _graph_shortest_path)
    registry.register("graph", "flow_certificate", _graph_flow)
    registry.register("graph", "min_cut_certificate", _graph_min_cut)
    registry.register("graph", "extreme_inputs", _extreme_inputs)
    return registry


def _walk_mappings(values: Iterable[Any]) -> Iterable[Mapping[str, Any]]:
    for value in values:
        if isinstance(value, Mapping):
            yield value
            yield from _walk_mappings(value.values())
        elif isinstance(value, list):
            yield from _walk_mappings(value)


def _find(nodes: list[Mapping[str, Any]], *keys: str) -> Mapping[str, Any] | None:
    required = set(keys)
    return next((node for node in nodes if required.issubset(node)), None)


def _passed(reason: str) -> ValidationCheck:
    return ValidationCheck(status="passed", reason=reason)


def _failed(reason: str) -> ValidationCheck:
    return ValidationCheck(status="failed", reason=reason)


def _not_assessed(reason: str) -> ValidationCheck:
    return ValidationCheck(status="not_assessed", reason=reason)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _regression_baseline(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "metrics", "baseline_metrics")
    if node is None:
        return _not_assessed("recommended and baseline metrics were not both recorded")
    try:
        recommended = node["metrics"]["rmse"]
        baseline = node["baseline_metrics"]["rmse"]
    except (KeyError, TypeError):
        return _failed("baseline comparison requires numeric RMSE values")
    if not _finite_number(recommended) or not _finite_number(baseline):
        return _failed("baseline comparison contains a non-finite RMSE")
    if recommended >= baseline:
        return _failed("recommended RMSE does not improve on the baseline")
    return _passed("recommended RMSE improves on the recorded baseline")


def _optimization_baseline(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "objective_value", "baseline_objective")
    if node is None:
        return _not_assessed("recommended and baseline objectives were not both recorded")
    recommended = node["objective_value"]
    baseline = node["baseline_objective"]
    if not _finite_number(recommended) or not _finite_number(baseline):
        return _failed("baseline comparison contains a non-finite objective")
    if recommended > baseline:
        return _failed("recommended objective is worse than the minimization baseline")
    return _passed("recommended objective is no worse than the minimization baseline")


def _residual_recompute(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "observed", "predicted", "residuals", "metrics")
    if node is None:
        return _not_assessed("observations, predictions, residuals, and metrics were not all recorded")
    observed = node["observed"]
    predicted = node["predicted"]
    residuals = node["residuals"]
    if not all(isinstance(values, list) for values in (observed, predicted, residuals)):
        return _failed("residual evidence must use numeric arrays")
    if not observed or len(observed) != len(predicted) or len(observed) != len(residuals):
        return _failed("residual evidence arrays have inconsistent lengths")
    if not all(_finite_number(value) for value in (observed + predicted + residuals)):
        return _failed("residual evidence contains non-finite values")

    recomputed = [actual - estimate for actual, estimate in zip(observed, predicted)]
    if any(
        not math.isclose(actual, recorded, rel_tol=1e-8, abs_tol=1e-8)
        for actual, recorded in zip(recomputed, residuals)
    ):
        return _failed("recorded residuals do not equal observed minus predicted")
    recomputed_rmse = math.sqrt(sum(value * value for value in recomputed) / len(recomputed))
    try:
        recorded_rmse = node["metrics"]["rmse"]
    except (KeyError, TypeError):
        return _failed("recorded metrics do not contain RMSE")
    if not _finite_number(recorded_rmse) or not math.isclose(
        recomputed_rmse, recorded_rmse, rel_tol=1e-8, abs_tol=1e-8
    ):
        return _failed("recorded RMSE does not match independently recomputed RMSE")
    return _passed("residuals and RMSE were independently recomputed")


def _cross_validation(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "cross_validation")
    if node is None:
        return _not_assessed("cross-validation evidence was not recorded")
    evidence = node["cross_validation"]
    if not isinstance(evidence, Mapping):
        return _failed("cross-validation evidence must be a mapping")
    values = evidence.get("fold_rmse")
    threshold = evidence.get("max_allowed_rmse")
    if (
        not isinstance(values, list)
        or not values
        or not all(_finite_number(value) for value in values)
        or not _finite_number(threshold)
    ):
        return _failed("cross-validation evidence is incomplete or non-finite")
    if max(values) > threshold:
        return _failed("at least one validation fold exceeds the RMSE threshold")
    return _passed("all validation folds satisfy the RMSE threshold")


def _sensitivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "sensitivity")
    if node is None:
        return _not_assessed("sensitivity evidence was not recorded")
    evidence = node["sensitivity"]
    if not isinstance(evidence, Mapping):
        return _failed("sensitivity evidence must be a mapping")
    change = evidence.get("max_relative_change")
    threshold = evidence.get("threshold")
    if not _finite_number(change) or not _finite_number(threshold):
        return _failed("sensitivity evidence is incomplete or non-finite")
    if change > threshold:
        return _failed("sensitivity change exceeds its declared threshold")
    return _passed("sensitivity change remains within its declared threshold")


def _extreme_inputs(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "extreme_inputs")
    if node is None:
        return _not_assessed("extreme-input evidence was not recorded")
    evidence = node["extreme_inputs"]
    if not isinstance(evidence, Mapping):
        return _failed("extreme-input evidence must be a mapping")
    if evidence.get("finite") is not True:
        return _failed("extreme-input execution produced non-finite output")
    if evidence.get("constraints_satisfied") is not True:
        return _failed("extreme-input execution violates model constraints")
    return _passed("extreme-input outputs remain finite and feasible")


def _multistart(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "multistart")
    if node is None:
        return _not_assessed("multi-start evidence was not recorded")
    evidence = node["multistart"]
    if not isinstance(evidence, Mapping):
        return _failed("multi-start evidence must be a mapping")
    objectives = evidence.get("objective_values")
    tolerance = evidence.get("tolerance")
    if (
        not isinstance(objectives, list)
        or len(objectives) < 2
        or not all(_finite_number(value) for value in objectives)
        or not _finite_number(tolerance)
        or tolerance < 0
    ):
        return _failed("multi-start evidence is incomplete or non-finite")
    if max(objectives) - min(objectives) > tolerance:
        return _failed("multi-start objectives do not converge within tolerance")
    return _passed("multi-start objectives converge within tolerance")


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


def _optimization_feasibility(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "x", "objective_value", "problem", "feasibility_certificate")
    if node is None:
        return _not_assessed("optimization feasibility evidence was not recorded")
    parsed = _optimization_problem(node)
    certificate = node["feasibility_certificate"]
    if parsed is None or not isinstance(certificate, Mapping):
        return _failed("optimization problem or feasibility certificate is malformed")
    quadratic, linear, constant, value, problem, tolerance = parsed
    recomputed_objective = float(0.5 * value @ quadratic @ value + linear @ value + constant)
    recorded_objective = node["objective_value"]
    if not _finite_number(recorded_objective) or not math.isclose(
        recorded_objective,
        recomputed_objective,
        rel_tol=1e-8,
        abs_tol=1e-8,
    ):
        return _failed("recorded objective does not match independent recomputation")

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
        return _failed("optimization bounds or linear constraints are malformed")
    if lower.shape != (dimension,) or upper.shape != (dimension,) or len(a_ub) != len(b_ub) or len(a_eq) != len(b_eq):
        return _failed("optimization constraint dimensions do not match the solution")

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
            return _failed("quadratic constraint evidence is malformed")
        if matrix.shape != (dimension, dimension) or vector.shape != (dimension,):
            return _failed("quadratic constraint dimensions do not match the solution")
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
        if not _finite_number(recorded) or not math.isclose(recorded, expected, rel_tol=1e-8, abs_tol=1e-8):
            return _failed(f"feasibility certificate mismatch: {name}")
    satisfied = recomputed_max <= tolerance
    if certificate.get("constraints_satisfied") is not satisfied:
        return _failed("feasibility status disagrees with independently recomputed violations")
    if not satisfied:
        return _failed("recommended solution violates at least one constraint")
    return _passed("objective and all constraint violations were independently recomputed")


def _optimization_bounds(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "x", "objective_value", "problem", "bound_certificate")
    if node is None:
        return _not_assessed("optimization bound evidence was not recorded")
    parsed = _optimization_problem(node)
    certificate = node["bound_certificate"]
    if parsed is None or not isinstance(certificate, Mapping):
        return _failed("optimization problem or bound certificate is malformed")
    quadratic, linear, constant, _, _, tolerance = parsed
    eigenvalues = np.linalg.eigvalsh(quadratic)
    if float(np.min(eigenvalues)) <= 0.0:
        return _failed("a global lower bound requires a positive-definite objective")
    unconstrained = np.linalg.solve(quadratic, -linear)
    lower_bound = float(0.5 * unconstrained @ quadratic @ unconstrained + linear @ unconstrained + constant)
    upper_bound = node["objective_value"]
    if not _finite_number(upper_bound):
        return _failed("optimization upper bound is non-finite")
    expected = {
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "absolute_gap": upper_bound - lower_bound,
    }
    for name, value in expected.items():
        recorded = certificate.get(name)
        if not _finite_number(recorded) or not math.isclose(recorded, value, rel_tol=1e-8, abs_tol=1e-8):
            return _failed(f"bound certificate mismatch: {name}")
    valid = lower_bound <= upper_bound + tolerance
    if certificate.get("valid") is not valid or not valid:
        return _failed("optimization lower and upper bounds are inconsistent")
    return _passed("global objective lower bound and feasible upper bound were independently recomputed")


def _evaluation_evidence(
    nodes: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], np.ndarray, list[str], np.ndarray, np.ndarray, np.ndarray, list[int]] | None:
    node = _find(
        nodes,
        "decision_matrix",
        "criterion_types",
        "normalized_matrix",
        "weight_method",
        "weights",
        "scores",
        "ranking",
    )
    if node is None:
        return None
    try:
        matrix = np.asarray(node["decision_matrix"], dtype=float)
        criterion_types = list(node["criterion_types"])
        normalized = np.asarray(node["normalized_matrix"], dtype=float)
        weights = np.asarray(node["weights"], dtype=float).reshape(-1)
        scores = np.asarray(node["scores"], dtype=float).reshape(-1)
        ranking = list(node["ranking"])
    except (TypeError, ValueError):
        return None
    if (
        matrix.ndim != 2
        or matrix.shape[0] < 2
        or matrix.shape[1] < 1
        or normalized.shape != matrix.shape
        or weights.shape != (matrix.shape[1],)
        or scores.shape != (matrix.shape[0],)
        or len(criterion_types) != matrix.shape[1]
        or any(item not in {"benefit", "cost"} for item in criterion_types)
        or sorted(ranking) != list(range(matrix.shape[0]))
        or not np.all(np.isfinite(matrix))
        or not np.all(np.isfinite(normalized))
        or not np.all(np.isfinite(weights))
        or not np.all(np.isfinite(scores))
    ):
        return None
    return node, matrix, criterion_types, normalized, weights, scores, ranking


def _evaluation_normalized(matrix: np.ndarray, criterion_types: list[str]) -> np.ndarray | None:
    minima = np.min(matrix, axis=0)
    maxima = np.max(matrix, axis=0)
    spans = maxima - minima
    if np.any(spans <= 0.0):
        return None
    result = (matrix - minima) / spans
    for column, criterion_type in enumerate(criterion_types):
        if criterion_type == "cost":
            result[:, column] = 1.0 - result[:, column]
    return result


def _evaluation_normalization(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _evaluation_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete decision matrix and normalization evidence were not recorded")
    _, matrix, criterion_types, normalized, _, _, _ = parsed
    expected = _evaluation_normalized(matrix, criterion_types)
    if expected is None:
        return _failed("at least one criterion does not discriminate between alternatives")
    if not np.allclose(normalized, expected, rtol=1e-9, atol=1e-9):
        return _failed("recorded benefit/cost normalization disagrees with independent recomputation")
    return _passed("benefit and cost criteria were independently normalized")


def _evaluation_weights(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _evaluation_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete decision weight evidence was not recorded")
    node, matrix, criterion_types, _, weights, _, _ = parsed
    if np.any(weights < 0.0) or not np.isclose(np.sum(weights), 1.0, rtol=1e-9, atol=1e-9):
        return _failed("decision weights must be non-negative and sum to one")
    method = node["weight_method"]
    if method == "provided":
        return _passed("provided weights are finite, non-negative, and normalized")
    if method != "entropy":
        return _failed("weight method must be provided or entropy")
    normalized = _evaluation_normalized(matrix, criterion_types)
    if normalized is None:
        return _failed("entropy weights require discriminating criteria")
    probabilities = normalized / np.sum(normalized, axis=0)
    positive = probabilities > 0.0
    entropy = -np.sum(
        np.where(positive, probabilities * np.log(np.where(positive, probabilities, 1.0)), 0.0),
        axis=0,
    ) / np.log(matrix.shape[0])
    expected = 1.0 - entropy
    if float(np.sum(expected)) <= 0.0:
        return _failed("entropy weighting produced no discriminatory information")
    expected /= np.sum(expected)
    if not np.allclose(weights, expected, rtol=1e-9, atol=1e-9):
        return _failed("recorded entropy weights disagree with independent recomputation")
    return _passed("entropy weights were independently recomputed")


def _rank_descending(scores: np.ndarray) -> list[int]:
    return np.lexsort((np.arange(len(scores)), -scores)).astype(int).tolist()


def _evaluation_ranking(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _evaluation_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete decision ranking evidence was not recorded")
    _, matrix, criterion_types, _, weights, scores, ranking = parsed
    normalized = _evaluation_normalized(matrix, criterion_types)
    if normalized is None:
        return _failed("ranking cannot be recomputed from non-discriminating criteria")
    expected_scores = normalized @ weights
    if not np.allclose(scores, expected_scores, rtol=1e-9, atol=1e-9):
        return _failed("recorded decision scores disagree with independent recomputation")
    if ranking != _rank_descending(expected_scores):
        return _failed("recorded ranking disagrees with independently recomputed scores")
    return _passed("weighted scores and deterministic ranking were independently recomputed")


def _evaluation_sensitivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _evaluation_evidence(nodes)
    node = _find(nodes, "weight_perturbation")
    if parsed is None or node is None or not isinstance(node["weight_perturbation"], Mapping):
        return _not_assessed("weight perturbation evidence was not recorded")
    _, matrix, criterion_types, _, weights, _, ranking = parsed
    evidence = node["weight_perturbation"]
    fraction = evidence.get("fraction")
    threshold = evidence.get("threshold")
    runs = evidence.get("runs")
    if (
        not _finite_number(fraction)
        or not 0.0 < fraction < 1.0
        or not _finite_number(threshold)
        or not 0.0 <= threshold <= 1.0
        or not isinstance(runs, list)
        or len(runs) != 2 * len(weights)
    ):
        return _failed("weight perturbation design is incomplete or malformed")
    normalized = _evaluation_normalized(matrix, criterion_types)
    if normalized is None:
        return _failed("weight sensitivity requires discriminating criteria")
    base_positions = np.empty(len(ranking), dtype=float)
    base_positions[ranking] = np.arange(len(ranking), dtype=float)
    correlations: list[float] = []
    expected_index = 0
    for criterion in range(len(weights)):
        for direction in (-1.0, 1.0):
            run = runs[expected_index]
            expected_index += 1
            if not isinstance(run, Mapping):
                return _failed("weight perturbation run must be a mapping")
            expected_weights = weights.copy()
            expected_weights[criterion] *= 1.0 + direction * fraction
            expected_weights /= np.sum(expected_weights)
            try:
                recorded_weights = np.asarray(run["weights"], dtype=float).reshape(-1)
                recorded_ranking = list(run["ranking"])
            except (KeyError, TypeError, ValueError):
                return _failed("weight perturbation run is malformed")
            expected_ranking = _rank_descending(normalized @ expected_weights)
            if not np.allclose(recorded_weights, expected_weights, rtol=1e-9, atol=1e-9):
                return _failed("weight perturbation weights disagree with the declared fraction")
            if recorded_ranking != expected_ranking:
                return _failed("perturbed ranking disagrees with independent recomputation")
            positions = np.empty(len(ranking), dtype=float)
            positions[recorded_ranking] = np.arange(len(ranking), dtype=float)
            correlations.append(float(np.corrcoef(base_positions, positions)[0, 1]))
    retention = sum(run["ranking"][0] == ranking[0] for run in runs) / len(runs)
    minimum_correlation = min(correlations)
    stable = retention >= threshold and minimum_correlation >= threshold
    recorded_values = {
        "top_rank_retention_rate": retention,
        "min_rank_correlation": minimum_correlation,
    }
    for name, expected in recorded_values.items():
        recorded = evidence.get(name)
        if not _finite_number(recorded) or not math.isclose(recorded, expected, rel_tol=1e-9, abs_tol=1e-9):
            return _failed(f"weight sensitivity metric mismatch: {name}")
    if evidence.get("stable") is not stable:
        return _failed("reported ranking stability disagrees with recomputed perturbations")
    if not stable:
        return _failed("ranking is not stable under the declared weight perturbations")
    return _passed("all one-at-a-time weight perturbations and rankings were independently recomputed")


def _dynamics_evidence(
    nodes: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, bool, int] | None:
    node = _find(
        nodes,
        "time",
        "observed_states",
        "parameter_estimate",
        "identifiability",
        "simulated_states",
        "integration",
    )
    if node is None:
        return None
    try:
        time = np.asarray(node["time"], dtype=float).reshape(-1)
        observed = np.asarray(node["observed_states"], dtype=float)
        simulated = np.asarray(node["simulated_states"], dtype=float)
        estimate = node["parameter_estimate"]
        dynamics_matrix = np.asarray(estimate["matrix"], dtype=float)
        offset = np.asarray(estimate["offset"], dtype=float).reshape(-1)
        include_offset = estimate["include_offset"]
        integration = node["integration"]
        substeps = integration["substeps"]
    except (KeyError, TypeError, ValueError):
        return None
    if (
        observed.ndim != 2
        or len(time) < 3
        or observed.shape[0] != len(time)
        or simulated.shape != observed.shape
        or dynamics_matrix.shape != (observed.shape[1], observed.shape[1])
        or offset.shape != (observed.shape[1],)
        or not isinstance(include_offset, bool)
        or integration.get("method") != "rk4"
        or not isinstance(substeps, int)
        or isinstance(substeps, bool)
        or substeps < 1
        or np.any(np.diff(time) <= 0.0)
        or not all(np.all(np.isfinite(value)) for value in (
            time,
            observed,
            simulated,
            dynamics_matrix,
            offset,
        ))
    ):
        return None
    return node, time, observed, simulated, dynamics_matrix, offset, include_offset, substeps


def _dynamics_design(
    time: np.ndarray,
    observed: np.ndarray,
    include_offset: bool,
) -> tuple[np.ndarray, np.ndarray]:
    intervals = np.diff(time)
    midpoint_states = 0.5 * (observed[:-1] + observed[1:])
    design = (
        np.column_stack([midpoint_states, np.ones(len(midpoint_states))])
        if include_offset
        else midpoint_states
    )
    derivatives = np.diff(observed, axis=0) / intervals[:, None]
    return design, derivatives


def _dynamics_parameters(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete ODE parameter evidence was not recorded")
    node, time, observed, _, dynamics_matrix, offset, include_offset, _ = parsed
    design, derivatives = _dynamics_design(time, observed, include_offset)
    coefficients, _, rank, _ = np.linalg.lstsq(design, derivatives, rcond=None)
    expected_matrix = coefficients[:-1].T if include_offset else coefficients.T
    expected_offset = coefficients[-1] if include_offset else np.zeros(observed.shape[1])
    identifiability = node["identifiability"]
    if not isinstance(identifiability, Mapping):
        return _failed("ODE identifiability evidence must be a mapping")
    expected_identifiable = rank == design.shape[1]
    condition_number = float(np.linalg.cond(design))
    recorded_condition = identifiability.get("condition_number")
    max_condition_number = identifiability.get("max_condition_number")
    if (
        identifiability.get("design_rank") != int(rank)
        or identifiability.get("parameter_count_per_state") != int(design.shape[1])
        or identifiability.get("identifiable") is not bool(expected_identifiable)
    ):
        return _failed("ODE design rank disagrees with independent recomputation")
    if not expected_identifiable:
        return _failed("ODE parameter design is rank deficient")
    if (
        not _finite_number(recorded_condition)
        or not math.isclose(recorded_condition, condition_number, rel_tol=1e-9, abs_tol=1e-9)
    ):
        return _failed("ODE condition number disagrees with independent recomputation")
    if (
        not _finite_number(max_condition_number)
        or max_condition_number <= 1.0
        or condition_number > max_condition_number
    ):
        return _failed("ODE parameter design exceeds the declared condition-number limit")
    if not np.allclose(dynamics_matrix, expected_matrix, rtol=1e-9, atol=1e-9):
        return _failed("ODE dynamics matrix disagrees with independent parameter estimation")
    if not np.allclose(offset, expected_offset, rtol=1e-9, atol=1e-9):
        return _failed("ODE offset disagrees with independent parameter estimation")
    return _passed("ODE parameters and design rank were independently recomputed")


def _dynamics_rk4(
    time: np.ndarray,
    initial: np.ndarray,
    dynamics_matrix: np.ndarray,
    offset: np.ndarray,
    substeps: int,
) -> np.ndarray:
    result = [initial.copy()]
    for start, stop in zip(time[:-1], time[1:]):
        step = float(stop - start) / substeps
        state = result[-1].copy()
        for _ in range(substeps):
            k1 = dynamics_matrix @ state + offset
            k2 = dynamics_matrix @ (state + 0.5 * step * k1) + offset
            k3 = dynamics_matrix @ (state + 0.5 * step * k2) + offset
            k4 = dynamics_matrix @ (state + step * k3) + offset
            state = state + step * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        result.append(state)
    return np.asarray(result)


def _dynamics_integration(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete ODE integration evidence was not recorded")
    node, time, observed, simulated, dynamics_matrix, offset, _, substeps = parsed
    expected = _dynamics_rk4(time, observed[0], dynamics_matrix, offset, substeps)
    if not np.allclose(simulated, expected, rtol=1e-9, atol=1e-9):
        return _failed("recorded ODE trajectory disagrees with independent RK4 integration")
    try:
        residuals = np.asarray(node["residuals"], dtype=float)
        recorded_rmse = node["metrics"]["rmse"]
    except (KeyError, TypeError, ValueError):
        return _failed("ODE residual or RMSE evidence is malformed")
    expected_residuals = observed - expected
    expected_rmse = float(np.sqrt(np.mean(np.square(expected_residuals))))
    if not np.allclose(residuals, expected_residuals, rtol=1e-9, atol=1e-9):
        return _failed("ODE residuals disagree with independent recomputation")
    if not _finite_number(recorded_rmse) or not math.isclose(recorded_rmse, expected_rmse, rel_tol=1e-9, abs_tol=1e-9):
        return _failed("ODE RMSE disagrees with independent recomputation")
    return _passed("RK4 trajectory, residuals, and RMSE were independently recomputed")


def _dynamics_boundary(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    node = _find(nodes, "boundary_check")
    if parsed is None or node is None or not isinstance(node["boundary_check"], Mapping):
        return _not_assessed("ODE boundary evidence was not recorded")
    _, _, observed, simulated, _, _, _, _ = parsed
    evidence = node["boundary_check"]
    try:
        lower = np.asarray(evidence["lower"], dtype=float).reshape(-1)
        upper = np.asarray(evidence["upper"], dtype=float).reshape(-1)
        tolerance = float(evidence["tolerance"])
    except (KeyError, TypeError, ValueError):
        return _failed("ODE boundary evidence is malformed")
    if lower.shape != (observed.shape[1],) or upper.shape != lower.shape or tolerance < 0.0:
        return _failed("ODE boundary dimensions or tolerance are invalid")
    violation = float(max(
        np.max(lower - observed, initial=0.0),
        np.max(observed - upper, initial=0.0),
        np.max(lower - simulated, initial=0.0),
        np.max(simulated - upper, initial=0.0),
        0.0,
    ))
    recorded = evidence.get("max_violation")
    passed = violation <= tolerance
    if not _finite_number(recorded) or not math.isclose(recorded, violation, rel_tol=1e-9, abs_tol=1e-9):
        return _failed("ODE boundary violation disagrees with independent recomputation")
    if evidence.get("passed") is not passed or not passed:
        return _failed("ODE trajectory violates declared state bounds")
    return _passed("observed and simulated state bounds were independently checked")


def _dynamics_conservation(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    node = _find(nodes, "conservation_check")
    if parsed is None or node is None or not isinstance(node["conservation_check"], Mapping):
        return _not_assessed("ODE conservation evidence was not recorded")
    _, _, observed, simulated, _, _, _, _ = parsed
    evidence = node["conservation_check"]
    applicable = evidence.get("applicable")
    if applicable is False:
        if evidence.get("vectors") != [] or evidence.get("passed") is not True or not evidence.get("reason"):
            return _failed("non-applicable conservation evidence must be explicit and empty")
        return _passed("absence of a conservation law was explicitly declared")
    if applicable is not True:
        return _failed("ODE conservation applicability must be boolean")
    try:
        vectors = np.asarray(evidence["vectors"], dtype=float).reshape(-1, observed.shape[1])
        tolerance = float(evidence["tolerance"])
    except (KeyError, TypeError, ValueError):
        return _failed("ODE conservation evidence is malformed")
    observed_totals = observed @ vectors.T
    simulated_totals = simulated @ vectors.T
    observed_drift = float(np.max(np.abs(observed_totals - observed_totals[[0]])))
    simulated_drift = float(np.max(np.abs(simulated_totals - simulated_totals[[0]])))
    for name, expected in {
        "observed_max_drift": observed_drift,
        "simulated_max_drift": simulated_drift,
    }.items():
        recorded = evidence.get(name)
        if not _finite_number(recorded) or not math.isclose(recorded, expected, rel_tol=1e-9, abs_tol=1e-9):
            return _failed(f"ODE conservation drift mismatch: {name}")
    passed = max(observed_drift, simulated_drift) <= tolerance
    if evidence.get("passed") is not passed or not passed:
        return _failed("ODE trajectory violates a declared conservation law")
    return _passed("declared conservation laws were independently checked")


def _dynamics_step_sensitivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    node = _find(nodes, "step_sensitivity")
    if parsed is None or node is None or not isinstance(node["step_sensitivity"], Mapping):
        return _not_assessed("ODE step sensitivity evidence was not recorded")
    _, time, observed, simulated, dynamics_matrix, offset, _, substeps = parsed
    evidence = node["step_sensitivity"]
    refined_substeps = evidence.get("refined_substeps")
    threshold = evidence.get("threshold")
    if refined_substeps != 2 * substeps or not _finite_number(threshold) or threshold < 0.0:
        return _failed("ODE refined step design is malformed")
    refined = _dynamics_rk4(time, observed[0], dynamics_matrix, offset, refined_substeps)
    scale = max(float(np.max(np.abs(refined))), np.finfo(float).eps)
    change = float(np.max(np.abs(simulated - refined)) / scale)
    recorded = evidence.get("max_relative_change")
    passed = change <= threshold
    if not _finite_number(recorded) or not math.isclose(recorded, change, rel_tol=1e-9, abs_tol=1e-9):
        return _failed("ODE step sensitivity disagrees with independent recomputation")
    if evidence.get("passed") is not passed or not passed:
        return _failed("ODE integration is unstable under step refinement")
    return _passed("RK4 trajectory is stable under a twofold step refinement")


def _graph_evidence(
    nodes: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], int, int, int, list[Mapping[str, Any]]] | None:
    node = _find(
        nodes,
        "node_count",
        "source",
        "sink",
        "edges",
        "connectivity",
        "baseline",
        "shortest_path",
        "max_flow",
    )
    if node is None:
        return None
    node_count = node["node_count"]
    source = node["source"]
    sink = node["sink"]
    edges = node["edges"]
    if (
        not isinstance(node_count, int)
        or isinstance(node_count, bool)
        or node_count < 2
        or not isinstance(source, int)
        or not isinstance(sink, int)
        or source == sink
        or source not in range(node_count)
        or sink not in range(node_count)
        or not isinstance(edges, list)
        or not edges
        or not all(isinstance(edge, Mapping) for edge in edges)
    ):
        return None
    for edge_id, edge in enumerate(edges):
        if edge.get("id") != edge_id:
            return None
        start = edge.get("source")
        target = edge.get("target")
        weight = edge.get("weight")
        capacity = edge.get("capacity")
        if (
            not isinstance(start, int)
            or not isinstance(target, int)
            or start not in range(node_count)
            or target not in range(node_count)
            or start == target
            or not _finite_number(weight)
            or not _finite_number(capacity)
            or weight < 0.0
            or capacity < 0.0
        ):
            return None
    return node, node_count, source, sink, edges


def _graph_reachable_nodes(start: int, adjacency: list[list[int]]) -> list[int]:
    visited = {start}
    queue = [start]
    for node in queue:
        for neighbor in adjacency[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return sorted(visited)


def _graph_baseline(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete graph baseline evidence was not recorded")
    node, _, _, _, _ = parsed
    baseline = node["baseline"]
    shortest = node["shortest_path"]
    flow = node["max_flow"]
    if not all(isinstance(item, Mapping) for item in (baseline, shortest, flow)):
        return _failed("graph baseline evidence is malformed")
    baseline_distance = baseline.get("shortest_hop_distance")
    distance = shortest.get("distance")
    baseline_flow = baseline.get("flow_value")
    flow_value = flow.get("value")
    if not all(_finite_number(value) for value in (
        baseline_distance,
        distance,
        baseline_flow,
        flow_value,
    )):
        return _failed("graph baseline comparison requires finite path and flow values")
    if distance > baseline_distance + 1e-10:
        return _failed("weighted shortest path is worse than the shortest-hop baseline")
    if flow_value < baseline_flow - 1e-10:
        return _failed("maximum flow is worse than the zero-flow baseline")
    return _passed("weighted path and feasible flow improve on declared graph baselines")


def _graph_connectivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete graph connectivity evidence was not recorded")
    node, node_count, source, _, edges = parsed
    evidence = node["connectivity"]
    if not isinstance(evidence, Mapping):
        return _failed("graph connectivity evidence must be a mapping")
    forward = [[] for _ in range(node_count)]
    reverse = [[] for _ in range(node_count)]
    weak = [[] for _ in range(node_count)]
    for edge in edges:
        start = edge["source"]
        target = edge["target"]
        forward[start].append(target)
        reverse[target].append(start)
        weak[start].append(target)
        weak[target].append(start)
    expected_reachable = _graph_reachable_nodes(source, forward)
    weakly_connected = len(_graph_reachable_nodes(0, weak)) == node_count
    strongly_connected = (
        len(_graph_reachable_nodes(0, forward)) == node_count
        and len(_graph_reachable_nodes(0, reverse)) == node_count
    )
    if evidence.get("reachable_from_source") != expected_reachable:
        return _failed("source reachability disagrees with independent graph traversal")
    if evidence.get("weakly_connected") is not weakly_connected:
        return _failed("weak connectivity status disagrees with independent traversal")
    if evidence.get("strongly_connected") is not strongly_connected:
        return _failed("strong connectivity status disagrees with independent traversal")
    return _passed("reachability and directed connectivity were independently recomputed")


def _graph_shortest_path(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete shortest-path evidence was not recorded")
    node, node_count, source, sink, edges = parsed
    evidence = node["shortest_path"]
    if not isinstance(evidence, Mapping):
        return _failed("shortest-path evidence must be a mapping")
    distances = np.full(node_count, np.inf)
    distances[source] = 0.0
    for _ in range(node_count - 1):
        changed = False
        for edge in edges:
            start = edge["source"]
            target = edge["target"]
            candidate = distances[start] + edge["weight"]
            if candidate < distances[target]:
                distances[target] = candidate
                changed = True
        if not changed:
            break
    expected_labels = [float(value) if np.isfinite(value) else None for value in distances]
    if evidence.get("distance_labels") != expected_labels:
        return _failed("shortest-path distance labels disagree with Bellman-Ford recomputation")
    reachable = bool(np.isfinite(distances[sink]))
    if evidence.get("reachable") is not reachable:
        return _failed("shortest-path reachability disagrees with recomputed labels")
    if not reachable:
        if evidence.get("path") or evidence.get("edge_ids") or evidence.get("distance") is not None:
            return _failed("unreachable sink must not report a path or finite distance")
        return _passed("sink unreachability was independently verified")
    path = evidence.get("path")
    edge_ids = evidence.get("edge_ids")
    distance = evidence.get("distance")
    if (
        not isinstance(path, list)
        or not isinstance(edge_ids, list)
        or len(edge_ids) != len(path) - 1
        or path[0] != source
        or path[-1] != sink
        or not _finite_number(distance)
    ):
        return _failed("shortest-path node and edge sequences are malformed")
    path_distance = 0.0
    for start, target, edge_id in zip(path[:-1], path[1:], edge_ids):
        if not isinstance(edge_id, int) or edge_id not in range(len(edges)):
            return _failed("shortest-path edge id is invalid")
        edge = edges[edge_id]
        if edge["source"] != start or edge["target"] != target:
            return _failed("shortest-path edge sequence does not match its nodes")
        path_distance += edge["weight"]
    if not math.isclose(distance, path_distance, rel_tol=1e-10, abs_tol=1e-10):
        return _failed("reported shortest-path distance does not match the path edges")
    if not math.isclose(distance, distances[sink], rel_tol=1e-10, abs_tol=1e-10):
        return _failed("reported path is not shortest under independent recomputation")
    return _passed("path sequence and optimal distance were independently verified")


def _graph_flow_values(
    node: Mapping[str, Any],
    node_count: int,
    source: int,
    sink: int,
    edges: list[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, float] | None:
    evidence = node["max_flow"]
    if not isinstance(evidence, Mapping):
        return None
    try:
        flows = np.asarray(evidence["edge_flows"], dtype=float).reshape(-1)
        value = float(evidence["value"])
    except (KeyError, TypeError, ValueError):
        return None
    if flows.shape != (len(edges),) or not np.all(np.isfinite(flows)) or not math.isfinite(value):
        return None
    balances = np.zeros(node_count, dtype=float)
    for edge, flow in zip(edges, flows):
        balances[edge["source"]] -= flow
        balances[edge["target"]] += flow
    if not math.isclose(-balances[source], value, rel_tol=1e-10, abs_tol=1e-10):
        return None
    if not math.isclose(balances[sink], value, rel_tol=1e-10, abs_tol=1e-10):
        return None
    return flows, balances, value


def _graph_flow(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete graph flow evidence was not recorded")
    node, node_count, source, sink, edges = parsed
    flow_values = _graph_flow_values(node, node_count, source, sink, edges)
    if flow_values is None:
        return _failed("graph flow evidence is malformed or inconsistent at source and sink")
    flows, balances, _ = flow_values
    capacities = np.asarray([edge["capacity"] for edge in edges])
    capacity_violation = float(max(
        np.max(-flows, initial=0.0),
        np.max(flows - capacities, initial=0.0),
        0.0,
    ))
    internal = [node_id for node_id in range(node_count) if node_id not in {source, sink}]
    conservation_violation = float(np.max(np.abs(balances[internal]), initial=0.0))
    evidence = node["max_flow"]
    for name, expected in {
        "capacity_violation": capacity_violation,
        "conservation_violation": conservation_violation,
    }.items():
        recorded = evidence.get(name)
        if not _finite_number(recorded) or not math.isclose(recorded, expected, rel_tol=1e-10, abs_tol=1e-10):
            return _failed(f"graph flow certificate mismatch: {name}")
    if max(capacity_violation, conservation_violation) > 1e-10:
        return _failed("reported graph flow is not feasible")
    return _passed("edge capacities and node flow conservation were independently verified")


def _graph_min_cut(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return _not_assessed("complete graph minimum-cut evidence was not recorded")
    node, node_count, source, sink, edges = parsed
    flow_values = _graph_flow_values(node, node_count, source, sink, edges)
    evidence = node["max_flow"]
    cut = evidence.get("min_cut") if isinstance(evidence, Mapping) else None
    if flow_values is None or not isinstance(cut, Mapping):
        return _failed("minimum-cut evidence is malformed")
    _, _, flow_value = flow_values
    source_side = cut.get("source_side")
    sink_side = cut.get("sink_side")
    edge_ids = cut.get("edge_ids")
    if (
        not isinstance(source_side, list)
        or not isinstance(sink_side, list)
        or not isinstance(edge_ids, list)
        or set(source_side).union(sink_side) != set(range(node_count))
        or set(source_side).intersection(sink_side)
        or source not in source_side
        or sink not in sink_side
    ):
        return _failed("minimum-cut partition is invalid")
    source_set = set(source_side)
    expected_edge_ids = [
        edge["id"]
        for edge in edges
        if edge["source"] in source_set and edge["target"] not in source_set
    ]
    capacity = sum(edges[edge_id]["capacity"] for edge_id in expected_edge_ids)
    if edge_ids != expected_edge_ids:
        return _failed("minimum-cut edge ids disagree with the declared partition")
    recorded_capacity = cut.get("capacity")
    if not _finite_number(recorded_capacity) or not math.isclose(recorded_capacity, capacity, rel_tol=1e-10, abs_tol=1e-10):
        return _failed("minimum-cut capacity disagrees with independent recomputation")
    if not math.isclose(flow_value, capacity, rel_tol=1e-10, abs_tol=1e-10):
        return _failed("max-flow value does not equal the certified cut capacity")
    return _passed("flow-cut equality independently certifies maximum-flow optimality")


def _temporal_leakage(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "leakage_check")
    if node is None:
        return _not_assessed("temporal leakage evidence was not recorded")
    evidence = node["leakage_check"]
    if not isinstance(evidence, Mapping):
        return _failed("temporal leakage evidence must be a mapping")
    train_max = evidence.get("max_train_time")
    holdout_min = evidence.get("min_holdout_time")
    if not _finite_number(train_max) or not _finite_number(holdout_min):
        return _failed("temporal split boundaries are missing or non-finite")
    recomputed = train_max < holdout_min
    if evidence.get("passed") is not recomputed:
        return _failed("reported leakage status disagrees with the split boundaries")
    if not recomputed:
        return _failed("training time overlaps or follows the holdout period")
    return _passed("training observations strictly precede the holdout period")


def _prediction_interval(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = _find(nodes, "observed", "predicted", "prediction_interval")
    if node is None:
        return _not_assessed("prediction interval evidence was not recorded")
    observed = node["observed"]
    predicted = node["predicted"]
    evidence = node["prediction_interval"]
    if not isinstance(observed, list) or not isinstance(predicted, list) or not isinstance(evidence, Mapping):
        return _failed("prediction interval evidence has invalid types")
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
        return _failed("prediction interval arrays have inconsistent lengths")
    values = [*observed, *predicted, *lower, *upper]
    if not all(_finite_number(value) for value in values):
        return _failed("prediction interval evidence contains non-finite values")
    if not _finite_number(level) or not 0 < level < 1:
        return _failed("prediction interval level must be between zero and one")
    if any(low > estimate or estimate > high for low, estimate, high in zip(lower, predicted, upper)):
        return _failed("point predictions must lie inside their prediction intervals")
    recomputed_coverage = sum(
        low <= actual <= high
        for low, actual, high in zip(lower, observed, upper)
    ) / len(observed)
    if not _finite_number(recorded_coverage) or not math.isclose(
        recorded_coverage,
        recomputed_coverage,
        rel_tol=1e-8,
        abs_tol=1e-8,
    ):
        return _failed("recorded interval coverage does not match independent recomputation")
    return _passed("prediction interval bounds and empirical coverage were independently recomputed")
