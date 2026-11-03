"""operator_runtimes: dynamics 领域实现与合同。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.guidance.operator_runtimes.residual_metrics import residual_metrics


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

def affine_ode_rk4(
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
        "metrics": residual_metrics(residuals),
        "baseline_metrics": residual_metrics(baseline_residuals),
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
