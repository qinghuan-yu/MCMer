"""Deterministic Markdown renderer for auditable guidance artifacts."""

from __future__ import annotations

from typing import Any


def render_guidance_markdown(
    *,
    solve_spec: dict[str, Any],
    result_registry: dict[str, Any],
    parameter_registry: dict[str, Any],
) -> str:
    coverage = str(result_registry.get("summary", {}).get("coverage_status") or "partial")
    lines = [
        "# 建模指导方案",
        "",
        "## 可信度摘要",
        "",
        f"当前结构化计算覆盖状态为 `{coverage}`。本文只展示参数注册表和结果注册表中已有依据的内容。",
        "",
        "## 问题理解",
        "",
        str(solve_spec.get("problem_summary") or "题目仍需进一步结构化。"),
        "",
        "## 数据与来源",
        "",
        "所有输入来源均在参数与来源表的 `source_ref` 和 `source_location` 字段中登记。",
        "",
        "## 参数与来源表",
        "",
        "| parameter_id | 符号 | 含义 | 数值 | 单位 | 来源类型 | 来源 | 可信状态 |",
        "|---|---|---|---|---|---|---|---|",
    ]

    parameters = parameter_registry.get("parameters", [])
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            lines.append(
                "| {id} | {symbol} | {name} | {value} | {unit} | {source_type} | {source} | {status} |".format(
                    id=_cell(parameter.get("id")),
                    symbol=_cell(parameter.get("symbol")),
                    name=_cell(parameter.get("name")),
                    value=_cell(parameter.get("value", "待确认")),
                    unit=_cell(parameter.get("unit")),
                    source_type=_cell(parameter.get("source_type")),
                    source=_cell(parameter.get("source_ref") or parameter.get("source_location")),
                    status=_cell(parameter.get("trust_status")),
                )
            )

    lines.extend([
        "",
        "## 模型选择理由",
        "",
        str(solve_spec.get("method_guidance") or "当前规格未登记模型选择理由。"),
        "",
        "## 分问题建模步骤",
        "",
    ])
    steps = solve_spec.get("steps", [])
    if isinstance(steps, list) and steps:
        lines.extend(f"- {step}" for step in steps)
    else:
        lines.append("- 补充可执行计算步骤。")

    lines.extend(["", "## 必要计算结果", ""])
    verified = result_registry.get("verified_results", [])
    if isinstance(verified, list) and verified:
        for result in verified:
            if not isinstance(result, dict):
                continue
            lines.append(f"- `{_cell(result.get('id'))}`：{result.get('claim_text') or '已登记结果'}")
    else:
        lines.append("- 当前没有通过注册表验证的数值结果，不能给出确定性结论。")

    lines.extend(["", "## 复核与稳健性", ""])
    assumptions = solve_spec.get("assumptions", [])
    if isinstance(assumptions, list) and assumptions:
        lines.extend(f"- 待复核假设：{assumption}" for assumption in assumptions)
    else:
        lines.append("- 当前未登记额外假设；仍需结合题目领域进行稳健性复核。")

    lines.extend(["", "## 阻断项与待确认项", ""])
    blocked = result_registry.get("blocked_results", [])
    if isinstance(blocked, list) and blocked:
        for item in blocked:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- 阻断 `{_cell(item.get('id'))}`：{item.get('message') or item.get('reason') or '缺少可辨识参数'}"
            )
    else:
        lines.append("- 当前注册表未记录阻断项。")

    lines.extend([
        "",
        "## 论文转化建议",
        "",
        "先补齐阻断计算和稳健性检验，再把本方案改写为论文；不得把待确认参数写成既定事实。",
        "",
        "## 可复现附件说明",
        "",
        "- `solve_spec.json`：结构化计算规格。",
        "- `compute_run_manifest.json`：本地计算运行清单。",
        "- `parameter_registry.json`：参数、来源和可信状态。",
        "- `result_registry.json`：已验证结果与阻断结果。",
        "- `guidance_audit_report.json`：最终机器审计报告。",
        "",
    ])
    return "\n".join(lines)


def render_verified_guidance(
    *,
    approved_model: dict[str, Any],
    verification_report: dict[str, Any],
    parameter_registry: dict[str, Any],
    result_registry: dict[str, Any],
    execution_manifest: dict[str, Any] | None = None,
    calculation_failure: dict[str, Any] | None = None,
) -> str:
    model = approved_model.get("approved_model", {})
    model = model if isinstance(model, dict) else {}
    status = str(verification_report.get("status") or "blocked")
    failure = model.get("failure")
    if status == "blocked" and isinstance(failure, dict) and failure:
        return _render_blocked_failure_diagnostic(
            failure=failure,
            verification_report=verification_report,
            result_registry=result_registry,
        )
    if _is_calculation_failure_without_verified_evidence(
        status=status,
        execution_manifest=execution_manifest,
        parameter_registry=parameter_registry,
        result_registry=result_registry,
    ):
        return _render_calculation_failure_diagnostic(
            execution_manifest=execution_manifest or {},
            calculation_failure=calculation_failure or {},
            verification_report=verification_report,
            result_registry=result_registry,
        )
    if status == "blocked":
        return _render_verification_failure_diagnostic(
            approved_model=approved_model,
            verification_report=verification_report,
            result_registry=result_registry,
        )
    lines = [
        "# 建模指导方案",
        "",
        "## 可信度摘要",
        "",
        f"结果复核状态为 `{status}`；仅引用审核通过的模型与已登记结果。",
        "",
        "## 问题理解",
        "",
        str(model.get("problem_summary") or "依据题目拆解契约完成当前子问题的建模与计算。"),
        "",
        "## 数据与来源",
        "",
        "数据来源逐项记录在参数注册表和结果注册表中。",
        "",
        "## 参数与来源表",
        "",
        "| parameter_id | 符号 | 含义 | 数值 | 单位 | 来源类型 | 来源 | 可信状态 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for parameter in parameter_registry.get("parameters", []):
        if not isinstance(parameter, dict):
            continue
        lines.append(
            "| {id} | {symbol} | {name} | {value} | {unit} | {source_type} | {source} | {status} |".format(
                id=_cell(parameter.get("id")),
                symbol=_cell(parameter.get("symbol")),
                name=_cell(parameter.get("name")),
                value=_cell(parameter.get("value")),
                unit=_cell(parameter.get("unit")),
                source_type=_cell(parameter.get("source_type")),
                source=_cell(parameter.get("source_ref")),
                status=_cell(parameter.get("trust_status")),
            )
        )

    lines.extend(["", "## 模型选择理由", ""])
    lines.append(str(model.get("selected_method") or "未登记推荐方法。"))
    candidates = model.get("candidate_methods", [])
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                lines.append(f"- 备选方法：{candidate.get('name') or candidate.get('method') or '未命名'}")

    guidance_notes = _guidance_notes(result_registry)
    if guidance_notes:
        lines.extend(["", "## 关键可信度边界", ""])
        lines.extend(f"- {note}" for note in guidance_notes)

    identifiability_analysis = _identifiability_analysis(result_registry)
    if identifiability_analysis:
        lines.extend(["", "## 可辨识性分析", ""])
        for item in identifiability_analysis:
            subproblem_id = _cell(item.get("subproblem_id") or item.get("id"))
            lines.append(f"### {subproblem_id}")
            lines.append("")
            for label, key in (
                ("观测量", "observed"),
                ("未知量", "unknowns"),
                ("方程结构", "equation"),
                ("秩/自由度结论", "rank_condition"),
                ("解族或规范化", "solution_family"),
                ("采用的代表解规则", "selection_rule"),
                ("论文写法提醒", "guidance"),
            ):
                value = item.get(key)
                if value in {None, ""}:
                    continue
                lines.append(f"- {label}：{_guidance_text(_join_claim_items(value))}")
            lines.append("")

    claim_registry = _claim_registry(result_registry)
    if claim_registry:
        lines.extend(["", "## 结论状态登记", ""])
        lines.append("| claim_id | 状态 | 结论 | 证据 | 依赖假设 | 风险 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for claim in claim_registry:
            lines.append(
                "| {id} | {status} | {claim_text} | {evidence} | {assumptions} | {risk} |".format(
                    id=_cell(claim.get("id") or claim.get("claim_id")),
                    status=_cell(claim.get("status")),
                    claim_text=_cell(claim.get("claim_text")),
                    evidence=_cell(_join_claim_items(claim.get("evidence") or claim.get("evidence_ids"))),
                    assumptions=_cell(_join_claim_items(claim.get("assumptions"))),
                    risk=_cell(claim.get("risk") or claim.get("risk_note")),
                )
            )

    lines.extend(["", "## 分问题建模步骤", ""])
    for subproblem in model.get("subproblem_models", []):
        if not isinstance(subproblem, dict):
            continue
        subproblem_id = _cell(subproblem.get("subproblem_id"))
        lines.append(f"### 子问题 {subproblem_id}")
        lines.append("")
        lines.append(f"- 目标：{_guidance_text(subproblem.get('objective'))}")
        lines.append(f"- 方法：{_guidance_text(subproblem.get('selected_method'))}")
        lines.append(f"- 选择理由：{_guidance_text(subproblem.get('rationale'))}")
        for equation in subproblem.get("equations", []):
            lines.append(f"- 公式：`{_guidance_text(equation)}`")
        for parameter in subproblem.get("parameters", []):
            if not isinstance(parameter, dict):
                continue
            lines.append(
                "- 参数 `{symbol}`：{meaning}；单位 `{unit}`；来源 `{source}`；估计方法：{method}。".format(
                    symbol=_cell(parameter.get("symbol")),
                    meaning=_guidance_text(parameter.get("meaning")),
                    unit=_cell(parameter.get("unit")),
                    source=_guidance_text(parameter.get("source_detail")),
                    method=_guidance_text(parameter.get("estimation_method")),
                )
            )
        for constraint in subproblem.get("constraints", []):
            lines.append(f"- 约束：{_guidance_text(constraint)}")
        for index, step in enumerate(subproblem.get("solve_steps", []), start=1):
            lines.append(f"- 求解步骤 {index}：{_guidance_text(step)}")
        for result in subproblem.get("expected_results", []):
            lines.append(f"- 预期结果：{_guidance_text(result)}")
        lines.append("")

    lines.extend(["", "## 必要计算结果", ""])
    for result in result_registry.get("verified_results", []):
        if not isinstance(result, dict):
            continue
        lines.append(f"- `{_cell(result.get('id'))}`：{result.get('claim_text') or '已登记结果'}")
        metrics = result.get("metrics", {})
        if isinstance(metrics, dict):
            for name, value in metrics.items():
                lines.append(f"  - `{name}` = `{value}`")

    lines.extend(["", "## 图片解读", ""])
    figures = verification_report.get("artifact_refs", {}).get("figures", [])
    for figure in figures if isinstance(figures, list) else []:
        if not isinstance(figure, dict):
            continue
        path = _cell(figure.get("path"))
        role = _cell(figure.get("role"))
        sources = ", ".join(f"`{_cell(item)}`" for item in figure.get("source_data", []))
        result_ids = ", ".join(
            f"`{_cell(item)}`" for item in figure.get("linked_result_ids", [])
        )
        lines.append(f"![{role}]({path})")
        lines.append(
            f"- 图片 `{path}`；用途 `{role}`；来源数据：{sources}；绑定结果：{result_ids}。"
        )

    lines.extend(["", "## 复核与稳健性", ""])
    lines.append(f"- 数值复核结论：`{status}`。")
    lines.append("- 可辨识性与计算可行性：以模型审核结论和本轮数值复核状态为准。")
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
        passed = sum(1 for check in checks if isinstance(check, dict) and check.get("status") == "passed")
        lines.append(f"- {label}复核：{passed}/{len(checks)} 项通过。")
    for assumption in model.get("assumptions", []):
        lines.append(f"- 假设与局限：{_guidance_text(assumption)}")

    lines.extend(["", "## 阻断项与待确认项", ""])
    blocks = verification_report.get("blocking_issues", [])
    if blocks:
        lines.extend(f"- 阻断：{item}" for item in blocks)
    else:
        lines.append("- 当前复核未记录阻断项；仍需人工确认题设假设、参数来源和约束口径。")

    lines.extend([
        "",
        "## 论文转化建议",
        "",
        "将模型选择、核心假设、参数来源、约束条件和数值复核分别写入论文，禁止把待确认前提写成既定事实。",
        "",
        "## 可复现附件说明",
        "",
    ])
    for name, ref in verification_report.get("artifact_refs", {}).items():
        if isinstance(ref, list):
            lines.extend(f"- `{item}`" for item in ref)
        else:
            lines.append(f"- `{name}`：`{ref}`")
    return "\n".join(lines) + "\n"


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
        f"- 恢复入口：`{_cell(calculation_failure.get('resume_from') or 'approved_model_spec.json')}`",
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
        "- 重新生成 `solve.py`，确保它只读取真实附件和题设事实，不使用占位、模拟、随机或兜底数据。",
        "- 将 `solver_results.json`、`parameter_registry.json`、`result_registry.json` 和 `figure_registry.json` 写到 required output 指定的精确相对路径。",
        "- 写出 typed solver evidence 后重新执行结果复核；只有复核通过的参数和结果才能进入正式指导方案。",
        "",
        "## 可复现附件说明",
        "",
    ])
    for name, ref in verification_report.get("artifact_refs", {}).items():
        if name == "figures":
            continue
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
        "- 回到 `solve.py` 与 `solver_results.json`，修正导致复核失败的 typed solver evidence。",
        "- 对回归 evidence，`constraint_values` 必须是非负约束裕度或模型中的非负量，不能复用 residuals/errors。",
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


def _guidance_notes(result_registry: dict[str, Any]) -> list[str]:
    summary = result_registry.get("summary", {})
    if not isinstance(summary, dict):
        return []
    notes = summary.get("guidance_notes", [])
    if not isinstance(notes, list):
        return []
    return [str(note).strip() for note in notes if str(note).strip()]


def _claim_registry(result_registry: dict[str, Any]) -> list[dict[str, Any]]:
    summary = result_registry.get("summary", {})
    if isinstance(summary, dict):
        claims = summary.get("claim_registry", [])
        if isinstance(claims, list):
            return [claim for claim in claims if isinstance(claim, dict)]
    return []


def _identifiability_analysis(result_registry: dict[str, Any]) -> list[dict[str, Any]]:
    summary = result_registry.get("summary", {})
    if isinstance(summary, dict):
        analysis = summary.get("identifiability_analysis", [])
        if isinstance(analysis, list):
            return [item for item in analysis if isinstance(item, dict)]
    return []


def _join_claim_items(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value if value not in {None, ""} else "待确认")


def _guidance_text(value: Any) -> str:
    text = str(value if value not in {None, ""} else "待确认")
    replacements = {
        "通过固定一个截距为0来增强可辨识性。": (
            "固定一个截距为 0 仅是规范化约束，用于选取一个可复现代表解，"
            "不能证明支路分解唯一。"
        ),
        "通过固定一个截距为0来增强参数可辨识性。": (
            "固定一个截距为 0 仅是规范化约束，用于选取一个可复现代表解，"
            "不能证明支路分解唯一。"
        ),
        "增强参数可辨识性": "作为规范化约束选择代表解",
        "为增强可辨识性固定为0": "人为固定为 0 作为规范化约束",
        "为增强可辨识性": "作为规范化约束",
        "增强可辨识性": "作为规范化约束选择代表解",
        "固定b1=0或b2=0以确保参数唯一识别": (
            "固定 b1=0 或 b2=0 只能作为规范化约束，不能作为数据唯一识别证据"
        ),
        "F(t) = f1(t-2) + f2(t-2) + f3(t) + f4(t) (延迟补偿)": (
            "F(k)=f_1(k-1)+f_2(k-1)+f_3(k)+f_4(k)（样本序号 k；2 分钟延迟 = 1 个采样步）"
        ),
        "F(t) = f1(t-2) + f2(t-2) + f3(t) + f4(t)": (
            "F(k)=f_1(k-1)+f_2(k-1)+f_3(k)+f_4(k)"
        ),
        "F(t) = f1(t-2) + f2(t-2) + f3(t) (延迟补偿)": (
            "F(k)=f_1(k-1)+f_2(k-1)+f_3(k)（样本序号 k；2 分钟延迟 = 1 个采样步）"
        ),
        "F(t) = f1(t-2) + f2(t-2) + f3(t)": (
            "F(k)=f_1(k-1)+f_2(k-1)+f_3(k)"
        ),
        "F_obs(t) = F_true(t) + ε(t), where F_true(t) = f1(t-2) + f2(t-2) + f3(t)": (
            "F_obs(k)=F_true(k)+epsilon(k)，其中 F_true(k)=f_1(k-1)+f_2(k-1)+f_3(k)"
        ),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _render_blocked_failure_diagnostic(
    *,
    failure: dict[str, Any],
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
        "# 建模链路阻断诊断",
        "",
        "## 可信度摘要",
        "",
        "本次任务未形成可发布的建模指导方案；链路在模型审核前被合同校验阻断。",
        "系统没有审核通过的模型、参数表或数值计算结果，因此不会输出空参数表或伪计算结论。",
        "",
        "## 阻断位置",
        "",
        f"- 失败阶段：`{_cell(failure.get('failed_stage'))}`",
        f"- 失败操作：`{_cell(failure.get('failed_operation'))}`",
        f"- 错误类型：`{_cell(failure.get('error_type'))}`",
        f"- 输入文件：`{_cell(failure.get('input_ref'))}`",
        f"- 恢复入口：`{_cell(failure.get('resume_from'))}`",
        "",
        "## 阻断原因",
        "",
        f"- {failure.get('error') or '未登记详细错误。'}",
    ]
    for issue in blocking_issues:
        if issue and issue != failure.get("error"):
            lines.append(f"- {issue}")

    lines.extend([
        "",
        "## 已登记阻断结果",
        "",
    ])
    if blocked_results:
        for item in blocked_results:
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{_cell(item.get('id'))}`：{item.get('message') or item.get('reason')}")
    else:
        lines.append("- 暂无额外 blocked result；以建模合同失败记录为准。")

    lines.extend([
        "",
        "## 下一步修复动作",
        "",
        "- 重新生成 `ModelSpec`，并逐个子问题补齐可辨识性计划。",
        "- 每个子问题至少写明 rank/Jacobian/唯一性检查、固定参数、额外识别约束或设计矩阵满秩条件之一。",
        "- 修复后从恢复入口重新进入建模阶段，再由独立模型审核决定是否允许进入计算。",
        "",
        "## 可复现附件说明",
        "",
    ])
    for name, ref in verification_report.get("artifact_refs", {}).items():
        if name == "figures":
            continue
        lines.append(f"- `{name}`：`{ref}`")
    return "\n".join(lines) + "\n"


def _cell(value: Any) -> str:
    text = str(value if value not in {None, ""} else "待确认")
    return text.replace("|", "\\|").replace("\n", " ")
