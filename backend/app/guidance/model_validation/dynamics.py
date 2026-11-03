"""model_validation: dynamics 领域实现与合同。"""

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


def _dynamics_evidence(
    nodes: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, bool, int] | None:
    node = find_evidence(
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

def dynamics_parameters(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    if parsed is None:
        return not_assessed("complete ODE parameter evidence was not recorded")
    node, time, observed, _, dynamics_matrix, offset, include_offset, _ = parsed
    design, derivatives = _dynamics_design(time, observed, include_offset)
    coefficients, _, rank, _ = np.linalg.lstsq(design, derivatives, rcond=None)
    expected_matrix = coefficients[:-1].T if include_offset else coefficients.T
    expected_offset = coefficients[-1] if include_offset else np.zeros(observed.shape[1])
    identifiability = node["identifiability"]
    if not isinstance(identifiability, Mapping):
        return failed("ODE identifiability evidence must be a mapping")
    expected_identifiable = rank == design.shape[1]
    condition_number = float(np.linalg.cond(design))
    recorded_condition = identifiability.get("condition_number")
    max_condition_number = identifiability.get("max_condition_number")
    if (
        identifiability.get("design_rank") != int(rank)
        or identifiability.get("parameter_count_per_state") != int(design.shape[1])
        or identifiability.get("identifiable") is not bool(expected_identifiable)
    ):
        return failed("ODE design rank disagrees with independent recomputation")
    if not expected_identifiable:
        return failed("ODE parameter design is rank deficient")
    if (
        not finite_number(recorded_condition)
        or not math.isclose(recorded_condition, condition_number, rel_tol=1e-9, abs_tol=1e-9)
    ):
        return failed("ODE condition number disagrees with independent recomputation")
    if (
        not finite_number(max_condition_number)
        or max_condition_number <= 1.0
        or condition_number > max_condition_number
    ):
        return failed("ODE parameter design exceeds the declared condition-number limit")
    if not np.allclose(dynamics_matrix, expected_matrix, rtol=1e-9, atol=1e-9):
        return failed("ODE dynamics matrix disagrees with independent parameter estimation")
    if not np.allclose(offset, expected_offset, rtol=1e-9, atol=1e-9):
        return failed("ODE offset disagrees with independent parameter estimation")
    return passed_check("ODE parameters and design rank were independently recomputed")

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

def dynamics_integration(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    if parsed is None:
        return not_assessed("complete ODE integration evidence was not recorded")
    node, time, observed, simulated, dynamics_matrix, offset, _, substeps = parsed
    expected = _dynamics_rk4(time, observed[0], dynamics_matrix, offset, substeps)
    if not np.allclose(simulated, expected, rtol=1e-9, atol=1e-9):
        return failed("recorded ODE trajectory disagrees with independent RK4 integration")
    try:
        residuals = np.asarray(node["residuals"], dtype=float)
        recorded_rmse = node["metrics"]["rmse"]
    except (KeyError, TypeError, ValueError):
        return failed("ODE residual or RMSE evidence is malformed")
    expected_residuals = observed - expected
    expected_rmse = float(np.sqrt(np.mean(np.square(expected_residuals))))
    if not np.allclose(residuals, expected_residuals, rtol=1e-9, atol=1e-9):
        return failed("ODE residuals disagree with independent recomputation")
    if not finite_number(recorded_rmse) or not math.isclose(recorded_rmse, expected_rmse, rel_tol=1e-9, abs_tol=1e-9):
        return failed("ODE RMSE disagrees with independent recomputation")
    return passed_check("RK4 trajectory, residuals, and RMSE were independently recomputed")

def dynamics_boundary(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    node = find_evidence(nodes, "boundary_check")
    if parsed is None or node is None or not isinstance(node["boundary_check"], Mapping):
        return not_assessed("ODE boundary evidence was not recorded")
    _, _, observed, simulated, _, _, _, _ = parsed
    evidence = node["boundary_check"]
    try:
        lower = np.asarray(evidence["lower"], dtype=float).reshape(-1)
        upper = np.asarray(evidence["upper"], dtype=float).reshape(-1)
        tolerance = float(evidence["tolerance"])
    except (KeyError, TypeError, ValueError):
        return failed("ODE boundary evidence is malformed")
    if lower.shape != (observed.shape[1],) or upper.shape != lower.shape or tolerance < 0.0:
        return failed("ODE boundary dimensions or tolerance are invalid")
    violation = float(max(
        np.max(lower - observed, initial=0.0),
        np.max(observed - upper, initial=0.0),
        np.max(lower - simulated, initial=0.0),
        np.max(simulated - upper, initial=0.0),
        0.0,
    ))
    recorded = evidence.get("max_violation")
    passed = violation <= tolerance
    if not finite_number(recorded) or not math.isclose(recorded, violation, rel_tol=1e-9, abs_tol=1e-9):
        return failed("ODE boundary violation disagrees with independent recomputation")
    if evidence.get("passed") is not passed or not passed:
        return failed("ODE trajectory violates declared state bounds")
    return passed_check("observed and simulated state bounds were independently checked")

def dynamics_conservation(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    node = find_evidence(nodes, "conservation_check")
    if parsed is None or node is None or not isinstance(node["conservation_check"], Mapping):
        return not_assessed("ODE conservation evidence was not recorded")
    _, _, observed, simulated, _, _, _, _ = parsed
    evidence = node["conservation_check"]
    applicable = evidence.get("applicable")
    if applicable is False:
        if evidence.get("vectors") != [] or evidence.get("passed") is not True or not evidence.get("reason"):
            return failed("non-applicable conservation evidence must be explicit and empty")
        return passed_check("absence of a conservation law was explicitly declared")
    if applicable is not True:
        return failed("ODE conservation applicability must be boolean")
    try:
        vectors = np.asarray(evidence["vectors"], dtype=float).reshape(-1, observed.shape[1])
        tolerance = float(evidence["tolerance"])
    except (KeyError, TypeError, ValueError):
        return failed("ODE conservation evidence is malformed")
    observed_totals = observed @ vectors.T
    simulated_totals = simulated @ vectors.T
    observed_drift = float(np.max(np.abs(observed_totals - observed_totals[[0]])))
    simulated_drift = float(np.max(np.abs(simulated_totals - simulated_totals[[0]])))
    for name, expected in {
        "observed_max_drift": observed_drift,
        "simulated_max_drift": simulated_drift,
    }.items():
        recorded = evidence.get(name)
        if not finite_number(recorded) or not math.isclose(recorded, expected, rel_tol=1e-9, abs_tol=1e-9):
            return failed(f"ODE conservation drift mismatch: {name}")
    passed = max(observed_drift, simulated_drift) <= tolerance
    if evidence.get("passed") is not passed or not passed:
        return failed("ODE trajectory violates a declared conservation law")
    return passed_check("declared conservation laws were independently checked")

def dynamics_step_sensitivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _dynamics_evidence(nodes)
    node = find_evidence(nodes, "step_sensitivity")
    if parsed is None or node is None or not isinstance(node["step_sensitivity"], Mapping):
        return not_assessed("ODE step sensitivity evidence was not recorded")
    _, time, observed, simulated, dynamics_matrix, offset, _, substeps = parsed
    evidence = node["step_sensitivity"]
    refined_substeps = evidence.get("refined_substeps")
    threshold = evidence.get("threshold")
    if refined_substeps != 2 * substeps or not finite_number(threshold) or threshold < 0.0:
        return failed("ODE refined step design is malformed")
    refined = _dynamics_rk4(time, observed[0], dynamics_matrix, offset, refined_substeps)
    scale = max(float(np.max(np.abs(refined))), np.finfo(float).eps)
    change = float(np.max(np.abs(simulated - refined)) / scale)
    recorded = evidence.get("max_relative_change")
    passed = change <= threshold
    if not finite_number(recorded) or not math.isclose(recorded, change, rel_tol=1e-9, abs_tol=1e-9):
        return failed("ODE step sensitivity disagrees with independent recomputation")
    if evidence.get("passed") is not passed or not passed:
        return failed("ODE integration is unstable under step refinement")
    return passed_check("RK4 trajectory is stable under a twofold step refinement")
