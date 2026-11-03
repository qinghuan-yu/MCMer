"""guidance audit answers responsibilities."""

from __future__ import annotations

from app.artifacts.guidance_audit_tables import _FINAL_ANSWER_EVIDENCE_TOKENS, _FINAL_ANSWER_STATUS_TOKENS, _contains_any, _final_answer_table_rows_missing_claim_status, _final_answer_table_rows_missing_concrete_answer, _final_answer_table_rows_missing_evidence, _markdown_section, _unknown_final_answer_evidence_refs


def _check_claim_registry_section_structure(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    if "结论状态登记" not in "\n".join(str(section) for section in required_sections):
        return

    section = _markdown_section(text, "结论状态登记")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(lowered, _FINAL_ANSWER_STATUS_TOKENS + ("status", "状态", "结论状态")):
        missing.append("claim_status_or_layer")
    if not _contains_any(lowered, ("evidence", "source", "result_", "param_", "data", "证据", "来源", "数据")):
        missing.append("evidence_or_source")
    if not _contains_any(
        lowered,
        ("assumption", "prior", "representative", "candidate", "contest", "risk", "假设", "先验", "代表", "候选", "竞赛", "规范化", "风险", "阻断"),
    ):
        missing.append("assumption_or_representative_boundary")

    if missing:
        blocks.append({
            "type": "claim_registry_section_missing_structure",
            "location": "结论状态登记",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the claim registry section to classify each key conclusion by status, bind it to evidence or source IDs, and disclose assumptions, representative-choice boundaries, risks, or blocked status.",
            "missing_items": missing,
        })


def _check_calculation_results_section_structure(
    text: str,
    guidance_context: dict[str, object],
    parameter_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    if "必要计算结果" not in "\n".join(str(section) for section in required_sections):
        return

    section = _markdown_section(text, "必要计算结果")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(lowered, ("formula", "equation", "function", "model", "公式", "方程", "函数", "模型")):
        missing.append("formula_or_model")
    if not (
        _contains_any(lowered, ("parameter", "param_", "参数"))
        or _section_mentions_registered_parameter(section, parameter_registry)
    ):
        missing.append("parameter_context")
    if not _contains_any(
        lowered,
        ("solve", "solver", "fit", "fitting", "least squares", "optimiz", "forecast", "求解", "拟合", "优化", "预测"),
    ):
        missing.append("solver_or_method")
    if not _contains_any(
        lowered,
        ("result_", "metric", "rmse", "residual", "objective", "forecast", "value", "结果", "指标", "误差", "残差", "目标值", "数值"),
    ):
        missing.append("result_or_metric")

    if missing:
        blocks.append({
            "type": "calculation_results_section_missing_structure",
            "location": "必要计算结果",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the calculation-results section to include formulas or model equations, parameter context, solver or fitting method, and concrete result IDs, values, or error metrics.",
            "missing_items": missing,
        })


def _check_subproblem_steps_section_structure(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    if "分问题建模步骤" not in "\n".join(str(section) for section in required_sections):
        return

    section = _markdown_section(text, "分问题建模步骤")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(lowered, ("objective", "target", "goal", "目标", "待求", "输出")):
        missing.append("objective_or_target")
    if not _contains_any(
        lowered,
        ("formula", "equation", "function", "model", "公式", "方程", "函数", "模型"),
    ):
        missing.append("formula_or_model")
    if not _contains_any(lowered, ("parameter", "param_", "alpha", "beta", "theta", "参数")):
        missing.append("parameter_definition")
    if not _contains_any(
        lowered,
        ("constraint", "assumption", "boundary", "condition", "limit", "约束", "假设", "边界", "条件", "局限"),
    ):
        missing.append("constraint_or_assumption_boundary")
    if not _contains_any(
        lowered,
        ("solve", "solver", "step", "fit", "least squares", "optimiz", "estimate", "求解", "步骤", "拟合", "优化", "估计"),
    ):
        missing.append("solver_or_step")
    if not _contains_any(lowered, ("result_", "expected", "output", "table", "结果", "输出", "应填", "表格")):
        missing.append("expected_output_or_result")

    if missing:
        blocks.append({
            "type": "subproblem_steps_missing_actionable_modeling",
            "location": "分问题建模步骤",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite each subproblem step so it gives the modeling target, formula or model, parameters, constraints or assumptions, solver steps, and expected outputs.",
            "missing_items": missing,
        })


def _check_final_answer_evidence_binding(
    text: str,
    guidance_context: dict[str, object],
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    if "最终答案与应填表格" not in "\n".join(str(section) for section in required_sections):
        return

    section = _markdown_section(text, "最终答案与应填表格")
    if not section.strip():
        return

    lowered = section.lower()
    if not _contains_any(lowered, _FINAL_ANSWER_EVIDENCE_TOKENS):
        blocks.append({
            "type": "final_answer_missing_evidence_binding",
            "location": "最终答案与应填表格",
            "route": "writer",
            "severity": "high",
            "fix": "Bind final-answer rows to result_registry or parameter_registry IDs, or mark the row as blocked.",
        })
        return

    missing_rows = _final_answer_table_rows_missing_evidence(section)
    if missing_rows:
        blocks.append({
            "type": "final_answer_missing_evidence_binding",
            "location": "最终答案与应填表格",
            "route": "writer",
            "severity": "high",
            "fix": "Bind every final-answer table row to result_registry or parameter_registry IDs, or mark it as blocked.",
            "missing_rows": missing_rows[:5],
        })
        return

    unknown_refs = _unknown_final_answer_evidence_refs(section, result_registry, parameter_registry)
    if unknown_refs:
        blocks.append({
            "type": "final_answer_unknown_evidence_reference",
            "location": "最终答案与应填表格",
            "route": "writer",
            "severity": "high",
            "fix": "Reference only IDs that exist in result_registry or parameter_registry.",
            "unknown_refs": unknown_refs[:10],
        })

    missing_status_rows = _final_answer_table_rows_missing_claim_status(section)
    if missing_status_rows:
        blocks.append({
            "type": "final_answer_missing_claim_status",
            "location": "最终答案与应填表格",
            "route": "writer",
            "severity": "high",
            "fix": "Add a claim status to every final-answer row: source-locked, evidence-verified, chosen-under-prior, candidate-design, or blocked.",
            "missing_rows": missing_status_rows[:5],
        })

    missing_concrete_rows = _final_answer_table_rows_missing_concrete_answer(section)
    if missing_concrete_rows:
        blocks.append({
            "type": "final_answer_missing_concrete_answer",
            "location": "最终答案与应填表格",
            "route": "writer",
            "severity": "high",
            "fix": "Write the formula, value, observation times, or blocked reason directly in each final-answer row instead of only pointing to registry IDs.",
            "missing_rows": missing_concrete_rows[:5],
        })


def _check_parameter_source_table_structure(
    text: str,
    guidance_context: dict[str, object],
    parameter_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    required_text = "\n".join(str(section) for section in required_sections)
    if "参数与来源表" not in required_text or "最终答案与应填表格" not in required_text:
        return
    parameters = parameter_registry.get("parameters", [])
    if not isinstance(parameters, list) or not parameters:
        return

    section = _markdown_section(text, "参数与来源表")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(lowered, ("value", "数值", "取值", "值")):
        missing.append("value")
    if not _contains_any(lowered, ("unit", "单位")):
        missing.append("unit")
    if not _contains_any(lowered, ("source", "来源", "source_type", "source_ref", "source_location", "数据", "附件")):
        missing.append("source")
    if not _contains_any(lowered, ("trust", "status", "可信", "状态", "source_locked", "evidence_verified", "assumed", "estimated")):
        missing.append("trust_status")

    if missing:
        blocks.append({
            "type": "parameter_table_missing_source_structure",
            "location": "参数与来源表",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the parameter table to include each parameter's value, unit, source or source type, and trust/status so readers can distinguish data-derived, assumed, and representative parameters.",
            "missing_items": missing,
        })


def _check_review_section_error_context(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    required_text = "\n".join(str(section) for section in required_sections)
    if "复核与稳健性" not in required_text or "最终答案与应填表格" not in required_text:
        return

    section = _markdown_section(text, "复核与稳健性")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(
        lowered,
        ("rmse", "residual", "error", "objective", "metric", "误差", "残差", "目标值", "指标"),
    ):
        missing.append("error_or_metric")
    if not _contains_any(
        lowered,
        ("threshold", "baseline", "tolerance", "criterion", "阈值", "基线", "容许", "准则", "对照"),
    ):
        missing.append("threshold_or_baseline")
    if not _contains_any(
        lowered,
        ("interpret", "explain", "means", "说明", "解释", "判断", "不能", "代表"),
    ):
        missing.append("interpretation")
    if not _contains_any(
        lowered,
        ("robust", "sensitivity", "stability", "稳健", "敏感", "稳定"),
    ):
        missing.append("robustness_or_sensitivity")

    if missing:
        blocks.append({
            "type": "review_section_missing_error_context",
            "location": "复核与稳健性",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the review section to explain error or residual metrics, thresholds or baselines, interpretation of pass/fail meaning, and robustness or sensitivity checks.",
            "missing_items": missing,
        })


def _section_mentions_registered_parameter(section: str, parameter_registry: dict[str, object]) -> bool:
    parameters = parameter_registry.get("parameters", [])
    if not isinstance(parameters, list):
        return False
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        for key in ("id", "symbol"):
            value = str(parameter.get(key) or "").strip()
            if value and value in section:
                return True
    return False
