import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from app.guidance.artifacts import (
    ParameterRegistry,
    ResultRegistry,
    reader_guidance_reference_issues,
)
from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.model_validators import build_default_model_validator_registry
from app.guidance.operator_catalog import build_default_operator_registry
from app.guidance.operator_execution import OperatorGraphExecutor
from app.guidance.renderer import render_verified_guidance


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
BLIND_IDS = {
    "blind_orchid_forecast_271",
    "blind_quartz_optimization_314",
    "blind_lantern_evaluation_159",
    "blind_cobalt_dynamics_265",
    "blind_saffron_graph_358",
}


def _case_model(case: dict) -> ModelIR:
    return ModelIR.model_validate({
        "schema_version": "1.0",
        "model_id": case["id"],
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": f'{case["id"]}_subproblem',
            "objective": "Execute an unseen cross-family benchmark through the common graph.",
            "variables": [{
                "id": "table",
                "role": "derived",
                "symbol": "D",
                "unit": "unspecified",
                "shape": ["row", "column", "table"],
            }],
            "data": [{
                "id": "blind_workbook",
                "source_ref": "data/blind_inputs.xlsx",
                "fields": {"observations": case["sheet"]},
            }],
            "equations": [{
                "id": "declared_assumption",
                "left": {"kind": "literal", "value": 0.0},
                "right": {"kind": "literal", "value": 0.0},
            }],
            "constraints": [],
            "operators": [
                {
                    "node_id": "table",
                    "operator_id": "data.read_excel_table",
                    "inputs": {"source": "data/blind_inputs.xlsx"},
                    "outputs": ["table"],
                    "parameters": case["table_parameters"],
                },
                {
                    "node_id": "solve",
                    "operator_id": case["operator_id"],
                    "inputs": case["inputs"],
                    "outputs": ["answer"],
                    "parameters": case["parameters"],
                },
            ],
            "candidate_models": [
                {
                    "id": "declared_baseline",
                    "role": "baseline",
                    "operator_node_ids": ["table"],
                    "selection_metric": "family_specific_certificate",
                    "applicability_boundary": "Blind benchmark baseline only.",
                },
                {
                    "id": "compiled_recommendation",
                    "role": "recommended",
                    "operator_node_ids": ["table", "solve"],
                    "selection_metric": "family_specific_certificate",
                    "applicability_boundary": "Blind benchmark data and declared assumptions only.",
                },
            ],
            "validation": {
                "model_families": [case["family"]],
                "identifiability_checks": ["declared_rank_or_structure"],
                "evidence_checks": ["independent_recompute"],
                "robustness_checks": ["family_specific_sensitivity"],
            },
            "final_answers": [{
                "id": "blind_answer",
                "item": "Blind benchmark result",
                "value_ref": "answer",
                "status": "evidence_verified",
                "evidence_ids": ["solve"],
            }],
        }],
    })


def _blind_cases() -> list[dict]:
    return [
        {
            "id": "blind_orchid_forecast_271",
            "sheet": "forecast",
            "family": "forecast",
            "operator_id": "forecast.robust_regularized_time_split",
            "table_parameters": {
                "sheet": "forecast",
                "matrix_columns": ["feature"],
                "matrix_name": "features",
            },
            "inputs": {
                "time": "table.time",
                "features": "table.features",
                "target": "table.target",
            },
            "parameters": {
                "train_size": 15,
                "alpha": 0.001,
                "huber_delta": 1.35,
                "interval_level": 0.95,
                "max_iterations": 100,
                "tolerance": 1e-8,
            },
        },
        {
            "id": "blind_quartz_optimization_314",
            "sheet": "optimization",
            "family": "optimization",
            "operator_id": "optimization.constrained_quadratic_multistart",
            "table_parameters": {"sheet": "optimization"},
            "inputs": {"data": "table.limit"},
            "parameters": {
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
                "quadratic_constraints": [],
                "bounds": {"lower": [0.0, 0.0], "upper": ["$data.0", "$data.1"]},
                "starts": [[0.0, 2.0], [1.0, 1.0], [2.0, 0.0]],
                "constraint_tolerance": 1e-7,
                "multistart_tolerance": 1e-6,
            },
        },
        {
            "id": "blind_lantern_evaluation_159",
            "sheet": "evaluation",
            "family": "evaluation",
            "operator_id": "decision.mcda_weighted_ranking",
            "table_parameters": {
                "sheet": "evaluation",
                "matrix_columns": ["benefit", "cost"],
                "matrix_name": "alternatives",
            },
            "inputs": {"matrix": "table.alternatives"},
            "parameters": {
                "criterion_types": ["benefit", "cost"],
                "weight_method": "provided",
                "weights": [0.7, 0.3],
                "perturbation_fraction": 0.05,
                "stability_threshold": 0.8,
            },
        },
        {
            "id": "blind_cobalt_dynamics_265",
            "sheet": "dynamics",
            "family": "differential_equation",
            "operator_id": "dynamics.affine_ode_rk4",
            "table_parameters": {
                "sheet": "dynamics",
                "matrix_columns": ["state"],
                "matrix_name": "states",
            },
            "inputs": {"time": "table.time", "states": "table.states"},
            "parameters": {
                "include_offset": True,
                "lower_bounds": [0.0],
                "upper_bounds": [30.0],
                "conservation_vectors": [],
                "integration_substeps": 1,
                "step_sensitivity_threshold": 1e-8,
            },
        },
        {
            "id": "blind_saffron_graph_358",
            "sheet": "graph",
            "family": "graph",
            "operator_id": "graph.shortest_path_max_flow",
            "table_parameters": {
                "sheet": "graph",
                "matrix_columns": ["weight", "capacity"],
                "matrix_name": "metrics",
            },
            "inputs": {"metrics": "table.metrics"},
            "parameters": {
                "node_count": 4,
                "source": 0,
                "sink": 3,
                "endpoints": [[0, 1], [0, 2], [1, 2], [1, 3], [2, 3]],
            },
        },
    ]


def _reviewed_result_registry(case: dict, assessment) -> ResultRegistry:
    result_id = "blind_answer"
    claim_id = f'{case["id"]}_claim'
    boundary = "Blind benchmark data and declared assumptions only."
    topics = [
        "problem_context",
        "identifiability",
        "route_choice",
        "final_answers",
        "claim_sources",
    ]
    return ResultRegistry.model_validate({
        "verified_results": [{
            "id": result_id,
            "result_type": case["family"],
            "claim_text": f'{case["id"]} produced independently checked structured evidence.',
            "metrics": {"passed_check_count": float(len(assessment.checks))},
            "source_data": ["data/blind_inputs.xlsx"],
        }],
        "blocked_results": [],
        "summary": {
            "verified_count": 1,
            "blocked_count": 0,
            "coverage_status": "verified",
            "guidance_notes": [boundary],
            "reader_decision_guide": [{
                "topic": topic,
                "question": f'What must the reader decide about {topic} for {case["id"]}?',
                "judgment": f'{case["family"]} evidence passed independent checks.',
                "action": f'Use {result_id}, retain the declared assumptions, and cite the registered evidence.',
            } for topic in topics],
            "method_route_comparison": [
                {
                    "route": case["operator_id"],
                    "status": "recommended",
                    "reason": "The compiled route produced independently verified numerical evidence.",
                    "boundary": boundary,
                    "evidence_ids": [result_id],
                },
                {
                    "route": "unverified narrative-only route",
                    "status": "rejected",
                    "reason": "It has no executable result or independent certificate.",
                    "boundary": "Rejected for this benchmark unless executable evidence is added.",
                    "evidence_ids": [result_id],
                },
            ],
            "identifiability_analysis": [{
                "subproblem_id": f'{case["id"]}_subproblem',
                "observed": f'Workbook sheet {case["sheet"]}.',
                "unknowns": "The family-specific parameters and requested result.",
                "equation": f'Compiled operator {case["operator_id"]}.',
                "rank_condition": "The family-specific independent validator must pass.",
                "conclusion": "Identifiable only under the declared benchmark structure.",
                "missing_information": "No claim is made outside the declared data and assumptions.",
                "selection_rule": "Prefer the executable route over the narrative-only baseline.",
                "guidance": "Report the registered result together with its applicability boundary.",
            }],
            "claim_registry": [{
                "id": claim_id,
                "status": "evidence_verified",
                "claim_text": f'{case["family"]} result passed its independent validators.',
                "evidence_ids": [result_id],
                "assumptions": [boundary],
                "risk": "The conclusion does not transfer beyond the declared structure without revalidation.",
            }],
            "final_answer_tables": [{
                "id": f'{case["id"]}_answers',
                "title": "Blind benchmark final answer",
                "rows": [{
                    "subproblem_id": f'{case["id"]}_subproblem',
                    "answer_item": "Registered structured result",
                    "answer": f'See {result_id}.',
                    "status": "evidence_verified",
                    "evidence_ids": [result_id],
                }],
                "notes": [boundary],
            }],
        },
    })


def test_unseen_cross_family_cases_use_only_common_model_ir_and_operators(tmp_path: Path) -> None:
    time = np.arange(20, dtype=float)
    workbook = tmp_path / "blind_inputs.xlsx"
    with pd.ExcelWriter(workbook) as writer:
        pd.DataFrame({"time": time, "feature": time, "target": 2.0 + 3.0 * time}).to_excel(
            writer, sheet_name="forecast", index=False
        )
        pd.DataFrame({"limit": [3.0, 3.0]}).to_excel(
            writer, sheet_name="optimization", index=False
        )
        pd.DataFrame({
            "benefit": [90.0, 80.0, 70.0],
            "cost": [8.0, 5.0, 3.0],
        }).to_excel(writer, sheet_name="evaluation", index=False)
        pd.DataFrame({"time": time[:10], "state": 1.0 + 2.0 * time[:10]}).to_excel(
            writer, sheet_name="dynamics", index=False
        )
        pd.DataFrame({
            "weight": [1.0, 4.0, 1.0, 5.0, 1.0],
            "capacity": [3.0, 2.0, 1.0, 2.0, 3.0],
        }).to_excel(writer, sheet_name="graph", index=False)

    production_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in APP_ROOT.rglob("*.py")
    )
    assert all(case_id not in production_text for case_id in BLIND_IDS)

    operator_registry = build_default_operator_registry()
    validator_registry = build_default_model_validator_registry()
    seen_families = set()
    for case in _blind_cases():
        work_dir = tmp_path / case["id"]
        (work_dir / "data").mkdir(parents=True)
        shutil.copy2(workbook, work_dir / "data" / "blind_inputs.xlsx")
        model_ir = _case_model(case)
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
        registry = json.loads((work_dir / "result_registry.json").read_text(encoding="utf-8"))
        result = registry["verified_results"][0]["value"]
        assessment = validator_registry.assess(
            family=case["family"],
            candidate_roles={"baseline", "recommended"},
            evidence=[result],
        )
        assert assessment.status == "passed", (case["id"], assessment.reasons)
        reviewed_registry = _reviewed_result_registry(case, assessment)
        parameter_registry = ParameterRegistry(parameters=[])
        assert reader_guidance_reference_issues(reviewed_registry, parameter_registry) == []
        guidance = render_verified_guidance(
            approved_model={
                "review_status": "approved",
                "approved_model_ir": model_ir.model_dump(mode="json"),
            },
            verification_report={
                "status": "verified",
                "execution_status": "passed",
                "evidence_status": "verified",
                "model_adequacy_status": "passed",
            },
            parameter_registry=parameter_registry.model_dump(mode="json"),
            result_registry=reviewed_registry.model_dump(mode="json"),
        )
        for section in (
            "## 问题理解",
            "## 建模路线与读者决策",
            "## 建模路线取舍",
            "## 可辨识性分析",
            "## 结论状态登记",
            "## 最终答案与应填表格",
            "## 分问题建模步骤",
            "## 必要计算结果",
            "## 复核与稳健性",
        ):
            assert section in guidance, (case["id"], section)
        assert "retain the declared assumptions" in guidance
        assert "Blind benchmark data and declared assumptions only." in guidance
        seen_families.add(case["family"])

    assert seen_families == {
        "forecast",
        "optimization",
        "evaluation",
        "differential_equation",
        "graph",
    }


def test_cross_family_numerical_faults_write_honest_failed_artifacts(tmp_path: Path) -> None:
    time = np.arange(5, dtype=float)
    workbook = tmp_path / "fault_inputs.xlsx"
    with pd.ExcelWriter(workbook) as writer:
        pd.DataFrame({"time": time, "state": np.ones(len(time))}).to_excel(
            writer, sheet_name="unidentifiable", index=False
        )
        pd.DataFrame({"limit": [1.0, 1.0]}).to_excel(
            writer, sheet_name="infeasible", index=False
        )
        pd.DataFrame({
            "time": time,
            "state": 1.0 + 1e-10 * time,
        }).to_excel(writer, sheet_name="ill_conditioned", index=False)

    cases = [
        {
            "id": "blind_unidentifiable_fault_979",
            "sheet": "unidentifiable",
            "family": "differential_equation",
            "operator_id": "dynamics.affine_ode_rk4",
            "table_parameters": {
                "sheet": "unidentifiable",
                "matrix_columns": ["state"],
                "matrix_name": "states",
            },
            "inputs": {"time": "table.time", "states": "table.states"},
            "parameters": {
                "include_offset": True,
                "lower_bounds": [0.0],
                "upper_bounds": [2.0],
                "conservation_vectors": [],
                "integration_substeps": 1,
            },
            "reason": "rank deficient",
        },
        {
            "id": "blind_infeasible_fault_323",
            "sheet": "infeasible",
            "family": "optimization",
            "operator_id": "optimization.constrained_quadratic_multistart",
            "table_parameters": {"sheet": "infeasible"},
            "inputs": {"data": "table.limit"},
            "parameters": {
                "objective": {
                    "quadratic": [[2.0, 0.0], [0.0, 2.0]],
                    "linear": [0.0, 0.0],
                    "constant": 0.0,
                },
                "linear_constraints": {
                    "a_ub": [[-1.0, -1.0]],
                    "b_ub": [-3.0],
                    "a_eq": [],
                    "b_eq": [],
                },
                "quadratic_constraints": [],
                "bounds": {"lower": [0.0, 0.0], "upper": ["$data.0", "$data.1"]},
                "starts": [[0.0, 0.0], [1.0, 1.0]],
                "constraint_tolerance": 1e-7,
                "multistart_tolerance": 1e-6,
            },
            "reason": "feasible baseline",
        },
        {
            "id": "blind_ill_conditioned_fault_846",
            "sheet": "ill_conditioned",
            "family": "differential_equation",
            "operator_id": "dynamics.affine_ode_rk4",
            "table_parameters": {
                "sheet": "ill_conditioned",
                "matrix_columns": ["state"],
                "matrix_name": "states",
            },
            "inputs": {"time": "table.time", "states": "table.states"},
            "parameters": {
                "include_offset": True,
                "lower_bounds": [0.0],
                "upper_bounds": [2.0],
                "conservation_vectors": [],
                "integration_substeps": 1,
                "max_condition_number": 1e8,
            },
            "reason": "ill-conditioned",
        },
    ]

    registry = build_default_operator_registry()
    for case in cases:
        work_dir = tmp_path / case["id"]
        (work_dir / "data").mkdir(parents=True)
        shutil.copy2(workbook, work_dir / "data" / "blind_inputs.xlsx")
        model_ir = _case_model(case)
        compilation = ModelIRCompiler(registry=registry).compile_to_file(
            model_ir,
            output_path=work_dir / "execution_plan.json",
        )
        assert compilation.status == "compiled", compilation.diagnostics
        manifest = OperatorGraphExecutor(registry=registry).execute(
            execution_plan=compilation.execution_plan,
            model_ir=model_ir,
            work_dir=work_dir,
        )

        assert manifest.status == "failed", case["id"]
        assert manifest.failure is not None
        assert manifest.failure.node_id == "solve"
        results = json.loads((work_dir / "result_registry.json").read_text(encoding="utf-8"))
        assert results["verified_results"] == []
        assert results["summary"]["coverage_status"] == "blocked"
        assert case["reason"] in results["blocked_results"][0]["reason"]
        assert results["blocked_results"][0]["recovery_action"]
