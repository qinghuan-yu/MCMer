from copy import deepcopy

import numpy as np

from app.guidance.model_validators import build_default_model_validator_registry


def _future_forecast_evidence() -> dict:
    from app.guidance.operator_catalog import build_default_operator_registry

    time = np.arange(32, dtype=float)
    target = 10.0 + 0.5 * time + 3.0 * np.sin(2.0 * np.pi * time / 4.0)
    runtime = build_default_operator_registry().runtime("forecast.future_basis_rolling_origin")
    return runtime.executor(
        inputs={"time": time.tolist(), "target": target.tolist()},
        parameters={
            "future_time": [32.0, 33.0, 34.0],
            "candidate_periods": [4.0, 8.0],
            "season_length": 4,
            "initial_train_size": 16,
            "interval_level": 0.9,
        },
        random_seed=None,
        work_dir=None,
    )["forecast"]


def test_regression_family_runs_independent_adequacy_validators() -> None:
    registry = build_default_model_validator_registry()
    evidence = {
        "observed": [1.0, 2.0, 3.0, 4.0],
        "predicted": [1.1, 1.9, 3.1, 3.9],
        "residuals": [-0.1, 0.1, -0.1, 0.1],
        "metrics": {"rmse": 0.1},
        "baseline_metrics": {"rmse": 0.5},
        "cross_validation": {"fold_rmse": [0.12, 0.14], "max_allowed_rmse": 0.2},
        "sensitivity": {"max_relative_change": 0.04, "threshold": 0.1},
        "extreme_inputs": {"finite": True, "constraints_satisfied": True},
    }

    assessment = registry.assess(
        family="regression",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.status == "passed"
    assert set(assessment.checks) == {
        "baseline_comparison",
        "residual_recompute",
        "cross_validation",
        "sensitivity",
        "extreme_inputs",
    }
    assert all(check.status == "passed" for check in assessment.checks.values())


def test_optimization_family_requires_consistent_multistart_evidence() -> None:
    registry = build_default_model_validator_registry()
    evidence = {
        "x": [2.0],
        "objective_value": 4.0,
        "baseline_objective": 10.0,
        "constraints_satisfied": True,
        "problem": {
            "objective": {
                "quadratic": [[2.0]],
                "linear": [0.0],
                "constant": 0.0,
            },
            "linear_constraints": {
                "a_ub": [],
                "b_ub": [],
                "a_eq": [],
                "b_eq": [],
            },
            "quadratic_constraints": [],
            "bounds": {"lower": [1.0], "upper": [3.0]},
            "constraint_tolerance": 1e-7,
        },
        "feasibility_certificate": {
            "bound_violation": 0.0,
            "linear_inequality_violation": 0.0,
            "linear_equality_violation": 0.0,
            "quadratic_constraint_violation": 0.0,
            "max_violation": 0.0,
            "constraints_satisfied": True,
        },
        "bound_certificate": {
            "lower_bound": 0.0,
            "upper_bound": 4.0,
            "absolute_gap": 4.0,
            "valid": True,
        },
        "multistart": {"objective_values": [4.0, 4.0000001, 4.0000002], "tolerance": 1e-5},
        "sensitivity": {"max_relative_change": 0.03, "threshold": 0.1},
        "extreme_inputs": {"finite": True, "constraints_satisfied": True},
    }

    assessment = registry.assess(
        family="optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.status == "passed"
    assert assessment.checks["multistart"].status == "passed"
    assert assessment.checks["feasibility_certificate"].status == "passed"
    assert assessment.checks["bound_certificate"].status == "passed"
    assert "residual_recompute" not in assessment.checks


def _milp_evidence() -> dict:
    return {
        "x": [3.0, 0.0],
        "objective_value": 6.0,
        "compiled_model": {
            "objective_sense": "minimize",
            "variable_labels": ["x[a]", "x[b]"],
            "objective": [2.0, 3.0],
            "integrality": [1, 1],
            "lower_bounds": [0.0, 0.0],
            "upper_bounds": [5.0, 5.0],
            "constraint_labels": ["conservation"],
            "matrix": [[1.0, 1.0]],
            "constraint_lower": [3.0],
            "constraint_upper": [3.0],
            "provenance": [],
        },
        "baseline": {
            "x": [0.0, 3.0],
            "objective_value": 9.0,
            "feasible": True,
        },
        "solver": {"success": True, "mip_gap": 0.0},
        "feasibility_certificate": {
            "max_constraint_violation": 0.0,
            "max_integrality_violation": 0.0,
        },
        "bound_certificate": {"dual_bound": 6.0, "primal_bound": 6.0},
    }


def test_milp_family_independently_recomputes_objective_feasibility_and_bounds() -> None:
    registry = build_default_model_validator_registry()
    evidence = _milp_evidence()

    assessment = registry.assess(
        family="mixed_integer_linear_optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.status == "passed", assessment.reasons
    assert set(assessment.checks) == {
        "objective_recompute",
        "feasibility_and_conservation",
        "integrality_recompute",
        "bound_and_gap",
        "baseline_comparison",
        "constraint_relevance",
    }

    evidence["objective_value"] = 7.0
    forged_objective = registry.assess(
        family="mixed_integer_linear_optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )
    assert forged_objective.checks["objective_recompute"].status == "failed"

    evidence = _milp_evidence()
    evidence["x"][0] = 2.5
    forged_integer = registry.assess(
        family="mixed_integer_linear_optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )
    assert forged_integer.checks["integrality_recompute"].status == "failed"


def _degenerate_milp_evidence() -> dict:
    # 约束 x_a + x_b >= 0 不起作用：最优解与仅凭变量界得到的解相同。
    evidence = _milp_evidence()
    evidence["x"] = [0.0, 0.0]
    evidence["objective_value"] = 0.0
    evidence["compiled_model"]["constraint_lower"] = [0.0]
    evidence["compiled_model"]["constraint_upper"] = [None]
    evidence["baseline"] = {"x": [0.0, 0.0], "objective_value": 0.0, "feasible": True}
    evidence["bound_certificate"] = {"dual_bound": 0.0, "primal_bound": 0.0}
    return evidence


def _assess_milp(evidence: dict):
    return build_default_model_validator_registry().assess(
        family="mixed_integer_linear_optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )


def test_milp_optimum_equal_to_bounds_only_optimum_fails_constraint_relevance() -> None:
    assessment = _assess_milp(_degenerate_milp_evidence())

    assert assessment.status == "failed"
    check = assessment.checks["constraint_relevance"]
    assert check.status == "failed"
    assert "variable bounds alone" in check.reason
    assert all(
        item.status == "passed"
        for name, item in assessment.checks.items()
        if name != "constraint_relevance"
    )


def test_constraint_relevance_passes_when_bounds_alone_leave_objective_unbounded() -> None:
    evidence = _degenerate_milp_evidence()
    evidence["compiled_model"]["upper_bounds"] = [None, None]
    evidence["compiled_model"]["objective_sense"] = "maximize"
    evidence["compiled_model"]["constraint_lower"] = [None]
    evidence["compiled_model"]["constraint_upper"] = [0.0]
    assert _assess_milp(evidence).checks["constraint_relevance"].status == "passed"


def test_constraint_relevance_is_vacuous_for_constant_objective() -> None:
    evidence = _degenerate_milp_evidence()
    evidence["compiled_model"]["objective"] = [0.0, 0.0]
    check = _assess_milp(evidence).checks["constraint_relevance"]
    assert check.status == "passed"
    assert "constant objective" in check.reason


def test_constraint_relevance_rounds_integer_bounds_toward_feasibility() -> None:
    # 整数变量下界 0.5 时仅凭变量界的最小值为 2*1 = 2，而非 1；约束推高至 6 仍有效。
    evidence = _milp_evidence()
    evidence["compiled_model"]["lower_bounds"] = [0.5, 0.0]
    assert _assess_milp(evidence).checks["constraint_relevance"].status == "passed"

    evidence = _degenerate_milp_evidence()
    evidence["compiled_model"]["lower_bounds"] = [0.5, 0.0]
    evidence["x"] = [1.0, 0.0]
    evidence["objective_value"] = 2.0
    evidence["baseline"] = {"x": [1.0, 0.0], "objective_value": 2.0, "feasible": True}
    evidence["bound_certificate"] = {"dual_bound": 2.0, "primal_bound": 2.0}
    assert _assess_milp(evidence).checks["constraint_relevance"].status == "failed"


def test_evaluation_family_independently_recomputes_ranking_and_stability() -> None:
    registry = build_default_model_validator_registry()
    evidence = {
        "decision_matrix": [[90.0, 8.0], [80.0, 5.0], [70.0, 3.0]],
        "criterion_types": ["benefit", "cost"],
        "normalized_matrix": [[1.0, 0.0], [0.5, 0.6], [0.0, 1.0]],
        "weight_method": "provided",
        "weights": [0.7, 0.3],
        "scores": [0.7, 0.53, 0.3],
        "ranking": [0, 1, 2],
        "weight_perturbation": {
            "fraction": 0.1,
            "runs": [
                {"weights": [0.6774193548387096, 0.3225806451612903], "ranking": [0, 1, 2]},
                {"weights": [0.719626168224299, 0.2803738317757009], "ranking": [0, 1, 2]},
                {"weights": [0.7216494845360825, 0.27835051546391754], "ranking": [0, 1, 2]},
                {"weights": [0.6796116504854368, 0.32038834951456313], "ranking": [0, 1, 2]},
            ],
            "top_rank_retention_rate": 1.0,
            "min_rank_correlation": 1.0,
            "threshold": 0.8,
            "stable": True,
        },
        "extreme_inputs": {"finite": True, "constraints_satisfied": True},
    }

    assessment = registry.assess(
        family="evaluation",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.status == "passed"
    assert set(assessment.checks) == {
        "normalization_recompute",
        "weight_recompute",
        "ranking_recompute",
        "weight_sensitivity",
        "extreme_inputs",
    }

    evidence["scores"][1] = 0.8
    forged = registry.assess(
        family="evaluation",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert forged.status == "failed"
    assert forged.checks["ranking_recompute"].status == "failed"


def test_dynamics_family_independently_recomputes_parameters_and_integration() -> None:
    registry = build_default_model_validator_registry()
    evidence = {
        "time": [0.0, 1.0, 2.0],
        "observed_states": [[0.0], [1.0], [2.0]],
        "parameter_estimate": {
            "matrix": [[0.0]],
            "offset": [1.0],
            "include_offset": True,
        },
        "identifiability": {
            "design_rank": 2,
            "parameter_count_per_state": 2,
            "identifiable": True,
            "condition_number": float(np.linalg.cond(np.asarray([[0.5, 1.0], [1.5, 1.0]]))),
            "max_condition_number": 1e10,
        },
        "simulated_states": [[0.0], [1.0], [2.0]],
        "residuals": [[0.0], [0.0], [0.0]],
        "metrics": {"rmse": 0.0},
        "baseline_metrics": {"rmse": 1.2909944487358056},
        "integration": {"method": "rk4", "substeps": 1},
        "boundary_check": {
            "lower": [0.0],
            "upper": [2.0],
            "tolerance": 1e-8,
            "max_violation": 0.0,
            "passed": True,
        },
        "conservation_check": {
            "applicable": False,
            "reason": "no conservation vector declared",
            "vectors": [],
            "tolerance": 1e-8,
            "passed": True,
        },
        "step_sensitivity": {
            "refined_substeps": 2,
            "max_relative_change": 0.0,
            "threshold": 1e-6,
            "passed": True,
        },
        "extreme_inputs": {"finite": True, "constraints_satisfied": True},
    }

    assessment = registry.assess(
        family="differential_equation",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.status == "passed", assessment.reasons
    assert set(assessment.checks) == {
        "baseline_comparison",
        "parameter_recompute",
        "integration_recompute",
        "boundary_check",
        "conservation_check",
        "step_sensitivity",
        "extreme_inputs",
    }

    recorded_condition = evidence["identifiability"]["condition_number"]
    evidence["identifiability"]["condition_number"] = 1.0
    forged_condition = registry.assess(
        family="differential_equation",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )
    assert forged_condition.status == "failed"
    assert forged_condition.checks["parameter_recompute"].status == "failed"
    evidence["identifiability"]["condition_number"] = recorded_condition

    evidence["simulated_states"][2][0] = 2.5
    forged = registry.assess(
        family="differential_equation",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )
    assert forged.status == "failed"
    assert forged.checks["integration_recompute"].status == "failed"


def test_graph_family_independently_verifies_path_flow_and_min_cut() -> None:
    registry = build_default_model_validator_registry()
    evidence = {
        "node_count": 4,
        "source": 0,
        "sink": 3,
        "edges": [
            {"id": 0, "source": 0, "target": 1, "weight": 1.0, "capacity": 3.0},
            {"id": 1, "source": 0, "target": 2, "weight": 4.0, "capacity": 2.0},
            {"id": 2, "source": 1, "target": 2, "weight": 1.0, "capacity": 1.0},
            {"id": 3, "source": 1, "target": 3, "weight": 5.0, "capacity": 2.0},
            {"id": 4, "source": 2, "target": 3, "weight": 1.0, "capacity": 3.0},
        ],
        "connectivity": {
            "reachable_from_source": [0, 1, 2, 3],
            "weakly_connected": True,
            "strongly_connected": False,
        },
        "baseline": {"shortest_hop_distance": 6.0, "flow_value": 0.0},
        "shortest_path": {
            "reachable": True,
            "path": [0, 1, 2, 3],
            "edge_ids": [0, 2, 4],
            "distance": 3.0,
            "distance_labels": [0.0, 1.0, 2.0, 3.0],
        },
        "max_flow": {
            "value": 5.0,
            "edge_flows": [3.0, 2.0, 1.0, 2.0, 3.0],
            "capacity_violation": 0.0,
            "conservation_violation": 0.0,
            "min_cut": {
                "source_side": [0],
                "sink_side": [1, 2, 3],
                "edge_ids": [0, 1],
                "capacity": 5.0,
            },
        },
        "extreme_inputs": {"finite": True, "constraints_satisfied": True},
    }

    assessment = registry.assess(
        family="graph",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.status == "passed", assessment.reasons
    assert set(assessment.checks) == {
        "baseline_comparison",
        "connectivity_certificate",
        "shortest_path_certificate",
        "flow_certificate",
        "min_cut_certificate",
        "extreme_inputs",
    }

    evidence["shortest_path"]["distance"] = 2.0
    forged = registry.assess(
        family="graph",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )
    assert forged.status == "failed"
    assert forged.checks["shortest_path_certificate"].status == "failed"


def test_forecast_family_independently_rejects_temporal_leakage() -> None:
    registry = build_default_model_validator_registry()
    evidence = {
        "observed": [10.0, 12.0],
        "predicted": [10.5, 11.5],
        "residuals": [-0.5, 0.5],
        "metrics": {"rmse": 0.5},
        "baseline_metrics": {"rmse": 2.0},
        "cross_validation": {"fold_rmse": [0.5], "max_allowed_rmse": 2.0},
        "sensitivity": {"max_relative_change": 0.02, "threshold": 0.1},
        "extreme_inputs": {"finite": True, "constraints_satisfied": True},
        "leakage_check": {
            "passed": True,
            "max_train_time": 8.0,
            "min_holdout_time": 7.0,
        },
        "prediction_interval": {
            "level": 0.95,
            "lower": [9.0, 10.0],
            "upper": [12.0, 13.0],
            "empirical_coverage": 1.0,
        },
    }

    assessment = registry.assess(
        family="forecast",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.status == "failed"
    assert assessment.checks["temporal_leakage"].status == "failed"
    assert assessment.checks["prediction_interval"].status == "passed"


def test_forecast_interval_rejects_mismatched_bound_lengths() -> None:
    registry = build_default_model_validator_registry()
    assessment = registry.assess(
        family="forecast",
        candidate_roles={"baseline", "recommended"},
        evidence=[{
            "observed": [10.0, 12.0],
            "predicted": [10.5, 11.5],
            "residuals": [-0.5, 0.5],
            "metrics": {"rmse": 0.5},
            "baseline_metrics": {"rmse": 2.0},
            "cross_validation": {"fold_rmse": [0.5], "max_allowed_rmse": 2.0},
            "sensitivity": {"max_relative_change": 0.02, "threshold": 0.1},
            "extreme_inputs": {"finite": True, "constraints_satisfied": True},
            "leakage_check": {
                "passed": True,
                "max_train_time": 6.0,
                "min_holdout_time": 7.0,
            },
            "prediction_interval": {
                "level": 0.95,
                "lower": [9.0],
                "upper": [12.0, 13.0],
                "empirical_coverage": 0.5,
            },
        }],
    )

    assert assessment.checks["prediction_interval"].status == "failed"


def test_future_forecast_rejects_sensitivity_above_declared_threshold() -> None:
    from app.guidance.operator_catalog import build_default_operator_registry

    time = np.arange(32, dtype=float)
    target = 10.0 + 0.5 * time + 3.0 * np.sin(2.0 * np.pi * time / 4.0)
    target[-1] += 10.0
    runtime = build_default_operator_registry().runtime("forecast.future_basis_rolling_origin")
    evidence = runtime.executor(
        inputs={"time": time.tolist(), "target": target.tolist()},
        parameters={
            "future_time": [32.0, 33.0, 34.0],
            "candidate_periods": [4.0, 8.0],
            "season_length": 4,
            "initial_train_size": 16,
            "interval_level": 0.9,
            "sensitivity_threshold": 0.01,
        },
        random_seed=None,
        work_dir=None,
    )["forecast"]
    assert evidence["sensitivity"]["passed"] is False
    assert evidence["sensitivity"]["max_relative_change"] > 0.01

    assessment = build_default_model_validator_registry().assess(
        family="future_forecast",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.checks["sensitivity_recompute"].status == "failed"
    assert assessment.status == "failed"


def test_future_forecast_family_independently_recomputes_all_temporal_evidence() -> None:
    registry = build_default_model_validator_registry()
    evidence = _future_forecast_evidence()

    assessment = registry.assess(
        family="future_forecast",
        candidate_roles={"baseline", "recommended"},
        evidence=[evidence],
    )

    assert assessment.status == "passed", assessment.reasons
    assert set(assessment.checks) == {
        "rolling_origin_recompute",
        "future_index_check",
        "error_and_baseline_recompute",
        "interval_recompute",
        "sensitivity_recompute",
    }

    for check_name, mutate in [
        (
            "rolling_origin_recompute",
            lambda item: item["rolling_origin"]["folds"][0].update({
                "max_train_time": item["rolling_origin"]["folds"][0]["validation_time"]
            }),
        ),
        (
            "future_index_check",
            lambda item: item["future_time"].__setitem__(0, item["history_time"][-1]),
        ),
        (
            "error_and_baseline_recompute",
            lambda item: item["metrics"].update({"rmse": item["metrics"]["rmse"] + 1.0}),
        ),
        (
            "interval_recompute",
            lambda item: item["prediction_interval"]["lower"].__setitem__(
                0, item["future_predictions"][0] + 1.0
            ),
        ),
        (
            "sensitivity_recompute",
            lambda item: item["sensitivity"].update({
                "max_relative_change": item["sensitivity"]["max_relative_change"] + 0.5
            }),
        ),
    ]:
        forged = deepcopy(evidence)
        mutate(forged)
        rejected = registry.assess(
            family="future_forecast",
            candidate_roles={"baseline", "recommended"},
            evidence=[forged],
        )
        assert rejected.checks[check_name].status == "failed"
