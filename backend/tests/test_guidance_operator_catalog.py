from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _runtime(operator_id: str):
    from app.guidance.operator_catalog import build_default_operator_registry

    registered = build_default_operator_registry().runtime(operator_id)
    assert registered is not None
    assert registered.executor is not None
    assert registered.validator is not None
    return registered


def _execute(operator_id: str, *, inputs: dict, parameters: dict, work_dir: Path) -> dict:
    registered = _runtime(operator_id)
    outputs = registered.executor(
        inputs=inputs,
        parameters=parameters,
        random_seed=None,
        work_dir=work_dir,
    )
    assert registered.validator(outputs=outputs) == []
    return outputs


def test_catalog_exposes_only_problem_independent_operator_ids() -> None:
    from app.guidance.operator_catalog import DEFAULT_OPERATOR_IDS

    assert DEFAULT_OPERATOR_IDS == {
        "artifact.extract_mapping",
        "artifact.extract_matrix",
        "artifact.extract_scalar",
        "artifact.extract_vector",
        "data.read_excel_table",
        "data.scalar_constant",
        "decision.mcda_weighted_ranking",
        "design.periodic_key_moments",
        "diagnostics.design_rank",
        "diagnostics.leave_one_out",
        "dynamics.affine_ode_rk4",
        "forecast.robust_regularized_time_split",
        "graph.shortest_path_max_flow",
        "model_selection.continuous_hinge_bic",
        "optimization.constrained_quadratic_multistart",
        "plot.xy_evidence",
        "regression.basis_least_squares",
        "regression.zero_intercept_grouped",
    }
    assert not any("traffic" in item or "anchor" in item or "wuyi" in item for item in DEFAULT_OPERATOR_IDS)


def test_robust_regularized_forecast_uses_time_split_intervals_and_leakage_check(
    tmp_path: Path,
) -> None:
    time = np.arange(40, dtype=float)
    target = 5.0 + 1.5 * time
    target[12] += 30.0

    evaluation = _execute(
        "forecast.robust_regularized_time_split",
        inputs={
            "time": time.tolist(),
            "features": time[:, None].tolist(),
            "target": target.tolist(),
        },
        parameters={
            "train_size": 30,
            "alpha": 0.01,
            "huber_delta": 1.35,
            "interval_level": 0.95,
            "max_iterations": 100,
            "tolerance": 1e-8,
        },
        work_dir=tmp_path,
    )["evaluation"]

    assert evaluation["split"] == {
        "train_indices": list(range(30)),
        "holdout_indices": list(range(30, 40)),
    }
    assert evaluation["leakage_check"]["passed"] is True
    assert evaluation["regularization"]["alpha"] == 0.01
    assert evaluation["robust_fit"]["converged"] is True
    assert evaluation["metrics"]["rmse"] < evaluation["baseline_metrics"]["rmse"]
    assert len(evaluation["prediction_interval"]["lower"]) == 10
    assert len(evaluation["prediction_interval"]["upper"]) == 10


def test_constrained_quadratic_optimization_emits_feasibility_bounds_and_multistart(
    tmp_path: Path,
) -> None:
    solution = _execute(
        "optimization.constrained_quadratic_multistart",
        inputs={"data": [3.0, 3.0]},
        parameters={
            "objective": {
                "quadratic": [[2.0, 0.0], [0.0, 2.0]],
                "linear": [-2.0, -4.0],
                "constant": 5.0,
            },
            "linear_constraints": {
                "a_ub": [[-1.0, -1.0]],
                "b_ub": [-2.0],
                "a_eq": [],
                "b_eq": [],
            },
            "quadratic_constraints": [{
                "quadratic": [[2.0, 0.0], [0.0, 2.0]],
                "linear": [0.0, 0.0],
                "constant": 0.0,
                "upper_bound": 6.0,
            }],
            "bounds": {
                "lower": [0.0, 0.0],
                "upper": ["$data.0", "$data.1"],
            },
            "starts": [[0.0, 2.0], [1.0, 1.0], [2.0, 0.0]],
            "constraint_tolerance": 1e-7,
            "multistart_tolerance": 1e-6,
        },
        work_dir=tmp_path,
    )["solution"]

    assert solution["x"] == pytest.approx([1.0, 2.0], abs=1e-5)
    assert solution["objective_value"] == pytest.approx(0.0, abs=1e-8)
    assert solution["feasibility_certificate"]["constraints_satisfied"] is True
    assert solution["bound_certificate"]["lower_bound"] <= solution["objective_value"]
    assert solution["bound_certificate"]["upper_bound"] == solution["objective_value"]
    assert len(solution["multistart"]["objective_values"]) == 3


def test_mcda_ranking_normalizes_costs_and_reports_weight_stability(
    tmp_path: Path,
) -> None:
    decision = _execute(
        "decision.mcda_weighted_ranking",
        inputs={
            "matrix": [
                [90.0, 8.0],
                [80.0, 5.0],
                [70.0, 3.0],
            ],
        },
        parameters={
            "criterion_types": ["benefit", "cost"],
            "weight_method": "provided",
            "weights": [0.7, 0.3],
            "perturbation_fraction": 0.1,
            "stability_threshold": 0.8,
        },
        work_dir=tmp_path,
    )["decision"]

    assert np.allclose(
        decision["normalized_matrix"],
        [[1.0, 0.0], [0.5, 0.6], [0.0, 1.0]],
    )
    assert decision["weights"] == pytest.approx([0.7, 0.3])
    assert decision["scores"] == pytest.approx([0.7, 0.53, 0.3])
    assert decision["ranking"] == [0, 1, 2]
    assert decision["weight_perturbation"]["top_rank_retention_rate"] == 1.0
    assert decision["weight_perturbation"]["stable"] is True

    entropy_decision = _execute(
        "decision.mcda_weighted_ranking",
        inputs={"matrix": [[8.0, 5.0], [6.0, 7.0], [4.0, 9.0]]},
        parameters={
            "criterion_types": ["benefit", "benefit"],
            "weight_method": "entropy",
            "perturbation_fraction": 0.05,
            "stability_threshold": 0.5,
        },
        work_dir=tmp_path,
    )["decision"]

    assert entropy_decision["weight_method"] == "entropy"
    assert sum(entropy_decision["weights"]) == pytest.approx(1.0)
    assert all(weight > 0.0 for weight in entropy_decision["weights"])


def test_affine_ode_estimates_parameters_and_checks_conservation_and_step_size(
    tmp_path: Path,
) -> None:
    time = np.linspace(0.0, 4.0, 17)
    first_state = 8.0 * np.exp(-0.4 * time)
    states = np.column_stack([first_state, 10.0 - first_state])

    simulation = _execute(
        "dynamics.affine_ode_rk4",
        inputs={"time": time.tolist(), "states": states.tolist()},
        parameters={
            "include_offset": False,
            "lower_bounds": [0.0, 0.0],
            "upper_bounds": [10.0, 10.0],
            "conservation_vectors": [[1.0, 1.0]],
            "conservation_tolerance": 1e-6,
            "integration_substeps": 2,
            "step_sensitivity_threshold": 1e-3,
        },
        work_dir=tmp_path,
    )["simulation"]

    assert simulation["integration"]["method"] == "rk4"
    assert simulation["identifiability"]["identifiable"] is True
    assert simulation["metrics"]["rmse"] < simulation["baseline_metrics"]["rmse"]
    assert simulation["boundary_check"]["passed"] is True
    assert simulation["conservation_check"]["passed"] is True
    assert simulation["step_sensitivity"]["passed"] is True
    assert np.allclose(
        np.sum(simulation["simulated_states"], axis=1),
        10.0,
        atol=1e-6,
    )

    with pytest.raises(ValueError, match="finite lower_bounds and upper_bounds"):
        _execute(
            "dynamics.affine_ode_rk4",
            inputs={"time": time.tolist(), "states": states.tolist()},
            parameters={
                "include_offset": False,
                "conservation_vectors": [[1.0, 1.0]],
                "integration_substeps": 2,
            },
            work_dir=tmp_path,
        )


def test_graph_analysis_emits_shortest_path_flow_and_min_cut_certificates(
    tmp_path: Path,
) -> None:
    analysis = _execute(
        "graph.shortest_path_max_flow",
        inputs={
            "metrics": [
                [1.0, 3.0],
                [4.0, 2.0],
                [1.0, 1.0],
                [5.0, 2.0],
                [1.0, 3.0],
            ],
        },
        parameters={
            "node_count": 4,
            "source": 0,
            "sink": 3,
            "endpoints": [[0, 1], [0, 2], [1, 2], [1, 3], [2, 3]],
        },
        work_dir=tmp_path,
    )["analysis"]

    assert analysis["shortest_path"]["path"] == [0, 1, 2, 3]
    assert analysis["shortest_path"]["distance"] == pytest.approx(3.0)
    assert analysis["max_flow"]["value"] == pytest.approx(5.0)
    assert analysis["max_flow"]["min_cut"]["capacity"] == pytest.approx(5.0)
    assert analysis["max_flow"]["capacity_violation"] == 0.0
    assert analysis["max_flow"]["conservation_violation"] == 0.0
    assert analysis["connectivity"]["weakly_connected"] is True
    assert analysis["connectivity"]["strongly_connected"] is False


def test_artifact_projection_is_limited_to_declared_paths_and_reducers(tmp_path: Path) -> None:
    artifact = {
        "groups": {"18": {"coefficient": 0.5}},
        "metrics": {"rmse": 2.0},
        "values": [1, 2],
        "coefficients": [0.25, 0.75],
        "design": [[1.0, 2.0], [1.0, 3.0]],
        "bounds": [20.0, 170.0, 28.0],
    }
    scalar = _execute(
        "artifact.extract_scalar",
        inputs={"artifact": artifact},
        parameters={"path": "groups.18.coefficient"},
        work_dir=tmp_path,
    )["value"]
    mapping = _execute(
        "artifact.extract_mapping",
        inputs={"artifact": artifact},
        parameters={"path": "metrics"},
        work_dir=tmp_path,
    )["value"]
    projected = _execute(
        "artifact.extract_mapping",
        inputs={"artifact": artifact},
        parameters={
            "fields": {
                "coefficient": "groups.18.coefficient",
                "rmse": "metrics.rmse",
            }
        },
        work_dir=tmp_path,
    )["value"]
    vector = _execute(
        "artifact.extract_vector",
        inputs={"artifact": artifact},
        parameters={"path": "values"},
        work_dir=tmp_path,
    )["value"]
    indexed = _execute(
        "artifact.extract_scalar",
        inputs={"artifact": artifact},
        parameters={"path": "coefficients.1"},
        work_dir=tmp_path,
    )["value"]
    selected_sum = _execute(
        "artifact.extract_scalar",
        inputs={"artifact": artifact},
        parameters={"path": "coefficients", "indices": [0, 1], "reducer": "sum"},
        work_dir=tmp_path,
    )["value"]
    matrix = _execute(
        "artifact.extract_matrix",
        inputs={"artifact": artifact},
        parameters={"path": "design"},
        work_dir=tmp_path,
    )["value"]
    selected_vector = _execute(
        "artifact.extract_vector",
        inputs={"artifact": artifact},
        parameters={"path": "bounds", "indices": [0, 1]},
        work_dir=tmp_path,
    )["value"]

    assert scalar == 0.5
    assert mapping == {"rmse": 2.0}
    assert projected == {"coefficient": 0.5, "rmse": 2.0}
    assert vector == [1, 2]
    assert indexed == 0.75
    assert selected_sum == 1.0
    assert matrix == [[1.0, 2.0], [1.0, 3.0]]
    assert selected_vector == [20.0, 170.0]


def test_periodic_key_moment_design_is_derived_from_declared_structure(tmp_path: Path) -> None:
    design = _execute(
        "design.periodic_key_moments",
        inputs={},
        parameters={
            "fixed_moments": [0, 1, 3, 10, 24, 30, 37, 45, 59],
            "periodic_starts": [3],
            "period": 9,
            "window_end_offset": 4,
            "sample_count": 60,
        },
        work_dir=tmp_path,
    )["design"]

    assert design["monitoring_count"] == 20.0
    assert design["objective_value"] == 20.0
    assert design["monitoring_times"] == "0, 1, 3, 7, 10, 12, 16, 21, 24, 25, 30, 34, 37, 39, 43, 45, 48, 52, 57, 59"


def test_excel_reader_selects_sheet_and_normalizes_columns(tmp_path: Path) -> None:
    workbook = tmp_path / "data" / "observations.xlsx"
    workbook.parent.mkdir()
    pd.DataFrame({" Group ": [1, None, 2], "x": [2, 3, 4]}).to_excel(
        workbook,
        sheet_name="measurements",
        index=False,
    )

    outputs = _execute(
        "data.read_excel_table",
        inputs={"source": "data/observations.xlsx"},
        parameters={
            "sheet": "measurements",
            "columns": [" Group ", "x"],
            "rename": {" Group ": "group"},
            "forward_fill": ["group"],
            "row_limit": 2,
            "matrix_columns": ["group", "x"],
            "matrix_name": "matrix",
        },
        work_dir=tmp_path,
    )

    assert outputs["table"] == {
        "group": [1.0, 1.0],
        "x": [2, 3],
        "matrix": [[1.0, 2.0], [1.0, 3.0]],
    }


def test_grouped_zero_intercept_regression_reports_metrics_and_loo(tmp_path: Path) -> None:
    table = {
        "group": [18, 18, 20, 20],
        "force": [1.0, 2.0, 1.0, 3.0],
        "diameter": [18.0, 18.0, 20.0, 20.0],
        "torque": [9.0, 18.0, 10.0, 30.0],
    }
    outputs = _execute(
        "regression.zero_intercept_grouped",
        inputs={"table": table},
        parameters={
            "group_by": "group",
            "response": "torque",
            "predictor_product": ["force", "diameter"],
        },
        work_dir=tmp_path,
    )

    groups = outputs["fit"]["groups"]
    assert groups["18"]["coefficient"] == pytest.approx(0.5)
    assert groups["20"]["coefficient"] == pytest.approx(0.5)
    assert groups["18"]["metrics"] == {"rmse": 0.0, "max_abs_residual": 0.0}
    assert groups["18"]["leave_one_out_max_relative_change"] == pytest.approx(0.0)


def test_basis_regression_builds_hinges_and_periodic_terms(tmp_path: Path) -> None:
    x = np.arange(12, dtype=float)
    y = 3.0 + 2.0 * x + 4.0 * np.maximum(x - 5.0, 0.0)
    outputs = _execute(
        "regression.basis_least_squares",
        inputs={"x": x.tolist(), "y": y.tolist()},
        parameters={
            "basis": [
                {"kind": "constant"},
                {"kind": "identity"},
                {"kind": "hinge", "location": 5.0},
                {"kind": "sin", "period": 4.0},
                {"kind": "cos", "period": 4.0},
            ]
        },
        work_dir=tmp_path,
    )

    fit = outputs["fit"]
    assert fit["rank"] == 5
    assert fit["metrics"]["rmse"] == pytest.approx(0.0, abs=1e-12)
    assert fit["coefficients"][:3] == pytest.approx([3.0, 2.0, 4.0], abs=1e-12)


def test_continuous_hinge_bic_compares_against_linear_baseline(tmp_path: Path) -> None:
    x = np.arange(0.0, 11.0)
    y1 = 1.0 + x + 3.0 * np.maximum(x - 5.0, 0.0)
    y2 = 2.0 + 0.5 * x + 2.0 * np.maximum(x - 5.0, 0.0)
    outputs = _execute(
        "model_selection.continuous_hinge_bic",
        inputs={"x": x.tolist(), "y": np.column_stack([y1, y2]).tolist()},
        parameters={"candidates": [3.0, 4.0, 5.0, 6.0, 7.0], "support_threshold": 2.0},
        work_dir=tmp_path,
    )

    comparison = outputs["comparison"]
    assert comparison["best_breakpoint_candidate"] == 5.0
    assert comparison["breakpoint_supported"] is True
    assert comparison["bic_improvement"] > 2.0


def test_rank_and_leave_one_out_diagnostics_are_independent(tmp_path: Path) -> None:
    rank = _execute(
        "diagnostics.design_rank",
        inputs={"matrix": [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]]},
        parameters={},
        work_dir=tmp_path,
    )["diagnostic"]
    sensitivity = _execute(
        "diagnostics.leave_one_out",
        inputs={"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 6.0]},
        parameters={"zero_intercept": True},
        work_dir=tmp_path,
    )["diagnostic"]

    assert rank == {"rank": 2, "parameter_count": 2, "full_column_rank": True}
    assert sensitivity["max_relative_change"] == pytest.approx(0.0)


def test_xy_plot_returns_a_registry_ready_evidence_binding(tmp_path: Path) -> None:
    record = _execute(
        "plot.xy_evidence",
        inputs={"x": [0.0, 1.0], "observed": [1.0, 2.0], "predicted": [1.0, 2.0]},
        parameters={
            "path": "figures/fit.png",
            "title": "Observed and fitted",
            "linked_result_ids": ["result_fit"],
        },
        work_dir=tmp_path,
    )["figure"]

    assert (tmp_path / "figures" / "fit.png").is_file()
    assert record["path"] == "figures/fit.png"
    assert record["linked_result_ids"] == ["result_fit"]
