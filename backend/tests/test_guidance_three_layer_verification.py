import asyncio
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.guidance.contracts import VerificationReport


def test_unassessed_counterevidence_does_not_require_fabricated_refs() -> None:
    from app.guidance.contracts import CounterevidenceRecord

    record = CounterevidenceRecord(
        subproblem_id="problem1",
        challenged_candidate_id="alternative_route",
        status="not_assessed",
        exclusion_reason="candidate-specific comparison evidence was not produced",
        check_refs=[],
        evidence_refs=[],
        applicability_boundary="Observed sample range under the alternative assumptions.",
    )

    assert record.status == "not_assessed"
    assert record.evidence_refs == []


def test_model_adequate_pass_requires_excluded_route_counterevidence() -> None:
    with pytest.raises(ValidationError, match="counterevidence"):
        VerificationReport.model_validate(
            {
                "model_id": "compared-model",
                "execution_verified": {
                    "status": "passed",
                    "reasons": ["execution passed"],
                },
                "evidence_supported": {
                    "status": "passed",
                    "reasons": ["evidence passed"],
                },
                "model_adequate": {
                    "status": "passed",
                    "candidate_roles": ["baseline", "recommended"],
                    "checks": {"regression.baseline_comparison": "passed"},
                    "reasons": ["recommended route passed all validators"],
                },
            }
        )


def test_model_adequacy_routes_registered_validators_by_declared_family() -> None:
    from app.guidance.model_ir import ApprovedModelIR, ModelIR
    from app.guidance.stages import (
        _assess_model_adequacy,
        _build_counterevidence_records,
    )

    backend_root = Path(__file__).resolve().parents[1]
    model_ir = ModelIR.model_validate_json(
        (
            backend_root / "tests/fixtures/model_ir/wuyi_traffic_flow.json"
        ).read_text(encoding="utf-8")
    )
    approved = ApprovedModelIR(
        review_status="approved",
        model_id=model_ir.model_id,
        approved_model_ir=model_ir,
    )
    result_registry = {
        "verified_results": [
            {
                "id": "regression_validation",
                "subproblem_id": "aggregate_flow_tasks",
                "value": {
                    "observed": [1.0, 2.0, 3.0],
                    "predicted": [1.1, 1.9, 3.0],
                    "residuals": [-0.1, 0.1, 0.0],
                    "metrics": {"rmse": (0.02 / 3) ** 0.5},
                    "baseline_metrics": {"rmse": 0.5},
                    "cross_validation": {
                        "fold_rmse": [0.12, 0.14],
                        "max_allowed_rmse": 0.2,
                    },
                    "sensitivity": {
                        "max_relative_change": 0.04,
                        "threshold": 0.1,
                    },
                    "extreme_inputs": {
                        "finite": True,
                        "constraints_satisfied": True,
                    },
                },
            },
            {
                "id": "optimization_validation",
                    "subproblem_id": "aggregate_flow_tasks",
                    "value": {
                        "x": [2.0],
                        "objective_value": 8.0,
                        "baseline_objective": 10.0,
                        "constraints_satisfied": True,
                        "problem": {
                            "objective": {
                                "quadratic": [[4.0]],
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
                            "bounds": {"lower": [0.0], "upper": [3.0]},
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
                            "upper_bound": 8.0,
                            "absolute_gap": 8.0,
                            "valid": True,
                        },
                        "multistart": {
                        "objective_values": [8.0, 8.0000001],
                        "tolerance": 1e-5,
                    },
                    "sensitivity": {
                        "max_relative_change": 0.03,
                        "threshold": 0.1,
                    },
                    "extreme_inputs": {
                        "finite": True,
                        "constraints_satisfied": True,
                    },
                },
            },
        ]
    }

    layer = _assess_model_adequacy(approved, result_registry)

    assert layer["status"] == "passed"
    assert (
        layer["checks"]["aggregate_flow_tasks.regression.residual_recompute"]
        == "passed"
    )
    assert (
        layer["checks"]["aggregate_flow_tasks.optimization.multistart"]
        == "passed"
    )
    assert layer["evidence_refs"] == [
        "result_registry.json#regression_validation",
        "result_registry.json#optimization_validation",
    ]
    records = _build_counterevidence_records(approved, layer)
    assert len(records) == 1
    assert records[0]["challenged_candidate_id"] == "simple_baseline"
    assert records[0]["status"] == "excluded"
    assert records[0]["check_refs"]
    assert records[0]["evidence_refs"] == layer["evidence_refs"]
    assert records[0]["applicability_boundary"].startswith("附件给定采样区间")


def test_model_adequate_rejects_recommended_route_without_baseline() -> None:
    with pytest.raises(ValidationError, match="baseline"):
        VerificationReport.model_validate(
            {
                "model_id": "single-route-model",
                "status": "verified",
                "execution_verified": {
                    "status": "passed",
                    "reasons": ["all declared operator nodes completed"],
                },
                "evidence_supported": {
                    "status": "passed",
                    "reasons": ["registered result references available observations"],
                },
                "model_adequate": {
                    "status": "passed",
                    "candidate_roles": ["recommended"],
                    "reasons": ["recommended route has a small training residual"],
                },
            }
        )


@pytest.mark.parametrize(
    "failed_check",
    [
        "identifiability",
        "unit_consistency",
        "data_leakage",
        "constraint_satisfaction",
        "parameter_stability",
    ],
)
def test_small_residual_cannot_override_failed_adequacy_check(failed_check: str) -> None:
    checks = {
        "residual_quality": "passed",
        "identifiability": "passed",
        "unit_consistency": "passed",
        "data_leakage": "passed",
        "constraint_satisfaction": "passed",
        "parameter_stability": "passed",
        "baseline_comparison": "passed",
    }
    checks[failed_check] = "failed"

    with pytest.raises(ValidationError, match=failed_check):
        VerificationReport.model_validate(
            {
                "model_id": "low-residual-invalid-model",
                "status": "verified",
                "execution_verified": {
                    "status": "passed",
                    "reasons": ["all declared operator nodes completed"],
                },
                "evidence_supported": {
                    "status": "passed",
                    "reasons": ["training residual is below the declared threshold"],
                },
                "model_adequate": {
                    "status": "passed",
                    "candidate_roles": ["baseline", "recommended"],
                    "checks": checks,
                    "reasons": ["training residual is small"],
                },
            }
        )


def test_result_verification_consumes_operator_execution_contract(tmp_path: Path) -> None:
    from app.guidance.model_ir import ApprovedModelIR, ModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_catalog import build_default_operator_registry
    from app.guidance.operator_execution import OperatorGraphExecutor
    from app.guidance.stages import ResultVerificationStage

    backend_root = Path(__file__).resolve().parents[1]
    shutil.copytree(
        backend_root / "project/work_dir/20260629-guidance-final-answer-95dd18ea/data",
        tmp_path / "data",
    )
    model_ir = ModelIR.model_validate_json(
        (
            backend_root / "tests/fixtures/model_ir/wuyi_traffic_flow.json"
        ).read_text(encoding="utf-8")
    )
    registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=registry).compile_to_file(
        model_ir,
        output_path=tmp_path / "execution_plan.json",
    )
    assert compilation.execution_plan is not None
    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=tmp_path,
    )
    (tmp_path / "approved_model_ir.json").write_text(
        ApprovedModelIR(
            review_status="approved",
            model_id=model_ir.model_id,
            approved_model_ir=model_ir,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-three-layer",
            task={"work_dir": str(tmp_path)},
            input_path=tmp_path / "execution_manifest.json",
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert manifest.status == "passed"
    assert report["execution_verified"]["status"] == "passed"
    assert report["evidence_supported"]["status"] == "passed"
    assert report["model_adequate"]["status"] == "not_assessed"
    assert report["status"] == "partial"


def test_result_verification_consumes_failed_operator_artifacts_without_crashing(
    tmp_path: Path,
) -> None:
    from app.guidance.model_ir import ApprovedModelIR, ModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_catalog import build_default_operator_registry
    from app.guidance.operator_execution import OperatorGraphExecutor
    from app.guidance.stages import ResultVerificationStage

    backend_root = Path(__file__).resolve().parents[1]
    model_ir = ModelIR.model_validate_json(
        (
            backend_root / "tests/fixtures/model_ir/wuyi_traffic_flow.json"
        ).read_text(encoding="utf-8")
    )
    registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=registry).compile_to_file(
        model_ir,
        output_path=tmp_path / "execution_plan.json",
    )
    assert compilation.execution_plan is not None
    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model_ir,
        work_dir=tmp_path,
    )
    (tmp_path / "approved_model_ir.json").write_text(
        ApprovedModelIR(
            review_status="approved",
            model_id=model_ir.model_id,
            approved_model_ir=model_ir,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-failed-three-layer",
            task={"work_dir": str(tmp_path)},
            input_path=tmp_path / "execution_manifest.json",
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert manifest.status == "failed"
    assert manifest.failure is not None
    assert manifest.failure.error_type == "FileNotFoundError"
    assert report["execution_verified"]["status"] == "failed"
    assert report["evidence_supported"]["status"] == "blocked"
    assert report["model_adequate"]["status"] == "blocked"
    assert report["status"] == "blocked"
    assert any("operator execution status is failed" in item for item in report["blocking_issues"])
