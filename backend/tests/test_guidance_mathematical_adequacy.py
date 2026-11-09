import asyncio
import json
from pathlib import Path

import pytest

from app.guidance.contracts import ModelAdequacyLayer, ProblemSpec, VerificationReport
from app.guidance.guidance_rendering.sections import (
    _verification_layer_rows,
    _verification_layer_summaries,
)
from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.model_ir_semantics import validate_model_fact_coverage
from app.guidance.model_review_contracts import ApprovedModelIR, ModelIRReview
from app.guidance.model_validators import build_default_model_validator_registry
from app.guidance.operator_catalog import build_default_operator_registry
from app.guidance.operator_execution import OperatorGraphExecutor
from app.guidance.verification_stage import ResultVerificationStage


def _inventory_case(mutation: str) -> tuple[ProblemSpec, ModelIR]:
    fixture = Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    subproblem = payload["subproblems"][0]
    subproblem["final_answers"][0]["evidence_ids"] = ["solve"]
    model = subproblem["execution_components"][0]["model"]
    if mutation == "missing_balance":
        model["constraints"] = [
            item for item in model["constraints"]
            if item["id"] not in {"initial_balance", "state_balance"}
        ]
    if mutation == "missing_activity":
        model["constraints"] = [
            item for item in model["constraints"] if item["id"] != "setup_capacity"
        ]
    delay_index = next(
        index for index, item in enumerate(model["constraints"])
        if item["id"] == "commissioning_delay"
    )
    if mutation == "zero_delay":
        model["constraints"][delay_index]["terms"][1]["variable"]["selectors"][0]["offset"] = 0

    # 覆盖只要求已有节点引用；初始库存参数的引用不能证明状态平衡存在。
    subproblem["fact_bindings"] = [
        {"fact_id": "stock_balance", "model_paths": ["/execution_components/0/model/parameters/3"]},
        {"fact_id": "delivery_delay", "model_paths": [f"/execution_components/0/model/constraints/{delay_index}"]},
        {"fact_id": "setup_activity", "model_paths": ["/execution_components/0/model/parameters/9"]},
    ]
    facts = [
        ("stock_balance", "Stock is conserved from an initial inventory of five, with demand, loss and maintenance deducted.", 5, "items"),
        ("delivery_delay", "Purchases become available exactly one period later.", 1, "period"),
        ("setup_activity", "Each purchase requires a paid setup activity with capacity twenty.", 20, "items"),
    ]
    spec = ProblemSpec(
        task_summary="Finite inventory with stock conservation and one-period delivery.",
        subproblems=[{
            "id": subproblem["subproblem_id"],
            "required_fact_ids": [item[0] for item in facts],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        }],
        locked_facts=[{
            "fact_id": fact_id,
            "statement": statement,
            "semantic_key": fact_id,
            "value": value,
            "unit": unit,
            "source_refs": [f"input_catalog.json#document/{fact_id}"],
            "subproblem_ids": [subproblem["subproblem_id"]],
        } for fact_id, statement, value, unit in facts],
    )
    return spec, ModelIR.model_validate(payload)


def _execute_case(root: Path, mutation: str) -> tuple[dict, dict]:
    root.mkdir(parents=True, exist_ok=True)
    spec, model = _inventory_case(mutation)
    validate_model_fact_coverage(spec, model)
    registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=registry).compile_to_file(
        model, output_path=root / "execution_plan.json",
    )
    assert compilation.status == "compiled", compilation.diagnostics
    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan,
        model_ir=model,
        work_dir=root,
    )
    assert manifest.status == "passed", manifest.failure
    results = json.loads((root / "result_registry.json").read_text(encoding="utf-8"))
    solution = results["verified_results"][0]["value"]
    numerical = build_default_model_validator_registry().assess(
        family="mixed_integer_linear_optimization",
        candidate_roles={"baseline", "recommended"},
        evidence=[solution],
    )
    assert numerical.status == "passed", numerical.reasons
    assert all(check.status == "passed" for check in numerical.checks.values())
    subproblem_id = model.subproblems[0].subproblem_id
    artifacts = {
        "problem_spec.json": spec,
        "model_ir.json": model,
        "model_review.json": ModelIRReview(
            model_id=model.model_id,
            subproblem_reviews=[{"subproblem_id": subproblem_id, "status": "approved", "assumption_checks": []}],
        ),
        "approved_model_ir.json": ApprovedModelIR(
            review_status="approved",
            model_id=model.model_id,
            executable_subproblem_ids=[subproblem_id],
            approved_model_ir=model,
        ),
    }
    for name, artifact in artifacts.items():
        (root / name).write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    output = asyncio.run(ResultVerificationStage().run(
        task_id=f"adequacy-{mutation}",
        task={"work_dir": str(root)},
        input_path=root / "execution_manifest.json",
        output_path=root / "verification_report.json",
    ))
    return json.loads(output.read_text(encoding="utf-8")), solution


@pytest.mark.parametrize("mutation", ["original", "missing_balance", "zero_delay", "missing_activity"])
def test_coverage_review_approval_and_numerics_do_not_prove_mathematical_adequacy(
    tmp_path: Path, mutation: str,
) -> None:
    report, solution = _execute_case(tmp_path, mutation)
    decisions = solution["decision_values"]
    if mutation == "original":
        assert solution["objective_value"] == pytest.approx(26.0)
    elif mutation == "missing_balance":
        assert solution["objective_value"] == pytest.approx(0.0)
        assert decisions["inventory[p0]"] != pytest.approx(5 - 2 - 1)
    elif mutation == "zero_delay":
        assert any(
            abs(decisions[f"commissioned[p{period}]"] - decisions[f"purchase[p{period - 1}]"]) > 1e-7
            for period in (1, 2)
        )
    else:
        assert any(
            decisions[f"purchase[p{period}]"] > 0 and decisions[f"setup[p{period}]"] == 0
            for period in range(3)
        )

    assert report["execution_verified"]["status"] == "passed"
    assert report["evidence_supported"]["status"] == "passed"
    assert report["model_adequate"]["status"] == "not_assessed", report
    assert report["model_adequate"]["source_coverage"]["status"] == "passed"
    assert report["model_adequate"]["numerical_validation"]["status"] == "passed"
    assert report["model_adequate"]["mathematical_review"]["status"] == "not_assessed"
    assert report["status"] == "partial"
    assert all(item["status"] != "excluded" for item in report["counterevidence_records"])


@pytest.mark.parametrize("layer_name", ["source_coverage", "mathematical_review", "numerical_validation"])
@pytest.mark.parametrize("status", ["not_assessed", "failed", "blocked"])
def test_claimed_pass_cannot_override_unassessed_or_failed_layer(layer_name: str, status: str) -> None:
    layers = {
        name: {"status": "passed", "reasons": [f"{name} checked."]}
        for name in ("source_coverage", "mathematical_review", "numerical_validation")
    }
    layers["numerical_validation"].update({
        "candidate_roles": ["baseline", "recommended"],
        "checks": {"case.baseline_comparison": "passed"},
    })
    layers[layer_name]["status"] = status
    adequacy = ModelAdequacyLayer(status="passed", **layers)
    report = VerificationReport(
        model_id="unjustified-pass",
        status="verified",
        execution_verified={"status": "passed", "reasons": ["Execution completed."]},
        evidence_supported={"status": "passed", "reasons": ["Results trace to execution."]},
        model_adequate=adequacy,
    )
    assert report.model_adequate.status == status
    assert report.status != "verified"


def test_guidance_displays_mathematical_review_separately_from_numerics(tmp_path: Path) -> None:
    report, _ = _execute_case(tmp_path, "missing_balance")
    rows = "\n".join(_verification_layer_rows(report))
    summaries = "\n".join(_verification_layer_summaries(report))
    assert "| 题面事实覆盖 | `passed` |" in rows
    assert "| 数学对应审核 | `not_assessed` |" in rows
    assert "| 数值复核 | `passed` |" in rows
    assert "数学对应审核：`not_assessed`" in summaries
    assert "数值复核：`passed`" in summaries


@pytest.mark.parametrize("tamper", ["missing_fact", "unknown_path", "wrong_subproblem"])
def test_source_coverage_failure_remains_separate_from_numerical_success(
    tmp_path: Path, tamper: str,
) -> None:
    _execute_case(tmp_path, "original")
    approved_path = tmp_path / "approved_model_ir.json"
    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    bindings = approved["approved_model_ir"]["subproblems"][0]["fact_bindings"]
    if tamper == "missing_fact":
        bindings.pop()
    elif tamper == "unknown_path":
        bindings[0]["model_paths"] = ["/execution_components/99"]
    else:
        problem_path = tmp_path / "problem_spec.json"
        spec = json.loads(problem_path.read_text(encoding="utf-8"))
        spec["subproblems"][0]["id"] = "another_problem"
        for fact in spec["locked_facts"]:
            fact["subproblem_ids"] = ["another_problem"]
        problem_path.write_text(json.dumps(spec), encoding="utf-8")
    approved_path.write_text(json.dumps(approved), encoding="utf-8")
    # 本测试隔离来源覆盖层；改变模型后重新执行，旧执行证据另由恢复测试拒绝。
    model = ModelIR.model_validate(approved["approved_model_ir"])
    registry = build_default_operator_registry()
    compilation = ModelIRCompiler(registry=registry).compile_to_file(
        model, output_path=tmp_path / "execution_plan.json",
    )
    OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan, model_ir=model, work_dir=tmp_path,
    )
    output = asyncio.run(ResultVerificationStage().run(
        task_id="coverage-tamper", task={},
        input_path=tmp_path / "execution_manifest.json",
        output_path=tmp_path / "verification_report.json",
    ))
    report = VerificationReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert report.model_adequate.source_coverage.status == "failed"
    assert report.model_adequate.numerical_validation.status == "passed"
    assert report.model_adequate.status == "failed"
    assert report.status == "partial"
