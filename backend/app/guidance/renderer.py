"""Guidance 职责边界：renderer。"""

from __future__ import annotations

from typing import Any

from app.guidance.guidance_rendering.data import (
    _READER_DECISION_TOPIC_LABELS,
    _cell,
    _claim_registry,
    _final_answer_evidence_ids,
    _final_answer_tables,
    _guidance_notes,
    _guidance_text,
    _identifiability_analysis,
    _join_claim_items,
    _method_route_comparison,
    _reader_decision_guide,
)
from app.guidance.guidance_rendering.diagnostics import (
    _is_calculation_failure_without_verified_evidence,
    _render_calculation_failure_diagnostic,
    _render_model_ir_blocked_diagnostic,
    _render_verification_failure_diagnostic,
)
from app.guidance.guidance_rendering.sections import (
    _append_fact_traceability,
    _calculation_context_lines,
    _data_source_lines,
    _problem_understanding_lines,
    _registered_subproblem_modeling_lines,
    _verification_layer_rows,
    _verification_layer_summaries,
)


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
    problem_spec: dict[str, Any] | None = None,
) -> str:
    model = approved_model.get("approved_model_ir", {})
    model = model if isinstance(model, dict) else {}
    status = str(verification_report.get("status") or "blocked")
    if status == "blocked" and approved_model.get("review_status") != "approved":
        return _append_fact_traceability(
            _render_model_ir_blocked_diagnostic(
                model=model,
                verification_report=verification_report,
            ),
            problem_spec=problem_spec,
            model=model,
        )
    if _is_calculation_failure_without_verified_evidence(
        status=status,
        execution_manifest=execution_manifest,
        parameter_registry=parameter_registry,
        result_registry=result_registry,
    ):
        return _append_fact_traceability(
            _render_calculation_failure_diagnostic(
                execution_manifest=execution_manifest or {},
                calculation_failure=calculation_failure or {},
                verification_report=verification_report,
                result_registry=result_registry,
            ),
            problem_spec=problem_spec,
            model=model,
        )
    if status == "blocked":
        return _append_fact_traceability(
            _render_verification_failure_diagnostic(
                approved_model=approved_model,
                verification_report=verification_report,
                result_registry=result_registry,
            ),
            problem_spec=problem_spec,
            model=model,
        )
    problem_understanding = _problem_understanding_lines(model, result_registry)
    lines = [
        "# 建模指导方案",
        "",
        "## 可信度摘要",
        "",
        "| 验证层 | 状态 | 说明 |",
        "| --- | --- | --- |",
        *_verification_layer_rows(verification_report),
        "",
        f"流程聚合状态为 `{status}`；该值只用于流程路由，不代表整题数学结论已经完成。",
        "",
        "## 问题理解",
        "",
        *problem_understanding,
        "",
        "## 数据与来源",
        "",
        *_data_source_lines(parameter_registry, result_registry),
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

    reader_decision_guide = _reader_decision_guide(result_registry)
    if reader_decision_guide:
        final_answer_evidence = _final_answer_evidence_ids(result_registry)
        lines.extend(["", "## 建模路线与读者决策", ""])
        lines.append("| 读者问题 | 本题判断 | 建模/写作动作 |")
        lines.append("| --- | --- | --- |")
        for item in reader_decision_guide:
            topic = str(item.get("topic") or "")
            topic_label = _READER_DECISION_TOPIC_LABELS.get(topic, topic)
            question = _guidance_text(item.get("question"))
            action = _guidance_text(item.get("action") or item.get("guidance"))
            if topic == "final_answers" and final_answer_evidence:
                action = f"答案证据：{', '.join(final_answer_evidence)}；{action}"
            lines.append(
                "| {question} | {judgment} | {action} |".format(
                    question=_cell(f"{topic_label}：{question}" if question else topic_label),
                    judgment=_cell(_guidance_text(item.get("judgment") or item.get("answer"))),
                    action=_cell(action),
                )
            )

    method_route_comparison = _method_route_comparison(result_registry)
    if method_route_comparison:
        lines.extend(["", "## 建模路线取舍", ""])
        lines.append("| 建模路线 | 处理结论 | 取舍理由 | 适用边界 | 证据 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in method_route_comparison:
            lines.append(
                "| {route} | {decision} | {reason} | {boundary} | {evidence} |".format(
                    route=_cell(item.get("route") or item.get("name")),
                    decision=_cell(_guidance_text(item.get("status"))),
                    reason=_cell(_guidance_text(item.get("reason") or item.get("rationale"))),
                    boundary=_cell(_guidance_text(item.get("boundary") or item.get("scope"))),
                    evidence=_cell(_join_claim_items(item.get("evidence_ids"))),
                )
            )

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
                ("唯一性结论", "conclusion"),
                ("缺失信息", "missing_information"),
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
                    evidence=_cell(_join_claim_items(claim.get("evidence_ids"))),
                    assumptions=_cell(_join_claim_items(claim.get("assumptions"))),
                    risk=_cell(claim.get("risk") or claim.get("risk_note")),
                )
            )

    final_answer_tables = _final_answer_tables(result_registry)
    if final_answer_tables:
        lines.extend(["", "## 最终答案与应填表格", ""])
        for table in final_answer_tables:
            title = _cell(table.get("title") or table.get("id") or "应填表格")
            lines.append(f"### {title}")
            lines.append("")
            rows = table.get("rows", [])
            if isinstance(rows, list) and rows:
                headers = ["题号", "应填项", "答案/阻断原因", "结论状态", "证据"]
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    answer = row.get("answer")
                    if row.get("status") == "blocked":
                        answer = row.get("blocked_reason")
                    values = [
                        row.get("subproblem_id"),
                        row.get("answer_item"),
                        answer,
                        row.get("status"),
                        _join_claim_items(row.get("evidence_ids")),
                    ]
                    lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
            notes = table.get("notes", [])
            if isinstance(notes, list):
                lines.extend(f"- 说明：{_guidance_text(note)}" for note in notes if str(note).strip())
            lines.append("")

    lines.extend(["", "## 分问题建模步骤", ""])
    lines.extend(_registered_subproblem_modeling_lines(result_registry, model))

    lines.extend(["", "## 必要计算结果", ""])
    lines.extend(_calculation_context_lines(result_registry, parameter_registry))
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
    result_claims = {
        str(result.get("id") or ""): _guidance_text(result.get("claim_text"))
        for result in result_registry.get("verified_results", [])
        if isinstance(result, dict)
    }
    for figure in figures if isinstance(figures, list) else []:
        if not isinstance(figure, dict):
            continue
        path = _cell(figure.get("path"))
        role = _cell(figure.get("role"))
        sources = ", ".join(f"`{_cell(item)}`" for item in figure.get("source_data", []))
        result_ids = ", ".join(
            f"`{_cell(item)}`" for item in figure.get("linked_result_ids", [])
        )
        linked_claims = "；".join(
            result_claims.get(str(item), "")
            for item in figure.get("linked_result_ids", [])
            if result_claims.get(str(item), "")
        )
        lines.append(f"![{role}]({path})")
        lines.append(
            f"- 图片 `{path}`；用途 `{role}`；来源数据：{sources}；绑定结果：{result_ids}。"
            f"解释：本图直接支撑已登记结论：{linked_claims or '对应结果的模型与数据关系'}。"
            "局限：图形贴合只说明登记证据在当前模型口径下自洽，不能单独证明不可观测变量被唯一恢复，也不等于真实机理已被证明。"
        )

    lines.extend(["", "## 复核与稳健性", ""])
    lines.extend(f"- {row}" for row in _verification_layer_summaries(verification_report))
    lines.append("- 可辨识性与计算可行性：以模型审核结论和本轮数值复核状态为准。")
    lines.append(
        "- 误差阈值/基线解释：RMSE、残差、目标值和约束裕度只说明结果在 `verification_report.json` "
        "登记的复算准则、baseline/threshold 或容许条件下自洽；若没有明确阈值或基线，"
        "不能把 `verified` 写成真实机理充分证明。"
    )
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
    for subproblem in model.get("subproblems", []):
        if not isinstance(subproblem, dict):
            continue
        subproblem_id = _cell(subproblem.get("subproblem_id"))
        for assumption in subproblem.get("assumptions", []):
            if not isinstance(assumption, dict):
                continue
            lines.append(
                "- 假设与局限 `{subproblem}/{assumption_id}`：{statement}；"
                "依据：{sources}；替代情景：{alternatives}；敏感性检查：{checks}。".format(
                    subproblem=subproblem_id,
                    assumption_id=_cell(assumption.get("id")),
                    statement=_guidance_text(assumption.get("statement")),
                    sources=_guidance_text(_join_claim_items(assumption.get("source_refs"))),
                    alternatives=_guidance_text(
                        _join_claim_items(assumption.get("alternative_scenarios"))
                    ),
                    checks=_guidance_text(
                        _join_claim_items(assumption.get("sensitivity_checks"))
                    ),
                )
            )

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
    return _append_fact_traceability(
        "\n".join(lines) + "\n",
        problem_spec=problem_spec,
        model=model,
    )
