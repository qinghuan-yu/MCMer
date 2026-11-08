"""Guidance 职责边界：guidance_stage。"""

from __future__ import annotations

import json
from pathlib import Path

from app.artifacts.guidance_audit import build_guidance_audit_report
from app.guidance.contracts import VerificationReport
from app.guidance.model_review_contracts import ApprovedModelIR
from app.guidance.renderer import render_verified_guidance
from app.guidance.stage_io import (
    _read_json,
    _read_json_if_exists,
    _required_input,
    _write_model,
)


class GuidanceStage:
    """Render guidance only from a verification report and its artifact references."""

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        source_path = _required_input(input_path)
        root = output_path.parent
        problem_spec = _read_json_if_exists(root / "problem_spec.json")
        if source_path.name == "approved_model_ir.json":
            report, approved, parameters, results = _build_blocked_guidance_artifacts(
                root,
                source_path,
            )
        else:
            report = VerificationReport.model_validate_json(
                source_path.read_text(encoding="utf-8")
            )
            refs = report.artifact_refs
            approved = _read_json(root / str(refs["approved_model"]))
            parameters = _read_json_if_exists(root / str(refs["parameter_registry"]))
            results = _read_json_if_exists(root / str(refs["result_registry"]))
            execution_manifest = _read_json_if_exists(
                root / str(refs.get("execution_manifest", "execution_manifest.json"))
            )
            calculation_failure = _read_json_if_exists(root / "calculation_failure.json")
            report = _enforce_verified_guidance_evidence(
                report,
                approved_model=approved,
                parameter_registry=parameters,
                result_registry=results,
            )
        if source_path.name == "approved_model_ir.json":
            execution_manifest = {}
            calculation_failure = {}
        guidance = render_verified_guidance(
            approved_model=approved,
            verification_report=report.model_dump(mode="json"),
            parameter_registry=parameters,
            result_registry=results,
            execution_manifest=execution_manifest,
            calculation_failure=calculation_failure,
            problem_spec=problem_spec,
        )
        context = {
            "artifact_type": "guidance",
            "required_sections": _required_guidance_sections(guidance),
        }
        audit = build_guidance_audit_report(
            guidance_markdown=guidance,
            guidance_context=context,
            result_registry=results,
            parameter_registry=parameters,
            figure_registry={
                "figures": report.artifact_refs.get("figures", []),
            },
            artifact_root=root,
        )
        output_path.write_text(guidance, encoding="utf-8")
        (root / "res.md").write_text(guidance, encoding="utf-8")
        (root / "guidance_context.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "guidance_audit_report.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return output_path

def _required_guidance_sections(guidance: str) -> list[str]:
    if (
        guidance.startswith("# 计算链路阻断诊断")
        or guidance.startswith("# 建模链路阻断诊断")
        or guidance.startswith("# 结果复核阻断诊断")
    ):
        return [
            "可信度摘要",
            "阻断位置",
            "阻断原因",
            "复核检查概览" if guidance.startswith("# 结果复核阻断诊断") else "",
            "下一步修复动作",
            "可复现附件说明",
        ]
    return [
        "可信度摘要",
        "问题理解",
        "数据与来源",
        "参数与来源表",
        "模型选择理由",
        "建模路线与读者决策",
        "建模路线取舍",
        "可辨识性分析",
        "结论状态登记",
        "分问题建模步骤",
        "必要计算结果",
        "最终答案与应填表格",
        "复核与稳健性",
        "阻断项与待确认项",
        "论文转化建议",
        "可复现附件说明",
    ]

def _enforce_verified_guidance_evidence(
    report: VerificationReport,
    *,
    approved_model: dict,
    parameter_registry: dict,
    result_registry: dict,
) -> VerificationReport:
    if report.status != "verified":
        return report

    issues: list[str] = []
    parameters = parameter_registry.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        issues.append("verified guidance evidence missing: parameter evidence")
    verified_results = result_registry.get("verified_results")
    if not isinstance(verified_results, list) or not verified_results:
        issues.append("verified guidance evidence missing: numerical results")

    check_groups = {
        "input checks": report.input_checks,
        "equation checks": report.equation_checks,
        "unit checks": report.unit_checks,
        "constraint checks": report.constraint_checks,
        "residual checks": report.residual_checks,
        "sensitivity checks": report.sensitivity_checks,
    }
    for label, checks in check_groups.items():
        if not checks:
            issues.append(f"verified guidance evidence missing: {label}")
        elif any(str(check.get("status")) != "passed" for check in checks):
            issues.append(f"verified guidance evidence failed: {label}")

    model = approved_model.get("approved_model")
    model = model if isinstance(model, dict) else {}
    subproblems = model.get("subproblem_models")
    if not isinstance(subproblems, list) or not subproblems:
        issues.append("verified guidance evidence missing: subproblem modeling details")
    else:
        for subproblem in subproblems:
            if not _has_complete_subproblem_guidance_details(subproblem):
                issues.append("verified guidance evidence incomplete: subproblem modeling details")
                break

    if not issues:
        return report
    return report.model_copy(
        update={
            "status": "blocked",
            "blocking_issues": [*report.blocking_issues, *issues],
        }
    )

def _has_complete_subproblem_guidance_details(subproblem: object) -> bool:
    if not isinstance(subproblem, dict):
        return False
    execution_status = subproblem.get("execution_status")
    if execution_status == "solvable":
        return all(
            isinstance(subproblem.get(field), list) and bool(subproblem[field])
            for field in ("equations", "parameters", "solve_steps")
        )
    if execution_status == "blocked":
        return (
            bool(str(subproblem.get("blocking_reason") or "").strip())
            and isinstance(subproblem.get("missing_inputs"), list)
            and bool(subproblem["missing_inputs"])
            and bool(str(subproblem.get("recovery_action") or "").strip())
        )
    return False

def _build_blocked_guidance_artifacts(
    root: Path,
    approved_path: Path,
) -> tuple[VerificationReport, dict, dict, dict]:
    approved_model = ApprovedModelIR.model_validate_json(
        approved_path.read_text(encoding="utf-8")
    )
    blocked_subproblems = [
        subproblem
        for subproblem in approved_model.approved_model_ir.subproblems
        if subproblem.execution_status == "blocked"
    ]
    issues = [subproblem.blocking_reason for subproblem in blocked_subproblems]

    parameters = {
        "parameters": [],
        "summary": {"total_count": 0, "blocked_count": 0},
    }
    results = {
        "verified_results": [],
        "blocked_results": [
            {
                "id": f"blocked_{subproblem.subproblem_id.replace('.', '_')}",
                "message": subproblem.blocking_reason,
                "reason": "approved_model_ir_blocked",
                "subproblem_id": subproblem.subproblem_id,
                "missing_inputs": subproblem.missing_inputs,
                "recovery_action": subproblem.recovery_action,
                "expected_outputs": subproblem.expected_outputs,
                "source_data": [approved_model.model_ir_ref],
            }
            for subproblem in blocked_subproblems
        ],
        "summary": {
            "coverage_status": "blocked",
            "verified_count": 0,
            "blocked_count": len(issues),
        },
    }
    report = _build_preexecution_blocked_report(
        approved_model=approved_model,
        approved_path=approved_path,
        blocking_issues=issues,
    )
    _write_model(root / "verification_report.json", report)
    (root / "parameter_registry.json").write_text(
        json.dumps(parameters, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "result_registry.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report, approved_model.model_dump(mode="json"), parameters, results

def _build_preexecution_blocked_report(
    *,
    approved_model: ApprovedModelIR,
    approved_path: Path,
    blocking_issues: list[str],
) -> VerificationReport:
    model_refs = [approved_model.model_ir_ref, approved_model.review_ref]
    if approved_model.revision_request_ref is not None:
        model_refs.append(approved_model.revision_request_ref)
    reasons = blocking_issues or ["model review blocked execution"]
    return VerificationReport(
        model_id=approved_model.model_id,
        coverage_status="blocked",
        execution_verified={
            "status": "not_assessed",
            "reasons": [
                "operator graph was not compiled or executed because model review blocked"
            ],
            "evidence_refs": model_refs,
        },
        evidence_supported={
            "status": "blocked",
            "reasons": [
                "result evidence cannot be assessed before an approved model is executed"
            ],
            "evidence_refs": model_refs,
        },
        model_adequate={
            "source_coverage": {
                "status": "not_assessed",
                "reasons": ["source coverage was not assessed for an executable model"],
            },
            "mathematical_review": {
                "status": "blocked",
                "reasons": reasons,
                "evidence_refs": model_refs,
            },
            "numerical_validation": {
                "status": "blocked",
                "candidate_roles": [],
                "checks": {},
                "reasons": ["no model was executed after the review blocked"],
                "evidence_refs": model_refs,
            },
        },
        blocking_issues=blocking_issues,
        artifact_refs={
            "approved_model": approved_path.name,
            "model_review": approved_model.review_ref,
            **(
                {"revision_request": approved_model.revision_request_ref}
                if approved_model.revision_request_ref is not None
                else {}
            ),
            "parameter_registry": "parameter_registry.json",
            "result_registry": "result_registry.json",
            "figures": [],
        },
    )
