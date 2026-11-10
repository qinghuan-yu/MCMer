from __future__ import annotations

import importlib

import pytest

from app.guidance.model_validators import build_default_model_validator_registry
from app.guidance.operator_catalog import build_default_operator_registry


OPERATOR_OWNERS = {
    "artifact.extract_mapping": "artifact_io",
    "artifact.extract_matrix": "artifact_io",
    "artifact.extract_scalar": "artifact_io",
    "artifact.extract_vector": "artifact_io",
    "data.read_excel_table": "artifact_io",
    "data.inspect_file": "artifact_io",
    "data.profile_table": "artifact_io",
    "data.scalar_constant": "artifact_io",
    "decision.mcda_weighted_ranking": "decision",
    "design.periodic_key_moments": "diagnostics",
    "diagnostics.design_rank": "diagnostics",
    "diagnostics.leave_one_out": "diagnostics",
    "dynamics.affine_ode_rk4": "dynamics",
    "forecast.robust_regularized_time_split": "forecast",
    "forecast.future_basis_rolling_origin": "forecast",
    "graph.shortest_path_max_flow": "graph",
    "model_selection.continuous_hinge_bic": "regression",
    "optimization.constrained_quadratic_multistart": "optimization",
    "optimization.milp": "milp",
    "plot.xy_evidence": "plotting",
    "regression.basis_least_squares": "regression",
    "regression.zero_intercept_grouped": "regression",
}


@pytest.mark.parametrize("operator_id, owner", OPERATOR_OWNERS.items())
def test_registered_operator_is_owned_by_its_numerical_domain(
    operator_id: str, owner: str,
) -> None:
    registry = build_default_operator_registry()
    runtime = registry.runtime(operator_id)

    assert {item.operator_id for item in build_default_operator_registry().definitions()} == set(OPERATOR_OWNERS)
    assert runtime is not None
    assert runtime.executor is not None
    assert runtime.executor.__module__ == f"app.guidance.operator_runtimes.{owner}"
    assert runtime.definition.version == (
        "3.0.0" if operator_id == "data.read_excel_table"
        else "1.0.0" if operator_id in {"data.inspect_file", "data.profile_table"}
        else "2.0.0"
    )
    assert runtime.definition.deterministic is True
    assert runtime.definition.validator_id == f"{operator_id}.validator.v1"


@pytest.mark.parametrize(
    ("family", "check", "owner"),
    [
        ("regression", "residual_recompute", "regression"),
        ("forecast", "temporal_leakage", "forecast"),
        ("future_forecast", "sensitivity_recompute", "forecast"),
        ("optimization", "feasibility_certificate", "optimization"),
        ("mixed_integer_linear_optimization", "bound_and_gap", "milp"),
        ("evaluation", "weight_recompute", "evaluation"),
        ("differential_equation", "parameter_recompute", "dynamics"),
        ("graph", "min_cut_certificate", "graph"),
        ("regression", "sensitivity", "robustness"),
    ],
)
def test_model_assessment_is_owned_by_its_mathematical_domain(
    family: str, check: str, owner: str,
) -> None:
    registry = build_default_model_validator_registry()

    assert registry._validators[family][check].__module__ == (
        f"app.guidance.model_validation.{owner}"
    )


def test_validation_contracts_are_owned_below_registry_and_domain_modules() -> None:
    contracts = importlib.import_module("app.guidance.model_validation.contracts")

    assert contracts.ValidationCheck.__module__ == contracts.__name__
    assert contracts.ModelValidationAssessment.__module__ == contracts.__name__
