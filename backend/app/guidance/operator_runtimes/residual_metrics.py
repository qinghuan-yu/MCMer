"""operator_runtimes: residual_metrics 领域实现与合同。"""

from __future__ import annotations

import numpy as np


def residual_metrics(residuals: np.ndarray) -> dict[str, float]:
    flat = np.asarray(residuals, dtype=float).reshape(-1)
    return {
        "rmse": float(np.sqrt(np.mean(np.square(flat)))),
        "max_abs_residual": float(np.max(np.abs(flat))),
    }
