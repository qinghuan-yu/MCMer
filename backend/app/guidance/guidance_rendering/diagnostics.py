"""Guidance 职责边界：guidance_rendering.diagnostics。"""

from __future__ import annotations

from typing import Any

from app.guidance.guidance_rendering.data import (
    _cell,
    _guidance_text,
    _join_claim_items,
)


def _is_calculation_failure_without_verified_evidence(
    *,
    status: str,
    execution_manifest: dict[str, Any] | None,
    parameter_registry: dict[str, Any],
    result_registry: dict[str, Any],
) -> bool:
    if status != "blocked" or not isinstance(execution_manifest, dict):
        return False
    verified_results = result_registry.get("verified_results", [])
    parameters = parameter_registry.get("parameters", [])
    return (
        execution_manifest.get("status") == "failed"
        and not verified_results
        and not parameters
    )

def _render_calculation_failure_diagnostic(
    *,
    execution_manifest: dict[str, Any],
    calculation_failure: dict[str, Any],
    verification_report: dict[str, Any],
    result_registry: dict[str, Any],
) -> str:
    blocking_issues = verification_report.get("blocking_issues", [])
    if not isinstance(blocking_issues, list):
        blocking_issues = []
    missing_outputs = execution_manifest.get("missing_required_outputs", [])
    if not isinstance(missing_outputs, list):
        missing_outputs = []
    blocked_results = result_registry.get("blocked_results", [])
    if not isinstance(blocked_results, list):
        blocked_results = []

    failed_operation = calculation_failure.get("failed_operation")
    if not failed_operation:
        failed_operation = (
            "solver_execution"
            if execution_manifest.get("execution_count", 0)
            else "program_generation"
        )
    error_message = (
        calculation_failure.get("error")
        or execution_manifest.get("error_message")
        or "未登记详细错误。"
    )

    lines = [
        "# 计算链路阻断诊断",
        "",
        "## 可信度摘要",
        "",
        "本次任务未形成可发布的建模指导方案；链路在数值计算阶段被阻断。",
        "系统没有参数注册表、结果注册表或 typed solver evidence，因此不会输出空参数表、空计算结果或未复核结论。",
        "",
        "## 阻断位置",
        "",
        "- 失败阶段：`calculation`",
        f"- 失败操作：`{_cell(failed_operation)}`",
        f"- 错误类型：`{_cell(calculation_failure.get('error_type') or _manifest_error_type(execution_manifest))}`",
        f"- 求解入口：`{_cell(execution_manifest.get('entrypoint'))}`",
        f"- 恢复入口：`{_cell(calculation_failure.get('resume_from') or 'execution_plan.json')}`",
        "",
        "## 阻断原因",
        "",
        f"- {error_message}",
    ]
    for issue in blocking_issues:
        if issue and issue != error_message:
            lines.append(f"- {issue}")

    lines.extend(["", "## 缺失产物", ""])
    if missing_outputs:
        lines.extend(f"- `{_cell(item)}`" for item in missing_outputs)
    else:
        lines.append("- 未登记缺失产物清单；以执行清单为准。")

    lines.extend(["", "## 已登记阻断结果", ""])
    if blocked_results:
        for item in blocked_results:
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{_cell(item.get('id'))}`：{item.get('message') or item.get('reason')}")
    else:
        lines.append("- 暂无 verified result；本轮只保留执行失败和复核阻断记录。")

    lines.extend([
        "",
        "## 下一步修复动作",
        "",
        "- 检查 `execution_plan.json` 中失败节点的算子版本、输入引用和输出声明。",
        "- 修复通用算子或 Model IR 后重跑执行图，重新生成参数、结果、图片和执行清单。",
        "- 重新执行结果复核；只有通过复核的参数和结果才能进入正式指导方案。",
        "",
        "## 可复现附件说明",
        "",
    ])
    for name, ref in verification_report.get("artifact_refs", {}).items():
        if name == "figures":
            continue
        lines.append(f"- `{name}`：`{ref}`")
    return "\n".join(lines) + "\n"

def _render_model_ir_blocked_diagnostic(
    *,
    model: dict[str, Any],
    verification_report: dict[str, Any],
) -> str:
    blocked = [
        item
        for item in model.get("subproblems", [])
        if isinstance(item, dict) and item.get("execution_status") == "blocked"
    ]
    lines = [
        "# 建模链路阻断诊断",
        "",
        "## 可信度摘要",
        "",
        "仅展示审核合同中已登记为 blocked 的 Model IR 子问题；未执行候选模型不构成恢复依据。",
        "",
        "## 阻断位置",
        "",
        "- 失败阶段：`model_ir`",
        f"- 模型 ID：`{_cell(model.get('model_id'))}`",
        "- 恢复入口：`model_ir.json`",
        "",
        "## 阻断原因",
        "",
    ]
    for subproblem in blocked:
        lines.extend([
            f"### 子问题 {_cell(subproblem.get('subproblem_id'))}",
            "",
            f"- 目标：{_guidance_text(subproblem.get('objective'))}",
            f"- 阻断原因：{_guidance_text(subproblem.get('blocking_reason'))}",
            f"- 缺失输入：{_guidance_text(_join_claim_items(subproblem.get('missing_inputs')))}",
            f"- 恢复后预期输出：{_guidance_text(_join_claim_items(subproblem.get('expected_outputs')))}",
            "",
        ])
    if not blocked:
        lines.append("- 已批准 Model IR 未登记可引用的 blocked 子问题。")
    lines.extend(["", "## 下一步修复动作", ""])
    if blocked:
        for subproblem in blocked:
            lines.append(
                f"- `{_cell(subproblem.get('subproblem_id'))}`："
                f"{_guidance_text(subproblem.get('recovery_action'))}"
            )
    else:
        lines.append("- 修订 Model IR，为每个不可计算子问题登记真实缺失输入和恢复条件。")
    lines.extend(["", "## 可复现附件说明", ""])
    for name, ref in verification_report.get("artifact_refs", {}).items():
        if name != "figures":
            lines.append(f"- `{name}`：`{ref}`")
    return "\n".join(lines) + "\n"

def _render_verification_failure_diagnostic(
    *,
    approved_model: dict[str, Any],
    verification_report: dict[str, Any],
    result_registry: dict[str, Any],
) -> str:
    blocking_issues = verification_report.get("blocking_issues", [])
    if not isinstance(blocking_issues, list):
        blocking_issues = []
    blocked_results = result_registry.get("blocked_results", [])
    if not isinstance(blocked_results, list):
        blocked_results = []

    lines = [
        "# 结果复核阻断诊断",
        "",
        "## 可信度摘要",
        "",
        "本次任务已有计算产物，但结果复核未通过，因此不会输出正式建模指导方案。",
        "系统不会展示参数表、数值结论、模型公式或图片解读作为可信内容；这些内容必须先修复并重新复核。",
        "",
        "## 阻断位置",
        "",
        "- 失败阶段：`verification`",
        f"- 模型 ID：`{_cell(verification_report.get('model_id') or approved_model.get('model_id'))}`",
        "- 恢复入口：`execution_manifest.json`",
        "",
        "## 阻断原因",
        "",
    ]
    if blocking_issues:
        lines.extend(f"- {issue}" for issue in blocking_issues)
    else:
        lines.append("- 复核报告状态为 blocked，但未登记详细阻断项；以 verification_report.json 为准。")

    lines.extend(["", "## 复核检查概览", ""])
    checked_groups = [
        ("输入覆盖", verification_report.get("input_checks", [])),
        ("公式/目标函数", verification_report.get("equation_checks", [])),
        ("单位一致性", verification_report.get("unit_checks", [])),
        ("约束满足", verification_report.get("constraint_checks", [])),
        ("残差/目标值", verification_report.get("residual_checks", [])),
        ("敏感性", verification_report.get("sensitivity_checks", [])),
    ]
    for label, checks in checked_groups:
        if not isinstance(checks, list) or not checks:
            continue
        passed = sum(
            1 for check in checks if isinstance(check, dict) and check.get("status") == "passed"
        )
        lines.append(f"- {label}复核：{passed}/{len(checks)} 项通过。")

    lines.extend(["", "## 已登记阻断结果", ""])
    if blocked_results:
        for item in blocked_results:
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{_cell(item.get('id'))}`：{item.get('message') or item.get('reason')}")
    else:
        lines.append("- 结果注册表未登记 blocked_results；本轮阻断来自 verification_report.json 的复核检查。")

    lines.extend([
        "",
        "## 下一步修复动作",
        "",
        "- 回到 `execution_plan.json`，定位产生未通过复核结果的算子节点和对应 IR 来源。",
        "- 修正算子输入、数学前提或验证计划后重跑执行图，禁止直接改写结果注册表。",
        "- 将题设常量、单位换算和显式假设补登记到 `parameter_registry.json`，再重新执行结果复核。",
        "",
        "## 可复现附件说明",
        "",
    ])
    for name, ref in verification_report.get("artifact_refs", {}).items():
        if name == "figures":
            continue
        lines.append(f"- `{name}`：`{ref}`")
    return "\n".join(lines) + "\n"

def _manifest_error_type(execution_manifest: dict[str, Any]) -> str:
    message = str(execution_manifest.get("error_message") or "")
    if ":" in message:
        return message.split(":", 1)[0]
    return "ExecutionFailure"
