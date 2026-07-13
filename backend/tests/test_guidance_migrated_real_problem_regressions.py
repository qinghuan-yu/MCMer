import json
import shutil
from pathlib import Path

import pytest

from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.operator_execution import OperatorGraphExecutor


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
FIXTURE_ROOT = BACKEND_ROOT / "tests" / "fixtures"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_numeric_mapping(actual: dict, expected: dict, *, rel: float, abs_: float) -> None:
    assert set(expected) <= set(actual)
    for key, expected_value in expected.items():
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            _assert_numeric_mapping(actual_value, expected_value, rel=rel, abs_=abs_)
        elif isinstance(expected_value, (int, float)):
            assert actual_value == pytest.approx(expected_value, rel=rel, abs=abs_)
        else:
            assert actual_value == expected_value


def test_real_attachment_forecast_runs_compiled_graph_and_independent_validation(
    tmp_path: Path,
) -> None:
    from app.guidance.model_validators import build_default_model_validator_registry
    from app.guidance.operator_catalog import build_default_operator_registry

    source_work_dir = (
        REPOSITORY_ROOT
        / "backend/project/work_dir/20260629-guidance-final-answer-95dd18ea"
    )
    work_dir = tmp_path / "real-forecast"
    shutil.copytree(source_work_dir / "data", work_dir / "data")
    model_ir = ModelIR.model_validate({
        "schema_version": "1.0",
        "model_id": "real_attachment_temporal_forecast",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "table1_forecast",
            "objective": "Evaluate a robust regularized forecast on a strict temporal holdout.",
            "variables": [
                {
                    "id": "table1",
                    "role": "derived",
                    "symbol": "D",
                    "unit": "unspecified",
                    "shape": ["row", "column", "table"],
                },
            ],
            "data": [{
                "id": "workbook",
                "source_ref": "data/附件(Attachment).xlsx",
                "fields": {"observations": "表1 (Table 1)"},
            }],
            "equations": [{
                "id": "declared_identity",
                "left": {"kind": "literal", "value": 0.0},
                "right": {"kind": "literal", "value": 0.0},
            }],
            "constraints": [],
            "operators": [
                {
                    "node_id": "table1",
                    "operator_id": "data.read_excel_table",
                    "inputs": {"source": "data/附件(Attachment).xlsx"},
                    "outputs": ["table1"],
                    "parameters": {
                        "sheet": 0,
                        "columns": [
                            "时间 t (Time t)",
                            "主路3的车流量 (Traffic flow on the Main road 3)",
                        ],
                        "rename": {
                            "时间 t (Time t)": "time",
                            "主路3的车流量 (Traffic flow on the Main road 3)": "target",
                        },
                        "row_limit": 30,
                        "matrix_columns": ["time"],
                        "matrix_name": "features",
                    },
                },
                {
                    "node_id": "forecast",
                    "operator_id": "forecast.robust_regularized_time_split",
                    "inputs": {
                        "time": "table1.time",
                        "features": "table1.features",
                        "target": "table1.target",
                    },
                    "outputs": ["forecast_evaluation"],
                    "parameters": {
                        "train_size": 24,
                        "alpha": 0.001,
                        "huber_delta": 1.35,
                        "interval_level": 0.95,
                        "max_iterations": 100,
                        "tolerance": 1e-8,
                    },
                },
            ],
            "candidate_models": [
                {
                    "id": "last_observation_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["table1"],
                    "selection_metric": "temporal_holdout_rmse",
                    "applicability_boundary": "Table 1 first 30 samples under last-observation persistence.",
                },
                {
                    "id": "robust_regularized_forecast",
                    "role": "recommended",
                    "operator_node_ids": ["table1", "forecast"],
                    "selection_metric": "temporal_holdout_rmse",
                    "applicability_boundary": "Table 1 first 30 samples under a locally linear temporal trend.",
                },
            ],
            "validation": {
                "model_families": ["forecast"],
                "identifiability_checks": ["design_rank"],
                "evidence_checks": [
                    "residual_recompute",
                    "baseline_comparison",
                    "temporal_leakage",
                ],
                "robustness_checks": [
                    "temporal_holdout",
                    "prediction_interval",
                    "regularization_sensitivity",
                ],
            },
            "final_answers": [{
                "id": "forecast_holdout_evaluation",
                "item": "Table 1 temporal holdout forecast evaluation",
                "value_ref": "forecast_evaluation",
                "status": "evidence_verified",
                "evidence_ids": ["forecast"],
            }],
        }],
    })
    operator_registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=operator_registry).compile_to_file(
        model_ir,
        output_path=work_dir / "execution_plan.json",
    )
    assert compilation.status == "compiled", compilation.diagnostics
    assert compilation.execution_plan is not None
    manifest = OperatorGraphExecutor(registry=operator_registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=work_dir,
    )
    assert manifest.status == "passed"
    registry = _load_json(work_dir / "result_registry.json")
    evaluation = registry["verified_results"][0]["value"]

    assessment = build_default_model_validator_registry().assess(
        family="forecast",
        candidate_roles={"baseline", "recommended"},
        evidence=[evaluation],
    )

    assert assessment.status == "passed", assessment.reasons
    assert evaluation["leakage_check"]["passed"] is True
    assert evaluation["metrics"]["rmse"] < evaluation["baseline_metrics"]["rmse"]


def test_real_attachment_dynamics_runs_compiled_graph_and_independent_validation(
    tmp_path: Path,
) -> None:
    from app.guidance.model_validators import build_default_model_validator_registry
    from app.guidance.operator_catalog import build_default_operator_registry

    source_work_dir = (
        REPOSITORY_ROOT
        / "backend/project/work_dir/20260629-guidance-final-answer-95dd18ea"
    )
    work_dir = tmp_path / "real-dynamics"
    shutil.copytree(source_work_dir / "data", work_dir / "data")
    model_ir = ModelIR.model_validate({
        "schema_version": "1.0",
        "model_id": "real_attachment_affine_ode",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "table1_affine_dynamics",
            "objective": "Estimate and audit an affine ODE on the first 30 observed flow samples.",
            "variables": [{
                "id": "table1",
                "role": "derived",
                "symbol": "D",
                "unit": "unspecified",
                "shape": ["row", "column", "table"],
            }],
            "data": [{
                "id": "workbook",
                "source_ref": "data/附件(Attachment).xlsx",
                "fields": {"observations": "表1 (Table 1)"},
            }],
            "equations": [{
                "id": "affine_ode_assumption",
                "left": {"kind": "literal", "value": 0.0},
                "right": {"kind": "literal", "value": 0.0},
            }],
            "constraints": [],
            "operators": [
                {
                    "node_id": "table1",
                    "operator_id": "data.read_excel_table",
                    "inputs": {"source": "data/附件(Attachment).xlsx"},
                    "outputs": ["table1"],
                    "parameters": {
                        "sheet": 0,
                        "columns": [
                            "时间 t (Time t)",
                            "主路3的车流量 (Traffic flow on the Main road 3)",
                        ],
                        "rename": {
                            "时间 t (Time t)": "time",
                            "主路3的车流量 (Traffic flow on the Main road 3)": "flow",
                        },
                        "row_limit": 30,
                        "matrix_columns": ["flow"],
                        "matrix_name": "states",
                    },
                },
                {
                    "node_id": "simulate",
                    "operator_id": "dynamics.affine_ode_rk4",
                    "inputs": {"time": "table1.time", "states": "table1.states"},
                    "outputs": ["dynamics_result"],
                    "parameters": {
                        "include_offset": True,
                        "lower_bounds": [0.0],
                        "upper_bounds": [60.0],
                        "boundary_tolerance": 1e-8,
                        "conservation_vectors": [],
                        "integration_substeps": 1,
                        "step_sensitivity_threshold": 1e-8,
                    },
                },
            ],
            "candidate_models": [
                {
                    "id": "constant_state_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["table1"],
                    "selection_metric": "trajectory_rmse",
                    "applicability_boundary": "First-observation persistence on the first 30 samples.",
                },
                {
                    "id": "affine_ode_model",
                    "role": "recommended",
                    "operator_node_ids": ["table1", "simulate"],
                    "selection_metric": "trajectory_rmse",
                    "applicability_boundary": "Only the first 30 samples under a time-invariant affine ODE assumption.",
                },
            ],
            "validation": {
                "model_families": ["differential_equation"],
                "identifiability_checks": ["design_rank"],
                "evidence_checks": ["parameter_recompute", "integration_recompute"],
                "robustness_checks": ["boundary_check", "step_sensitivity"],
            },
            "final_answers": [{
                "id": "table1_dynamics_result",
                "item": "Conditional affine ODE fit on the first 30 samples",
                "value_ref": "dynamics_result",
                "status": "evidence_verified",
                "evidence_ids": ["simulate"],
            }],
        }],
    })
    operator_registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=operator_registry).compile_to_file(
        model_ir,
        output_path=work_dir / "execution_plan.json",
    )
    assert compilation.status == "compiled", compilation.diagnostics
    assert compilation.execution_plan is not None
    manifest = OperatorGraphExecutor(registry=operator_registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=work_dir,
    )
    assert manifest.status == "passed"
    result_registry = _load_json(work_dir / "result_registry.json")
    result = result_registry["verified_results"][0]["value"]

    assessment = build_default_model_validator_registry().assess(
        family="differential_equation",
        candidate_roles={"baseline", "recommended"},
        evidence=[result],
    )

    assert assessment.status == "passed", assessment.reasons
    assert result["parameter_estimate"]["matrix"][0][0] == pytest.approx(0.0, abs=1e-10)
    assert result["parameter_estimate"]["offset"][0] == pytest.approx(1.5, abs=1e-10)
    assert result["metrics"]["rmse"] == pytest.approx(0.0, abs=1e-10)
    assert result["conservation_check"]["applicable"] is False


def test_real_attachment_graph_runs_compiled_time_expanded_network_and_certificates(
    tmp_path: Path,
) -> None:
    from app.guidance.model_validators import build_default_model_validator_registry
    from app.guidance.operator_catalog import build_default_operator_registry

    source_work_dir = (
        REPOSITORY_ROOT
        / "backend/project/work_dir/20260629-guidance-final-answer-95dd18ea"
    )
    work_dir = tmp_path / "real-graph"
    shutil.copytree(source_work_dir / "data", work_dir / "data")
    model_ir = ModelIR.model_validate({
        "schema_version": "1.0",
        "model_id": "real_attachment_time_expanded_graph",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "table1_time_expanded_graph",
            "objective": "Audit a declared time-expanded graph using observed times and capacities.",
            "variables": [{
                "id": "table1",
                "role": "derived",
                "symbol": "D",
                "unit": "unspecified",
                "shape": ["row", "column", "table"],
            }],
            "data": [{
                "id": "workbook",
                "source_ref": "data/附件(Attachment).xlsx",
                "fields": {"observations": "表1 (Table 1)"},
            }],
            "equations": [{
                "id": "time_expanded_graph_assumption",
                "left": {"kind": "literal", "value": 0.0},
                "right": {"kind": "literal", "value": 0.0},
            }],
            "constraints": [],
            "operators": [
                {
                    "node_id": "table1",
                    "operator_id": "data.read_excel_table",
                    "inputs": {"source": "data/附件(Attachment).xlsx"},
                    "outputs": ["table1"],
                    "parameters": {
                        "sheet": 0,
                        "columns": [
                            "时间 t (Time t)",
                            "主路3的车流量 (Traffic flow on the Main road 3)",
                        ],
                        "rename": {
                            "时间 t (Time t)": "weight",
                            "主路3的车流量 (Traffic flow on the Main road 3)": "capacity",
                        },
                        "row_limit": 5,
                        "matrix_columns": ["weight", "capacity"],
                        "matrix_name": "edge_metrics",
                    },
                },
                {
                    "node_id": "network",
                    "operator_id": "graph.shortest_path_max_flow",
                    "inputs": {"metrics": "table1.edge_metrics"},
                    "outputs": ["graph_result"],
                    "parameters": {
                        "node_count": 6,
                        "source": 0,
                        "sink": 5,
                        "endpoints": [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5]],
                    },
                },
            ],
            "candidate_models": [
                {
                    "id": "shortest_hop_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["table1"],
                    "selection_metric": "path_distance_and_flow",
                    "applicability_boundary": "Declared five-edge time-expanded chain without weighted optimization.",
                },
                {
                    "id": "certified_weighted_network",
                    "role": "recommended",
                    "operator_node_ids": ["table1", "network"],
                    "selection_metric": "path_distance_and_flow",
                    "applicability_boundary": "Only the declared chain topology using the first five attachment rows.",
                },
            ],
            "validation": {
                "model_families": ["graph"],
                "identifiability_checks": ["connectivity_certificate"],
                "evidence_checks": ["shortest_path_certificate", "flow_certificate"],
                "robustness_checks": ["min_cut_certificate"],
            },
            "final_answers": [{
                "id": "time_expanded_graph_result",
                "item": "Conditional time-expanded graph analysis",
                "value_ref": "graph_result",
                "status": "evidence_verified",
                "evidence_ids": ["network"],
            }],
        }],
    })
    operator_registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=operator_registry).compile_to_file(
        model_ir,
        output_path=work_dir / "execution_plan.json",
    )
    assert compilation.status == "compiled", compilation.diagnostics
    assert compilation.execution_plan is not None
    manifest = OperatorGraphExecutor(registry=operator_registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=work_dir,
    )
    assert manifest.status == "passed"
    result_registry = _load_json(work_dir / "result_registry.json")
    result = result_registry["verified_results"][0]["value"]

    assessment = build_default_model_validator_registry().assess(
        family="graph",
        candidate_roles={"baseline", "recommended"},
        evidence=[result],
    )

    assert assessment.status == "passed", assessment.reasons
    assert result["shortest_path"]["distance"] == pytest.approx(10.0)
    assert result["max_flow"]["value"] == pytest.approx(7.0)
    assert result["max_flow"]["min_cut"]["capacity"] == pytest.approx(7.0)
    assert result["connectivity"]["weakly_connected"] is True


def test_real_attachment_optimization_runs_compiled_graph_and_independent_certificates(
    tmp_path: Path,
) -> None:
    from app.guidance.model_validators import build_default_model_validator_registry
    from app.guidance.operator_catalog import build_default_operator_registry

    source_work_dir = (
        REPOSITORY_ROOT
        / "backend/project/work_dir/20260712-191111-dcd758cf"
    )
    work_dir = tmp_path / "real-optimization"
    shutil.copytree(source_work_dir / "data", work_dir / "data")
    model_ir = ModelIR.model_validate({
        "schema_version": "1.0",
        "model_id": "real_attachment_constrained_optimization",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "anchor_design_optimization",
            "objective": "Minimize a quadratic anchor design objective under measured limits.",
            "variables": [{
                "id": "table4",
                "role": "derived",
                "symbol": "D",
                "unit": "unspecified",
                "shape": ["row", "column", "table"],
            }],
            "data": [{
                "id": "workbook",
                "source_ref": "data/A-附件.xlsx",
                "fields": {"limits": "Sheet4_工况参数设置"},
            }],
            "equations": [{
                "id": "declared_identity",
                "left": {"kind": "literal", "value": 0.0},
                "right": {"kind": "literal", "value": 0.0},
            }],
            "constraints": [],
            "operators": [
                {
                    "node_id": "table4",
                    "operator_id": "data.read_excel_table",
                    "inputs": {"source": "data/A-附件.xlsx"},
                    "outputs": ["table4"],
                    "parameters": {
                        "sheet": 3,
                        "columns": ["工况 A：岩层锚杆"],
                        "rename": {"工况 A：岩层锚杆": "limits"},
                        "row_limit": 2,
                    },
                },
                {
                    "node_id": "optimize",
                    "operator_id": "optimization.constrained_quadratic_multistart",
                    "inputs": {"data": "table4.limits"},
                    "outputs": ["optimization_result"],
                    "parameters": {
                        "objective": {
                            "quadratic": [[2.0, 0.0], [0.0, 2.0]],
                            "linear": [-36.0, -200.0],
                            "constant": 10324.0,
                        },
                        "linear_constraints": {
                            "a_ub": [[-1.0, -1.0]],
                            "b_ub": [-50.0],
                            "a_eq": [],
                            "b_eq": [],
                        },
                        "quadratic_constraints": [{
                            "quadratic": [[2.0, 0.0], [0.0, 2.0]],
                            "linear": [0.0, 0.0],
                            "constant": 0.0,
                            "upper_bound": 12000.0,
                        }],
                        "bounds": {
                            "lower": [0.0, 0.0],
                            "upper": ["$data.0", "$data.1"],
                        },
                        "starts": [[10.0, 100.0], [18.0, 90.0], [15.0, 100.0]],
                        "constraint_tolerance": 1e-7,
                        "multistart_tolerance": 1e-5,
                    },
                },
            ],
            "candidate_models": [
                {
                    "id": "feasible_anchor_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["table4"],
                    "selection_metric": "objective_value",
                    "applicability_boundary": "Rock-layer anchor limits from attachment sheet 4.",
                },
                {
                    "id": "constrained_quadratic_design",
                    "role": "recommended",
                    "operator_node_ids": ["table4", "optimize"],
                    "selection_metric": "objective_value",
                    "applicability_boundary": "Positive-definite quadratic objective with declared constraints.",
                },
            ],
            "validation": {
                "model_families": ["optimization"],
                "identifiability_checks": ["positive_definite_objective"],
                "evidence_checks": ["feasibility_certificate", "bound_certificate"],
                "robustness_checks": ["multistart_stability"],
            },
            "final_answers": [{
                "id": "anchor_design_result",
                "item": "Constrained anchor design result",
                "value_ref": "optimization_result",
                "status": "evidence_verified",
                "evidence_ids": ["optimize"],
            }],
        }],
    })
    operator_registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=operator_registry).compile_to_file(
        model_ir,
        output_path=work_dir / "execution_plan.json",
    )
    assert compilation.status == "compiled", compilation.diagnostics
    assert compilation.execution_plan is not None
    manifest = OperatorGraphExecutor(registry=operator_registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=work_dir,
    )
    assert manifest.status == "passed"
    registry = _load_json(work_dir / "result_registry.json")
    result = registry["verified_results"][0]["value"]

    assessment = build_default_model_validator_registry().assess(
        family="optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[result],
    )

    assert assessment.status == "passed", assessment.reasons
    assert result["x"] == pytest.approx([18.0, 100.0], abs=1e-4)
    assert result["problem"]["bounds"]["upper"] == [20.0, 170.0]
    assert result["feasibility_certificate"]["constraints_satisfied"] is True
    assert result["bound_certificate"]["lower_bound"] <= result["bound_certificate"]["upper_bound"]
    assert result["bound_certificate"]["valid"] is True


def test_real_attachment_evaluation_runs_compiled_graph_and_ranking_audit(
    tmp_path: Path,
) -> None:
    from app.guidance.model_validators import build_default_model_validator_registry
    from app.guidance.operator_catalog import build_default_operator_registry

    source_work_dir = (
        REPOSITORY_ROOT
        / "backend/project/work_dir/20260712-191111-dcd758cf"
    )
    work_dir = tmp_path / "real-evaluation"
    shutil.copytree(source_work_dir / "data", work_dir / "data")
    model_ir = ModelIR.model_validate({
        "schema_version": "1.0",
        "model_id": "real_attachment_anchor_mcda",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "anchor_torque_evaluation",
            "objective": "Rank declared torque alternatives under an explicit benefit-cost preference.",
            "variables": [{
                "id": "table1",
                "role": "derived",
                "symbol": "D",
                "unit": "unspecified",
                "shape": ["row", "column", "table"],
            }],
            "data": [{
                "id": "workbook",
                "source_ref": "data/A-附件.xlsx",
                "fields": {"alternatives": "Sheet1_标准实验数据"},
            }],
            "equations": [{
                "id": "declared_identity",
                "left": {"kind": "literal", "value": 0.0},
                "right": {"kind": "literal", "value": 0.0},
            }],
            "constraints": [],
            "operators": [
                {
                    "node_id": "table1",
                    "operator_id": "data.read_excel_table",
                    "inputs": {"source": "data/A-附件.xlsx"},
                    "outputs": ["table1"],
                    "parameters": {
                        "sheet": 0,
                        "columns": ["预紧力矩 T \n/N·m", "预紧力 P\n/kN"],
                        "rename": {
                            "预紧力矩 T \n/N·m": "torque",
                            "预紧力 P\n/kN": "preload",
                        },
                        "row_limit": 7,
                        "matrix_columns": ["preload", "torque"],
                        "matrix_name": "alternatives",
                    },
                },
                {
                    "node_id": "rank",
                    "operator_id": "decision.mcda_weighted_ranking",
                    "inputs": {"matrix": "table1.alternatives"},
                    "outputs": ["decision_result"],
                    "parameters": {
                        "criterion_types": ["benefit", "cost"],
                        "weight_method": "provided",
                        "weights": [0.55, 0.45],
                        "perturbation_fraction": 0.02,
                        "stability_threshold": 0.8,
                    },
                },
            ],
            "candidate_models": [
                {
                    "id": "unweighted_data_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["table1"],
                    "selection_metric": "rank_stability",
                    "applicability_boundary": "Raw first-seven torque alternatives without a declared preference model.",
                },
                {
                    "id": "explicit_weighted_ranking",
                    "role": "recommended",
                    "operator_node_ids": ["table1", "rank"],
                    "selection_metric": "rank_stability",
                    "applicability_boundary": "Only valid under the declared 0.55 benefit and 0.45 cost preference.",
                },
            ],
            "validation": {
                "model_families": ["evaluation"],
                "identifiability_checks": ["criterion_discrimination"],
                "evidence_checks": ["normalization_recompute", "ranking_recompute"],
                "robustness_checks": ["weight_sensitivity"],
            },
            "final_answers": [{
                "id": "anchor_torque_ranking",
                "item": "Conditional ranking of the first seven torque alternatives",
                "value_ref": "decision_result",
                "status": "evidence_verified",
                "evidence_ids": ["rank"],
            }],
        }],
    })
    operator_registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=operator_registry).compile_to_file(
        model_ir,
        output_path=work_dir / "execution_plan.json",
    )
    assert compilation.status == "compiled", compilation.diagnostics
    assert compilation.execution_plan is not None
    manifest = OperatorGraphExecutor(registry=operator_registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=work_dir,
    )
    assert manifest.status == "passed"
    result_registry = _load_json(work_dir / "result_registry.json")
    result = result_registry["verified_results"][0]["value"]

    assessment = build_default_model_validator_registry().assess(
        family="evaluation",
        candidate_roles={"baseline", "recommended"},
        evidence=[result],
    )

    assert assessment.status == "passed", assessment.reasons
    assert result["decision_matrix"][0] == [12.68, 50.0]
    assert result["weights"] == [0.55, 0.45]
    assert result["ranking"] == [2, 4, 6, 5, 3, 1, 0]
    assert result["weight_perturbation"]["stable"] is True


@pytest.mark.parametrize(
    "case",
    _load_json(FIXTURE_ROOT / "guidance_migration_baseline.json")["cases"],
    ids=lambda case: case["id"],
)
def test_real_problem_model_ir_preserves_migration_baseline(case: dict, tmp_path: Path) -> None:
    from app.guidance.operator_catalog import build_default_operator_registry

    source_work_dir = REPOSITORY_ROOT / case["source_work_dir"]
    work_dir = tmp_path / case["id"]
    shutil.copytree(source_work_dir / "data", work_dir / "data")

    model_ir = ModelIR.model_validate_json(
        (FIXTURE_ROOT / "model_ir" / f"{case['id']}.json").read_text(encoding="utf-8")
    )
    registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=registry).compile_to_file(
        model_ir,
        output_path=work_dir / "execution_plan.json",
    )

    assert compilation.status == "compiled", compilation.diagnostics
    assert compilation.execution_plan is not None
    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=work_dir,
    )
    assert manifest.status == "passed"

    parameter_registry = _load_json(work_dir / "parameter_registry.json")
    result_registry = _load_json(work_dir / "result_registry.json")
    figure_registry = _load_json(work_dir / "figure_registry.json")
    parameters = {
        item["parameter_id"]: item["value"]
        for item in parameter_registry["parameters"]
    }
    results = {
        item["id"]: item["value"]
        for item in result_registry["verified_results"]
    }
    blocked_result_ids = {
        item["id"] for item in result_registry["blocked_results"]
    }
    figure_bindings = {
        item["path"]: item["linked_result_ids"]
        for item in figure_registry["figures"]
    }
    tolerance = case["numeric_tolerance"]

    _assert_numeric_mapping(
        parameters,
        case["parameters"],
        rel=tolerance["relative"],
        abs_=tolerance["absolute"],
    )
    _assert_numeric_mapping(
        results,
        case["results"],
        rel=tolerance["relative"],
        abs_=tolerance["absolute"],
    )
    assert blocked_result_ids == set(case["blocked_result_ids"])
    assert figure_bindings == case["figure_bindings"]
