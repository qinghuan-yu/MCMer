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
    for step in model.get("solve_steps", []):
        lines.append(f"- {step}")
    for equation in model.get("equations", []):
        lines.append(f"- 公式：`{equation}`")

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
        lines.append(f"![模型拟合与复核图]({figure})")
        lines.append(f"- 图片 `{figure}` 绑定已登记拟合结果与源数据。")

    lines.extend(["", "## 复核与稳健性", ""])
    lines.append(f"- 数值复核结论：`{status}`。")
    lines.append("- 可辨识性：支路分解结论仅在审核通过的显式识别约束下成立。")
    for assumption in model.get("assumptions", []):
        lines.append(f"- 假设与局限：{assumption}")

    lines.extend(["", "## 阻断项与待确认项", ""])
    blocks = verification_report.get("blocking_issues", [])
    if blocks:
        lines.extend(f"- 阻断：{item}" for item in blocks)
    else:
        lines.append("- 当前复核未记录阻断项；识别约束仍需人工确认。")

    lines.extend([
        "",
        "## 论文转化建议",
        "",
        "将模型选择、识别约束、参数来源和残差复核分别写入论文，禁止省略条件性结论。",
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
