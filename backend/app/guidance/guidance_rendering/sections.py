"""Guidance 职责边界：guidance_rendering.sections。"""

from __future__ import annotations

from typing import Any

from app.guidance.guidance_rendering.data import (
    _cell,
    _final_answer_tables,
    _guidance_text,
    _identifiability_analysis,
    _join_claim_items,
    _raw_reader_decision_guide,
    _reader_decision_item_text,
    _text_has_any,
)


def _append_fact_traceability(
    markdown: str,
    *,
    problem_spec: dict[str, Any] | None,
    model: dict[str, Any],
) -> str:
    if not isinstance(problem_spec, dict):
        return markdown
    facts = problem_spec.get("locked_facts", [])
    uncertainties = problem_spec.get("uncertainties", [])
    if not isinstance(facts, list) or not isinstance(uncertainties, list):
        return markdown

    model_paths: dict[str, list[str]] = {}
    for subproblem in model.get("subproblems", []):
        if not isinstance(subproblem, dict):
            continue
        subproblem_id = str(subproblem.get("subproblem_id") or "")
        for binding in subproblem.get("fact_bindings", []):
            if not isinstance(binding, dict):
                continue
            fact_id = str(binding.get("fact_id") or "")
            paths = binding.get("model_paths", [])
            if not fact_id or not isinstance(paths, list):
                continue
            model_paths.setdefault(fact_id, []).extend(
                f"{subproblem_id}:{path}" for path in paths
            )

    lines = [markdown.rstrip(), "", "## 题面事实追踪", ""]
    if facts:
        lines.extend([
            "| fact_id | 题面事实 | 值/单位 | 适用子问题 | InputCatalog 来源 | ModelIR 路径 |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            value_unit = f"{fact.get('value')} {fact.get('unit') or ''}".strip()
            lines.append(
                "| {fact_id} | {statement} | {value_unit} | {scope} | {sources} | {paths} |".format(
                    fact_id=_cell(fact.get("fact_id")),
                    statement=_cell(fact.get("statement")),
                    value_unit=_cell(value_unit),
                    scope=_cell(_join_claim_items(fact.get("subproblem_ids"))),
                    sources=_cell(_join_claim_items(fact.get("source_refs"))),
                    paths=_cell(_join_claim_items(model_paths.get(str(fact.get("fact_id")), []))),
                )
            )
    else:
        lines.append("- ProblemSpec 未登记 locked facts。")

    lines.extend(["", "### 题意歧义", ""])
    if uncertainties:
        for uncertainty in uncertainties:
            if not isinstance(uncertainty, dict):
                continue
            lines.append(
                "- `{id}`：{statement}；作用域：{scope}；候选值：{values}；来源：{sources}。".format(
                    id=_cell(uncertainty.get("uncertainty_id")),
                    statement=_cell(uncertainty.get("statement")),
                    scope=_cell(_join_claim_items(uncertainty.get("subproblem_ids"))),
                    values=_cell(_join_claim_items(uncertainty.get("candidate_values"))),
                    sources=_cell(_join_claim_items(uncertainty.get("source_refs"))),
                )
            )
    else:
        lines.append("- ProblemSpec 未登记题意歧义。")
    return "\n".join(lines) + "\n"

def _verification_layers(verification_report: dict[str, Any]) -> list[tuple[str, dict]]:
    adequacy = verification_report.get("model_adequate")
    adequacy = adequacy if isinstance(adequacy, dict) else {}
    return [
        (label, layer if isinstance(layer, dict) else {})
        for label, layer in (
            ("执行正确性", verification_report.get("execution_verified")),
            ("证据可追溯性", verification_report.get("evidence_supported")),
            ("题面事实覆盖", adequacy.get("source_coverage")),
            ("数学对应审核", adequacy.get("mathematical_review")),
            ("数值复核", adequacy.get("numerical_validation")),
            ("模型充分性", adequacy),
        )
    ]


def _verification_layer_rows(verification_report: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for label, layer in _verification_layers(verification_report):
        layer_status = _cell(layer.get("status") or "not_assessed")
        reasons = layer.get("reasons")
        reason_text = _join_claim_items(reasons) or "未登记说明"
        rows.append(f"| {label} | `{layer_status}` | {_cell(reason_text)} |")
    return rows

def _verification_layer_summaries(verification_report: dict[str, Any]) -> list[str]:
    summaries: list[str] = []
    for label, layer in _verification_layers(verification_report):
        summaries.append(f"{label}：`{layer.get('status') or 'not_assessed'}`。")
    return summaries

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
