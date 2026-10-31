import asyncio
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("coverage_status", "expected_status"),
    [("complete", "verified"), ("partial", "partial"), ("blocked", "blocked")],
)
def test_passing_layers_do_not_hide_incomplete_subproblem_coverage(
    coverage_status: str,
    expected_status: str,
):
    from app.guidance.contracts import VerificationReport

    report = VerificationReport(
        model_id="coverage-contract",
        coverage_status=coverage_status,
        execution_verified={"status": "passed", "reasons": ["Executed subset passed."]},
        evidence_supported={"status": "passed", "reasons": ["Evidence passed."]},
        model_adequate={
            "status": "passed",
            "reasons": ["Executed models passed."],
            "candidate_roles": ["baseline", "recommended"],
            "checks": {"supported.baseline_comparison": "passed"},
        },
        counterevidence_records=[{
            "subproblem_id": "supported",
            "challenged_candidate_id": "baseline",
            "status": "excluded",
            "exclusion_reason": "The recommended route passed baseline comparison.",
            "check_refs": ["supported.baseline_comparison"],
            "evidence_refs": ["result_registry.json#supported"],
            "applicability_boundary": "Registered finite models.",
        }],
    )

    assert report.status == expected_status
    assert report.execution_verified.status == "passed"
    assert report.evidence_supported.status == "passed"
    assert report.model_adequate.status == "passed"


def _mixed_model_ir() -> dict:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["subproblems"].insert(0, {
        "execution_status": "blocked",
        "subproblem_id": "unavailable_estimate",
        "objective": "Estimate an unavailable quantity.",
        "blocking_reason": "The required observations are not registered.",
        "missing_inputs": ["registered observations"],
        "recovery_action": "Register the observations and regenerate this subproblem.",
        "expected_outputs": ["estimated quantity"],
    })
    return payload


def test_model_review_derives_partial_status_from_per_subproblem_decisions():
    from app.guidance.model_ir import ModelIRReview

    review = ModelIRReview.model_validate({
        "model_id": "generic_multiperiod_inventory",
        "subproblem_reviews": [
            {
                "subproblem_id": "unavailable_estimate",
                "status": "blocked",
                "blocking_issues": ["The required observations are not registered."],
            },
            {
                "subproblem_id": "finite_inventory_policy",
                "status": "approved",
            },
        ],
        "assumption_checks": [],
    })

    assert review.status == "partial"
    assert review.executable_subproblem_ids == ["finite_inventory_policy"]


def test_calculation_executes_approved_subset_and_preserves_blocked_results(
    tmp_path: Path,
):
    from app.guidance.model_ir import ApprovedModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_catalog import build_default_operator_registry
    from app.guidance.operator_execution import OperatorGraphExecutor
    from app.guidance.stages import CalculationStage

    approved = ApprovedModelIR.model_validate({
        "review_status": "partial",
        "model_id": "generic_multiperiod_inventory",
        "model_ir_ref": "model_ir.json",
        "review_ref": "model_review.json",
        "executable_subproblem_ids": ["finite_inventory_policy"],
        "approved_model_ir": _mixed_model_ir(),
    })
    approved_path = tmp_path / "approved_model_ir.json"
    approved_path.write_text(approved.model_dump_json(indent=2), encoding="utf-8")
    registry = build_default_operator_registry()
    stage = CalculationStage(
        compiler=ModelIRCompiler(registry=registry),
        executor=OperatorGraphExecutor(registry=registry),
    )

    asyncio.run(stage.run(
        task_id="partial-execution",
        task={"work_dir": str(tmp_path)},
        input_path=approved_path,
        output_path=tmp_path / "execution_manifest.json",
    ))

    results = json.loads(
        (tmp_path / "result_registry.json").read_text(encoding="utf-8")
    )
    assert [
        item["subproblem_id"] for item in results["verified_results"]
    ] == ["finite_inventory_policy"]
    assert [
        item["subproblem_id"] for item in results["blocked_results"]
    ] == ["unavailable_estimate"]
    assert results["summary"]["coverage_status"] == "partial"


def test_verification_reports_partial_coverage_without_failing_execution(
    tmp_path: Path,
):
    from app.guidance.model_ir import ApprovedModelIR
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_catalog import build_default_operator_registry
    from app.guidance.operator_execution import OperatorGraphExecutor
    from app.guidance.stages import (
        CalculationStage,
        GuidanceStage,
        ResultVerificationStage,
    )

    approved = ApprovedModelIR.model_validate({
        "review_status": "partial",
        "model_id": "generic_multiperiod_inventory",
        "model_ir_ref": "model_ir.json",
        "review_ref": "model_review.json",
        "executable_subproblem_ids": ["finite_inventory_policy"],
        "approved_model_ir": _mixed_model_ir(),
    })
    approved_path = tmp_path / "approved_model_ir.json"
    approved_path.write_text(approved.model_dump_json(indent=2), encoding="utf-8")
    registry = build_default_operator_registry()
    manifest_path = asyncio.run(CalculationStage(
        compiler=ModelIRCompiler(registry=registry),
        executor=OperatorGraphExecutor(registry=registry),
    ).run(
        task_id="partial-verification",
        task={"work_dir": str(tmp_path)},
        input_path=approved_path,
        output_path=tmp_path / "execution_manifest.json",
    ))

    report_path = asyncio.run(ResultVerificationStage().run(
        task_id="partial-verification",
        task={"work_dir": str(tmp_path)},
        input_path=manifest_path,
        output_path=tmp_path / "verification_report.json",
    ))

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["coverage_status"] == "partial"
    assert report["execution_verified"]["status"] == "passed"
    assert report["status"] == "partial"
    assert any(
        "unavailable_estimate" in issue
        and "required observations are not registered" in issue.lower()
        for issue in report["blocking_issues"]
    )
    guidance_path = asyncio.run(GuidanceStage().run(
        task_id="partial-verification",
        task={"work_dir": str(tmp_path)},
        input_path=report_path,
        output_path=tmp_path / "guidance.md",
    ))
    guidance = guidance_path.read_text(encoding="utf-8")
    assert "流程聚合状态为 `partial`" in guidance
    assert "`policy`" in guidance
    assert "unavailable_estimate" in guidance
    assert "required observations are not registered" in guidance.lower()


def test_model_review_stage_projects_per_subproblem_decisions(
    tmp_path: Path,
):
    from app.guidance.model_ir import ModelIRReview, ModelReviewSubmission
    from app.guidance.stages import ModelReviewStage

    class PartialReviewer:
        async def review(self, *, model_ir, **kwargs):
            return ModelIRReview.model_validate({
                "model_id": model_ir.model_id,
                "subproblem_reviews": [
                    {
                        "subproblem_id": "unavailable_estimate",
                        "status": "blocked",
                        "blocking_issues": [
                            "The required observations are not registered."
                        ],
                    },
                    {
                        "subproblem_id": "finite_inventory_policy",
                        "status": "approved",
                    },
                ],
                "assumption_checks": [],
            })

    model_path = tmp_path / "model_ir.json"
    model_path.write_text(
        json.dumps(_mixed_model_ir(), ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "problem_spec.json").write_text(json.dumps({
        "task_summary": "Solve the supported subproblem.",
        "subproblems": [
            {
                "id": "unavailable_estimate",
                "required_fact_ids": [],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            },
            {
                "id": "finite_inventory_policy",
                "required_fact_ids": [],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            },
        ],
    }), encoding="utf-8")
    submission_path = tmp_path / "model_review_submission.json"
    submission_path.write_text(
        ModelReviewSubmission(model_ir_ref=model_path.name).model_dump_json(),
        encoding="utf-8",
    )

    approved_path = asyncio.run(ModelReviewStage(
        reviewer=PartialReviewer(),
    ).run(
        task_id="partial-review",
        task={"work_dir": str(tmp_path)},
        input_path=submission_path,
        output_path=tmp_path / "approved_model_ir.json",
    ))

    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    assert approved["review_status"] == "partial"
    assert approved["executable_subproblem_ids"] == ["finite_inventory_policy"]
    assert [
        item["execution_status"]
        for item in approved["approved_model_ir"]["subproblems"]
    ] == ["blocked", "solvable"]
