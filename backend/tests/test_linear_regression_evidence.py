from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.guidance.model_validators import build_default_model_validator_registry
from app.guidance.operator_catalog import build_default_operator_registry


def fit_line(x, y, *, kinds=("constant", "identity"), **parameters):
    runtime = build_default_operator_registry().runtime("regression.basis_least_squares")
    return runtime.invoke(inputs={"x": list(x), "y": list(y)},
                          parameters={"basis": [{"kind": k} for k in kinds], **parameters},
                          random_seed=None, work_dir=Path.cwd())["fit"]


def assess(fit):
    return build_default_model_validator_registry().assess(
        family="regression", candidate_roles={"baseline", "recommended"}, evidence=[fit])


@pytest.mark.parametrize("kinds", [("constant", "identity"), ("identity", "constant")])
def test_descriptive_line_reports_measured_leave_one_out_and_boundary_evidence(kinds):
    data = pd.read_excel(Path(__file__).parent / "fixtures/anscombe_linear_fit/observations.xlsx")
    x, y = data.x1.to_numpy(), data.y1.to_numpy()
    fit = fit_line(x, y, kinds=kinds)
    assessment = assess(fit)
    assert assessment.status == "passed", assessment.reasons

    slope, intercept = np.polyfit(x, y, 1)
    assert fit["named_coefficients"] == pytest.approx({"intercept": intercept, "slope": slope})
    assert fit["baseline_metrics"]["rmse"] == pytest.approx(np.std(y))
    assert fit["x"] == x.tolist() and fit["observed"] == y.tolist()
    endpoints = np.array([min(x), max(x)])
    full = intercept + slope * endpoints
    assert fit["extreme_inputs"]["x"] == endpoints.tolist()
    assert fit["extreme_inputs"]["predicted"] == pytest.approx(full)

    held_out, means, moves = [], [], []
    for i in range(len(x)):
        keep = np.arange(len(x)) != i
        a, b = np.polyfit(x[keep], y[keep], 1)
        held_out.append(b + a * x[i])
        means.append(np.mean(y[keep]))
        moves.append(max(abs(b + a * endpoints - full)))
    cv = fit["cross_validation"]
    assert cv["predicted"] == pytest.approx(held_out)
    assert cv["baseline_predicted"] == pytest.approx(means)
    assert cv["fold_rmse"] == pytest.approx(abs(y - held_out))
    assert cv["max_allowed_rmse"] == pytest.approx(max(abs(y - means)))
    assert cv["rmse"] == pytest.approx(np.sqrt(np.mean((y - held_out) ** 2)))
    assert cv["baseline_rmse"] == pytest.approx(np.sqrt(np.mean((y - means) ** 2)))
    assert fit["sensitivity"]["threshold"] == 0.2
    assert fit["sensitivity"]["max_relative_change"] == pytest.approx(max(moves) / np.ptp(y))
    assert assess(fit_line(x, y, sensitivity_threshold=0)).checks["sensitivity"].status == "failed"


@pytest.mark.parametrize(("x", "y", "parameters"), [
    ([1, 1, 1], [1, 2, 3], {}), ([1, 1, 2], [1, 2, 3], {}),
    ([1, 2], [1, 2], {}), ([1, 2, 3], [1, 2, 3], {"kinds": ("identity",)}),
    ([1, 2, 3], [1, 2, 3], {"search": {"candidates": [1]}}),
])
def test_inapplicable_fits_do_not_supply_deleted_line_evidence(x, y, parameters):
    fitted = fit_line(x, y, **parameters)
    assert "cross_validation" not in fitted and "sensitivity" not in fitted
    assert assess(fitted).status != "passed"
