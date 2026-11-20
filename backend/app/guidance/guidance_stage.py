"""Guidance 职责边界：guidance_stage。"""

from __future__ import annotations

import json
from pathlib import Path

from app.artifacts.guidance_audit import build_guidance_audit_report
from app.guidance.contracts import VerificationReport
from app.guidance.model_review_contracts import ApprovedModelIR
from app.guidance.recovery import file_snapshots
from app.guidance.renderer import render_verified_guidance
from app.guidance.guidance_rendering.data import _cell, _join_claim_items
from app.guidance.guidance_rendering.sections import _append_fact_traceability, _verification_layer_rows
from app.guidance.verification_stage import ResultVerificationStage
from app.guidance.stage_io import (
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
        execution_manifest = {}
        calculation_failure = {}
        if source_path.name == "approved_model_ir.json":
            problem_spec = _read_json_if_exists(root / "problem_spec.json")
            report, approved, parameters, results = _build_blocked_guidance_artifacts(
                root,
                source_path,
            )
        else:
            report = _read_verification_for_publication(source_path)
            # 下游只读取工作区中约定的正式工件，不接受文档提供的任意路径。
            approved, parameters, results, problem_spec = {}, {}, {}, {}
            try:
                approved = _read_json_if_exists(root / "approved_model_ir.json")
                execution_manifest = _read_json_if_exists(root / "execution_manifest.json")
                problem_spec = _read_json_if_exists(root / "problem_spec.json")
                parameters = _read_json_if_exists(root / "parameter_registry.json")
                results = _read_json_if_exists(root / "result_registry.json")
                calculation_failure = _read_json_if_exists(root / "calculation_failure.json")
                if report.status != "blocked":
                    report = _publication_report(report, root)
            except (OSError, ValueError) as exc:
                report = report.model_copy(update={"status": "blocked", "blocking_issues": [
                    f"cannot read publication evidence: {exc}",
                ]})
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
            "required_sections": _required_guidance_sections(guidance, results),
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
            expected_fact_appendix=_append_fact_traceability("", problem_spec=problem_spec,
                model=approved.get("approved_model_ir", {})).strip(),
            verification_rows=_verification_layer_rows(report.model_dump(mode="json")),
            assumption_number_context=_assumption_number_context(
                problem_spec, approved.get("approved_model_ir", {}), results) if report.status != "blocked" else None,
        )
        if audit["status"] == "BLOCK" and report.status != "blocked":
            issues = [f"{item['type']} ({item['location']})" for item in audit["blocks"]]
            report = report.model_copy(update={"status": "blocked", "blocking_issues": issues})
            guidance = render_verified_guidance(
                approved_model=approved, verification_report=report.model_dump(mode="json"),
                parameter_registry=parameters, result_registry=results, problem_spec=problem_spec,
            )
            context["required_sections"] = _required_guidance_sections(guidance, results)
        audit["publication_status"] = report.status
        output_path.write_text(guidance, encoding="utf-8")
        audit["guidance_sha256"] = file_snapshots(root, [output_path.name])[output_path.name]
        (root / "res.md").write_text(guidance, encoding="utf-8")
        (root / "guidance_context.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "guidance_audit_report.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return output_path


def _assumption_number_context(problem_spec, model, results) -> dict[str, tuple[str, set[int | float]]]:
    """Source-backed numbers permitted inside the exact approved assumption cell of each answer."""
    facts = {fact["fact_id"]: fact for fact in (problem_spec or {}).get("locked_facts", [])}
    registered = {(result.get("subproblem_id"), result.get("id")) for result in results.get("verified_results", [])}
    context = {}
    for sub in model.get("subproblems", []):
        assumptions = sub.get("assumptions", [])
        refs = {ref for a in assumptions for ref in a["source_refs"]}
        values = set()
        for binding in sub.get("fact_bindings", []):
            fact = facts.get(binding["fact_id"])
            if not fact or not binding["model_paths"] or type(fact.get("value")) not in (float, int):
                continue
            if fact["fact_id"] in refs or refs.intersection(fact["source_refs"]):
                values.add(fact["value"])
        if values:
            cell = _cell(_join_claim_items([a["statement"] for a in assumptions]))
            for answer in sub.get("final_answers", []):
                if (sub["subproblem_id"], answer["id"]) in registered:
                    context[_cell(answer["id"])] = cell, values
    return context

def _required_guidance_sections(guidance: str, results: dict) -> list[str]:
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
    summary = results.get("summary", {})
    sections = [
        "可信度摘要", "问题理解", "数据与来源", "参数与来源表", "模型选择理由",
        "分问题建模步骤", "必要计算结果", "复核与稳健性", "阻断项与待确认项", "可复现附件说明",
    ]
    for field, section in (
        ("reader_decision_guide", "建模路线与读者决策"), ("method_route_comparison", "建模路线取舍"),
        ("identifiability_analysis", "可辨识性分析"), ("claim_registry", "结论状态登记"),
    ):
        if summary.get(field):
            sections.append(section)
    if results.get("verified_results") or summary.get("final_answer_tables"):
        sections.append("最终答案与应填表格")
    return sections


def _read_verification_for_publication(path: Path) -> VerificationReport:
    try:
        return VerificationReport.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        reason = f"无法读取有效的验证记录 {path.name}，请从执行清单重新复核。"
        layer = {"status": "not_assessed", "reasons": [reason]}
        # 此对象仅供阻断报告显示，保留原验证记录或缺失现场。
        return VerificationReport(
            model_id="", coverage_status="blocked", blocking_issues=[reason],
            execution_verified=layer, evidence_supported=layer,
            model_adequate={"source_coverage": layer, "mathematical_review": layer,
                            "numerical_validation": layer},
            artifact_refs={"verification": path.name},
        )


def _publication_report(report: VerificationReport, root: Path) -> VerificationReport:
    # 重读同一套执行、来源、数学验证证据，不重新求解，也不由报告另作数学裁决。
    try:
        current = ResultVerificationStage.assess(root / "execution_manifest.json", root)
        if current == report and current.evidence_supported.status == "passed":
            return report
        issues = current.blocking_issues or ["verification evidence missing or changed before publication"]
        if current.schema_version != report.schema_version:
            issues = [*current.blocking_issues,
                      "legacy baseline comparisons and candidate exclusions require a new verification report"]
    except (OSError, ValueError, KeyError) as exc:
        issues = [f"verification evidence unavailable before publication: {exc}"]
    return report.model_copy(update={"status": "blocked", "blocking_issues": issues})


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
