import numpy as np

from app.guidance.model_validators import build_default_model_validator_registry


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
