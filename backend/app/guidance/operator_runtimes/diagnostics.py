"""operator_runtimes: diagnostics 领域实现与合同。"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def periodic_key_moments(
    *, inputs: dict, parameters: dict, random_seed, work_dir: Path
) -> dict:
    del inputs, random_seed, work_dir
    sample_count = int(parameters["sample_count"])
    period = int(parameters["period"])
    end_offset = int(parameters["window_end_offset"])
    moments = {int(value) for value in parameters["fixed_moments"]}
    for start in parameters["periodic_starts"]:
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

def design_rank(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del parameters, random_seed, work_dir
    matrix = np.asarray(inputs["matrix"], dtype=float)
    rank = int(np.linalg.matrix_rank(matrix))
    parameter_count = int(matrix.shape[1])
    return {"diagnostic": {
        "rank": rank,
        "parameter_count": parameter_count,
        "full_column_rank": rank == parameter_count,
    }}

def leave_one_out(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    x = np.asarray(inputs["x"], dtype=float)
    y = np.asarray(inputs["y"], dtype=float)
    zero_intercept = bool(parameters["zero_intercept"])

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
