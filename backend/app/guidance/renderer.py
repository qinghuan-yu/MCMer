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
) -> str:
    model = approved_model.get("approved_model", {})
    model = model if isinstance(model, dict) else {}
    status = str(verification_report.get("status") or "blocked")
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

    lines.extend(["", "## 分问题建模步骤", ""])
    for subproblem in model.get("subproblem_models", []):
        if not isinstance(subproblem, dict):
            continue
        subproblem_id = _cell(subproblem.get("subproblem_id"))
        lines.append(f"### 子问题 {subproblem_id}")
        lines.append("")
        lines.append(f"- 目标：{subproblem.get('objective')}")
        lines.append(f"- 方法：{subproblem.get('selected_method')}")
        lines.append(f"- 选择理由：{subproblem.get('rationale')}")
        for equation in subproblem.get("equations", []):
            lines.append(f"- 公式：`{equation}`")
        for parameter in subproblem.get("parameters", []):
            if not isinstance(parameter, dict):
                continue
            lines.append(
                "- 参数 `{symbol}`：{meaning}；单位 `{unit}`；来源 `{source}`；估计方法：{method}。".format(
                    symbol=_cell(parameter.get("symbol")),
                    meaning=parameter.get("meaning"),
                    unit=_cell(parameter.get("unit")),
                    source=parameter.get("source_detail"),
                    method=parameter.get("estimation_method"),
                )
            )
        for constraint in subproblem.get("constraints", []):
            lines.append(f"- 约束：{constraint}")
        for index, step in enumerate(subproblem.get("solve_steps", []), start=1):
            lines.append(f"- 求解步骤 {index}：{step}")
        for result in subproblem.get("expected_results", []):
            lines.append(f"- 预期结果：{result}")
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
        lines.append(f"- 假设与局限：{assumption}")

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


def _cell(value: Any) -> str:
    text = str(value if value not in {None, ""} else "待确认")
    return text.replace("|", "\\|").replace("\n", " ")
