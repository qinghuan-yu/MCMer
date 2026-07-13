"""Deterministic Markdown renderer for auditable guidance artifacts."""

from __future__ import annotations

from typing import Any


_READER_DECISION_TOPIC_LABELS = {
    "problem_context": "观测量/未知量/时间口径",
    "identifiability": "可辨识性与缺失信息",
    "route_choice": "推荐路线与排除路线",
    "final_answers": "最终答案、证据与写作动作",
    "claim_sources": "数据/证据、假设/先验、候选/代表解来源分层",
}


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
    model = approved_model.get("approved_model_ir", {})
    model = model if isinstance(model, dict) else {}
    status = str(verification_report.get("status") or "blocked")
    if status == "blocked" and approved_model.get("review_status") != "approved":
        return _render_model_ir_blocked_diagnostic(
            model=model,
            verification_report=verification_report,
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


def _verification_layer_rows(verification_report: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for label, key in (
        ("执行正确性", "execution_verified"),
        ("证据可追溯性", "evidence_supported"),
        ("模型充分性", "model_adequate"),
    ):
        layer = verification_report.get(key)
        layer = layer if isinstance(layer, dict) else {}
        layer_status = _cell(layer.get("status") or "not_assessed")
        reasons = layer.get("reasons")
        reason_text = _join_claim_items(reasons) or "未登记说明"
        rows.append(f"| {label} | `{layer_status}` | {_cell(reason_text)} |")
    return rows


def _verification_layer_summaries(verification_report: dict[str, Any]) -> list[str]:
    summaries: list[str] = []
    for label, key in (
        ("执行正确性", "execution_verified"),
        ("证据可追溯性", "evidence_supported"),
        ("模型充分性", "model_adequate"),
    ):
        layer = verification_report.get(key)
        layer = layer if isinstance(layer, dict) else {}
        summaries.append(f"{label}：`{layer.get('status') or 'not_assessed'}`。")
    return summaries


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


def _guidance_notes(result_registry: dict[str, Any]) -> list[str]:
    summary = result_registry.get("summary", {})
    if not isinstance(summary, dict):
        return []
    notes = summary.get("guidance_notes", [])
    if not isinstance(notes, list):
        return []
    return [str(note).strip() for note in notes if str(note).strip()]


def _calculation_context_lines(
    result_registry: dict[str, Any],
    parameter_registry: dict[str, Any],
) -> list[str]:
    lines: list[str] = []

    equations: list[str] = []
    solvers: list[str] = []
    for analysis in _identifiability_analysis(result_registry):
        equation = _guidance_text(analysis.get("equation")).strip()
        if equation and equation != "待确认" and equation not in equations:
            equations.append(equation)
        selection_rule = _guidance_text(analysis.get("selection_rule")).strip()
        if selection_rule and selection_rule != "待确认" and selection_rule not in solvers:
            solvers.append(selection_rule)

    if equations:
        lines.append("- 公式/模型方程：" + "；".join(f"`{equation}`" for equation in equations[:5]))
    else:
        lines.append("- 公式/模型方程：当前结果注册表未登记已执行模型，不能引用候选公式。")

    parameter_refs = _calculation_parameter_refs(parameter_registry)
    if parameter_refs:
        lines.append("- 参数证据：" + "；".join(parameter_refs[:8]))
    else:
        lines.append("- 参数证据：当前未登记可引用参数，不能发布未绑定来源的数值结论。")

    if solvers:
        lines.append("- 求解/拟合方法：" + "；".join(solvers[:6]))
    else:
        lines.append("- 求解/拟合方法：当前结果注册表未登记已执行求解规则。")
    return lines


def _calculation_parameter_refs(parameter_registry: dict[str, Any]) -> list[str]:
    parameters = parameter_registry.get("parameters", [])
    if not isinstance(parameters, list):
        return []
    refs: list[str] = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        parameter_id = _cell(parameter.get("id"))
        symbol = _cell(parameter.get("symbol"))
        status = _cell(parameter.get("trust_status"))
        refs.append(f"`{parameter_id}`/{symbol}（{status}）")
    return refs


def _data_source_lines(
    parameter_registry: dict[str, Any],
    result_registry: dict[str, Any],
) -> list[str]:
    sources = _registered_data_sources(parameter_registry, result_registry)
    source_text = "、".join(f"`{source}`" for source in sources[:8]) if sources else "参数表和结果表登记的 source_ref/source_data"
    return [
        f"- 来源文件/数据表：{source_text}。",
        "- 观测数据/输入字段：只把题设或附件中直接观测到的量写成 data evidence；未观测的目标、状态变量或支路量必须在可辨识性分析中标为 unknown。",
        "- 时间/样本口径：按题面、附件字段和参数表登记的 time/period/sample/unit 统一；若题面给的是采样序号，就禁止在正文混用分钟制延迟。",
        "- 预处理与可信状态：清洗、单位换算、缺失值处理和代表性假设必须追踪到参数与来源表或 result_registry，不得只写“系统已登记”。",
    ]


def _registered_data_sources(
    parameter_registry: dict[str, Any],
    result_registry: dict[str, Any],
) -> list[str]:
    sources: list[str] = []
    for parameter in parameter_registry.get("parameters", []):
        if not isinstance(parameter, dict):
            continue
        for key in ("source_ref", "source_location"):
            value = str(parameter.get(key) or "").strip()
            if value and value not in sources:
                sources.append(value)

    result_groups = (
        result_registry.get("verified_results", []),
        result_registry.get("blocked_results", []),
        result_registry.get("warning_results", []),
    )
    for group in result_groups:
        if not isinstance(group, list):
            continue
        for result in group:
            if not isinstance(result, dict):
                continue
            for key in ("source_data", "input_files", "source_ref"):
                value = result.get(key)
                if isinstance(value, list):
                    candidates = value
                else:
                    candidates = [value]
                for candidate in candidates:
                    text = str(candidate or "").strip()
                    if text and text not in sources:
                        sources.append(text)
    return sources


def _problem_understanding_lines(
    model: dict[str, Any],
    result_registry: dict[str, Any],
) -> list[str]:
    lines = [
        str(model.get("problem_summary") or "依据题目拆解契约完成当前子问题的建模与计算。").strip()
    ]

    for item in _raw_reader_decision_guide(result_registry):
        text = _guidance_text(_reader_decision_item_text(item)).strip()
        if text and text not in lines:
            lines.append(f"- {text}")

    combined = "\n".join(lines).lower()
    if not _text_has_any(combined, ("observ", "观测", "数据", "输入", "附件", "样本")):
        lines.append("- 观测数据/inputs：以数据与来源章节、参数注册表和 result_registry.source_data 登记为准。")
    if not _text_has_any(combined, ("unknown", "未知", "待求", "目标", "决策变量", "输出")):
        lines.append("- 未知目标/outputs：以已审核模型的目标、参数、状态变量和最终答案表为准。")
    if not _text_has_any(
        combined,
        ("time", "period", "sample", "coordinate", "时间", "时刻", "样本", "口径", "单位", "采样"),
    ):
        lines.append("- 时间/数据口径：以题设、附件字段、样本/period/time 单位和参数表登记为准。")
    return lines


def _raw_reader_decision_guide(result_registry: dict[str, Any]) -> list[Any]:
    summary = result_registry.get("summary", {})
    if not isinstance(summary, dict):
        return []
    guide = summary.get("reader_decision_guide", [])
    if not isinstance(guide, list):
        return []
    return guide


def _reader_decision_item_text(item: Any) -> str:
    if isinstance(item, dict):
        return "；".join(
            str(value)
            for value in (
                item.get("question") or item.get("topic"),
                item.get("judgment") or item.get("answer"),
                item.get("action") or item.get("guidance"),
            )
            if value not in {None, ""}
        )
    return str(item if item not in {None, ""} else "")


def _claim_registry(result_registry: dict[str, Any]) -> list[dict[str, Any]]:
    summary = result_registry.get("summary", {})
    if isinstance(summary, dict):
        claims = summary.get("claim_registry", [])
        if isinstance(claims, list):
            return [claim for claim in claims if isinstance(claim, dict)]
    return []


def _reader_decision_guide(result_registry: dict[str, Any]) -> list[dict[str, Any]]:
    summary = result_registry.get("summary", {})
    if isinstance(summary, dict):
        guide = summary.get("reader_decision_guide", [])
        if isinstance(guide, list):
            return [item for item in guide if isinstance(item, dict)]
    return []


def _method_route_comparison(result_registry: dict[str, Any]) -> list[dict[str, Any]]:
    summary = result_registry.get("summary", {})
    if isinstance(summary, dict):
        routes = summary.get("method_route_comparison", [])
        if isinstance(routes, list):
            return [item for item in routes if isinstance(item, dict)]
    return []


def _identifiability_analysis(result_registry: dict[str, Any]) -> list[dict[str, Any]]:
    summary = result_registry.get("summary", {})
    if isinstance(summary, dict):
        analysis = summary.get("identifiability_analysis", [])
        if isinstance(analysis, list):
            return [item for item in analysis if isinstance(item, dict)]
    return []


def _registered_subproblem_modeling_lines(
    result_registry: dict[str, Any],
    model: dict[str, Any],
) -> list[str]:
    analyses = _identifiability_analysis(result_registry)
    answer_tables = _final_answer_tables(result_registry)
    lines: list[str] = []
    if analyses:
        for analysis in analyses:
            subproblem_id = str(analysis.get("subproblem_id") or "unknown")
            lines.extend([
                f"### 子问题 {_cell(subproblem_id)}",
                "",
                f"- 目标：{_guidance_text(analysis.get('guidance'))}",
                f"- 已执行模型/公式：`{_guidance_text(analysis.get('equation'))}`",
                f"- 参数或未知量：{_guidance_text(analysis.get('unknowns'))}",
                f"- 观测输入：{_guidance_text(analysis.get('observed'))}",
                f"- 约束与秩条件：{_guidance_text(analysis.get('rank_condition'))}",
                f"- 缺失信息/适用边界：{_guidance_text(analysis.get('missing_information'))}",
                "- 求解步骤 1：按已登记观测构建设计矩阵并拟合上述模型。",
                f"- 求解步骤 2：{_guidance_text(analysis.get('selection_rule'))}",
                f"- 唯一性结论：{_guidance_text(analysis.get('conclusion'))}",
            ])
            for row in _matching_final_answer_rows(answer_tables, subproblem_id):
                lines.append(
                    "- 已登记输出：{item}；答案：{answer}；状态：{status}；证据：{evidence}。".format(
                        item=_guidance_text(row.get("answer_item")),
                        answer=_guidance_text(row.get("answer") or row.get("blocked_reason")),
                        status=_cell(row.get("status")),
                        evidence=_cell(_join_claim_items(row.get("evidence_ids"))),
                    )
                )
            lines.append("")

        for subproblem in model.get("subproblem_models", []):
            if isinstance(subproblem, dict) and subproblem.get("execution_status") == "blocked":
                lines.extend(_blocked_subproblem_modeling_lines(subproblem))
        return lines

    for subproblem in model.get("subproblem_models", []):
        if not isinstance(subproblem, dict):
            continue
        if subproblem.get("execution_status") == "blocked":
            lines.extend(_blocked_subproblem_modeling_lines(subproblem))
            continue
        lines.extend([
            f"### 子问题 {_cell(subproblem.get('subproblem_id'))}",
            "",
            f"- 目标：{_guidance_text(subproblem.get('objective'))}",
            f"- 方法：{_guidance_text(subproblem.get('selected_method'))}",
            f"- 选择理由：{_guidance_text(subproblem.get('rationale'))}",
        ])
        for equation in subproblem.get("equations", []):
            lines.append(f"- 公式：`{_guidance_text(equation)}`")
        for parameter in subproblem.get("parameters", []):
            if isinstance(parameter, dict):
                lines.append(
                    f"- 参数 `{_cell(parameter.get('symbol'))}`："
                    f"{_guidance_text(parameter.get('meaning'))}。"
                )
        for constraint in subproblem.get("constraints", []):
            lines.append(f"- 约束：{_guidance_text(constraint)}")
        for index, step in enumerate(subproblem.get("solve_steps", []), start=1):
            lines.append(f"- 求解步骤 {index}：{_guidance_text(step)}")
        for result in subproblem.get("expected_results", []):
            lines.append(f"- 预期结果：{_guidance_text(result)}")
        lines.append("")
    return lines


def _matching_final_answer_rows(
    answer_tables: list[dict[str, Any]],
    analysis_id: str,
) -> list[dict[str, Any]]:
    base_id = analysis_id.split("-", 1)[0]
    rows: list[dict[str, Any]] = []
    for table in answer_tables:
        table_rows = table.get("rows", [])
        if not isinstance(table_rows, list):
            continue
        for row in table_rows:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("subproblem_id") or "")
            if row_id == analysis_id or row_id == base_id:
                rows.append(row)
    return rows


def _blocked_subproblem_modeling_lines(subproblem: dict[str, Any]) -> list[str]:
    lines = [
        f"### 子问题 {_cell(subproblem.get('subproblem_id'))}",
        "",
        f"- 目标：{_guidance_text(subproblem.get('objective'))}",
        "- 执行状态：`blocked`，不生成公式、参数或数值。",
        f"- 阻断原因：{_guidance_text(subproblem.get('blocking_reason'))}",
        f"- 缺失输入：{_guidance_text(_join_claim_items(subproblem.get('missing_inputs')))}",
        f"- 恢复动作：{_guidance_text(subproblem.get('recovery_action'))}",
    ]
    for result in subproblem.get("expected_results", []):
        lines.append(f"- 恢复后预期输出：{_guidance_text(result)}")
    lines.append("")
    return lines


def _final_answer_tables(result_registry: dict[str, Any]) -> list[dict[str, Any]]:
    summary = result_registry.get("summary", {})
    if isinstance(summary, dict):
        tables = summary.get("final_answer_tables", [])
        if isinstance(tables, list):
            return [table for table in tables if isinstance(table, dict)]
    return []


def _final_answer_evidence_ids(result_registry: dict[str, Any]) -> list[str]:
    evidence_ids: list[str] = []
    for table in _final_answer_tables(result_registry):
        rows = table.get("rows", [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_evidence = row.get("evidence_ids", [])
            if not isinstance(row_evidence, list):
                continue
            for evidence_id in row_evidence:
                value = str(evidence_id).strip()
                if value and value not in evidence_ids:
                    evidence_ids.append(value)
    return evidence_ids


def _verified_result_ids(result_registry: dict[str, Any]) -> list[str]:
    verified = result_registry.get("verified_results", [])
    if not isinstance(verified, list):
        return []
    result_ids: list[str] = []
    for result in verified:
        if isinstance(result, dict):
            result_id = str(result.get("id") or "").strip()
            if result_id:
                result_ids.append(result_id)
    return result_ids


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


def _text_has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _cell(value: Any) -> str:
    text = str(value if value not in {None, ""} else "待确认")
    return text.replace("|", "\\|").replace("\n", " ")
