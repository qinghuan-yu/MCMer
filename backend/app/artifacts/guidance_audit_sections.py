"""guidance audit sections responsibilities."""

from __future__ import annotations

from pathlib import Path
from app.artifacts.guidance_audit_evidence import _registered_source_refs
from app.artifacts.guidance_audit_tables import _FINAL_ANSWER_EVIDENCE_TOKENS, _contains_any, _markdown_section


_DISCLOSURE_TOKENS = (
    "blocked", "partial", "unverified", "阻断", "未完成", "无法", "待确认", "部分",
)


def _check_figure_coverage(
    text: str,
    figure_registry: dict[str, object],
    artifact_root: Path | None,
    blocks: list[dict[str, object]],
) -> None:
    figures = figure_registry.get("figures", [])
    if not isinstance(figures, list):
        return
    for figure in figures:
        if not isinstance(figure, dict):
            continue
        path = str(figure.get("path") or "").strip()
        if path and artifact_root is not None and not (artifact_root / path).is_file():
            blocks.append({
                "type": "missing_figure_file",
                "location": path,
                "route": "calculation",
                "severity": "high",
                "fix": "Regenerate the registered figure before publishing guidance.",
            })
        required_refs = [
            path,
            str(figure.get("role") or "").strip(),
            *[str(item) for item in figure.get("source_data", [])],
            *[str(item) for item in figure.get("linked_result_ids", [])],
        ]
        missing_refs = [ref for ref in required_refs if ref and ref not in text]
        if missing_refs:
            blocks.append({
                "type": "missing_figure_binding_disclosure",
                "location": path or "图片解读",
                "route": "writer",
                "severity": "high",
                "fix": "List each figure's role, source data, and linked result IDs.",
                "missing_refs": missing_refs,
            })


def _check_figure_interpretation_context(
    text: str,
    guidance_context: dict[str, object],
    figure_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    required_text = "\n".join(str(section) for section in required_sections)
    if "图片解读" not in required_text or "最终答案与应填表格" not in required_text:
        return

    figures = figure_registry.get("figures", [])
    if not isinstance(figures, list) or not figures:
        return

    section = _markdown_section(text, "图片解读")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(
        lowered,
        ("interpret", "explain", "support", "show", "indicate", "说明", "解释", "支撑", "展示", "反映", "用于判断"),
    ):
        missing.append("interpretation_or_supported_judgment")
    if not _contains_any(
        lowered,
        ("limit", "cannot", "not prove", "only", "boundary", "risk", "局限", "不能", "不等于", "只说明", "边界", "风险"),
    ):
        missing.append("limitation_or_nonproof")

    if missing:
        blocks.append({
            "type": "figure_section_missing_interpretation",
            "location": "图片解读",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the figure section to state what each figure supports and what it cannot prove, especially for fitted or decomposed results.",
            "missing_items": missing,
        })


def _check_required_sections(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return

    for section in required_sections:
        section_text = str(section).strip()
        if section_text and section_text not in text:
            blocks.append({
                "type": "missing_expected_section",
                "location": section_text,
                "route": "writer",
                "severity": "high",
                "fix": f"Add or clearly label the section: {section_text}",
            })


def _check_reader_guidance_value(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    required_text = "\n".join(str(section) for section in required_sections)
    formal_markers = (
        "建模路线与读者决策",
        "建模路线取舍",
        "可辨识性分析",
        "结论状态登记",
        "最终答案与应填表格",
    )
    if not all(marker in required_text for marker in formal_markers):
        return

    lowered = text.lower()
    missing: list[str] = []
    if not (
        _contains_any(lowered, ("observed", "observation", "观测", "数据", "输入"))
        and _contains_any(lowered, ("unknown", "未知", "待求", "决策变量", "目标"))
        and _contains_any(lowered, ("time", "period", "sample", "coordinate", "时间", "时刻", "样本", "口径", "not applicable"))
    ):
        missing.append("observed_unknown_time_context")
    if not _contains_any(
        lowered,
        ("unique", "identifi", "rank", "missing information", "可辨识", "唯一", "不可", "秩", "缺少"),
    ):
        missing.append("identifiability_or_missing_information")
    if not (
        _contains_any(lowered, ("recommended", "recommend", "推荐"))
        and _contains_any(lowered, ("rejected", "excluded", "not recommended", "被排除", "排除", "不推荐"))
    ):
        missing.append("recommended_and_rejected_routes")
    if not (
        _contains_any(lowered, ("formula", "equation", "function", "公式", "方程", "函数", "模型"))
        and _contains_any(lowered, ("parameter", "param_", "参数"))
        and _contains_any(lowered, ("solve", "solver", "fit", "optimiz", "求解", "拟合", "优化"))
        and _contains_any(lowered, ("final answer", "answer table", "最终答案", "应填", "答案", "result_"))
    ):
        missing.append("formula_parameter_solver_final_answer")
    if not (
        _contains_any(lowered, ("data", "evidence", "result_", "数据", "证据"))
        and _contains_any(lowered, ("assumption", "prior", "假设", "先验", "规范化"))
        and _contains_any(lowered, ("candidate", "representative", "contest", "chosen", "候选", "代表", "竞赛"))
    ):
        missing.append("claim_source_layers")

    if missing:
        blocks.append({
            "type": "vague_reader_guidance",
            "location": "guidance.md",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite formal guidance so it directly answers observation/unknown/time, identifiability, route choice, formulas/results, and claim source layers.",
            "missing_items": missing,
        })


def _check_reader_decision_section_structure(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    if "建模路线与读者决策" not in "\n".join(str(section) for section in required_sections):
        return

    section = _markdown_section(text, "建模路线与读者决策")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not (
        _contains_any(lowered, ("observed", "observation", "观测", "数据", "输入"))
        and _contains_any(lowered, ("unknown", "未知", "待求", "目标", "输出"))
        and _contains_any(
            lowered,
            ("time", "period", "sample", "coordinate", "时间", "时刻", "样本", "口径", "单位"),
        )
    ):
        missing.append("observed_unknown_time_context")
    if not _contains_any(
        lowered,
        ("unique", "identifi", "rank", "missing information", "可辨识", "唯一", "不可", "秩", "缺少"),
    ):
        missing.append("identifiability_or_missing_information")
    if not (
        _contains_any(lowered, ("recommended", "recommend", "chosen", "采用", "推荐", "选择"))
        and _contains_any(lowered, ("rejected", "excluded", "not recommended", "排除", "不推荐", "放弃"))
    ):
        missing.append("recommended_and_rejected_routes")
    if not (
        _contains_any(lowered, ("final answer", "answer table", "最终答案", "应填", "可填", "答案"))
        and _contains_any(lowered, _FINAL_ANSWER_EVIDENCE_TOKENS)
        and _contains_any(lowered, ("action", "guidance", "write", "modeling", "建模", "写作", "动作"))
    ):
        missing.append("final_answer_evidence_and_action")
    if not (
        _contains_any(lowered, ("data", "evidence", "result_", "数据", "证据"))
        and _contains_any(lowered, ("assumption", "prior", "假设", "先验", "规范化"))
        and _contains_any(
            lowered,
            ("candidate", "representative", "contest", "chosen", "候选", "代表", "竞赛"),
        )
    ):
        missing.append("claim_source_layers")

    if missing:
        blocks.append({
            "type": "reader_decision_section_missing_actionable_summary",
            "location": "建模路线与读者决策",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the reader-decision section itself to answer observation/unknown/time, identifiability, recommended and rejected routes, final-answer evidence/actions, and claim-source layers.",
            "missing_items": missing,
        })


def _check_problem_understanding_section_context(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    if "问题理解" not in "\n".join(str(section) for section in required_sections):
        return

    section = _markdown_section(text, "问题理解")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(lowered, ("observ", "观测", "数据", "输入", "附件", "样本")):
        missing.append("observed_data_or_inputs")
    if not _contains_any(lowered, ("unknown", "未知", "待求", "目标", "决策变量", "输出")):
        missing.append("unknown_target_or_outputs")
    if not _contains_any(
        lowered,
        ("time", "period", "sample", "coordinate", "时间", "时刻", "样本", "口径", "单位", "采样"),
    ):
        missing.append("time_or_data_coordinate")

    if missing:
        blocks.append({
            "type": "problem_understanding_missing_context",
            "location": "问题理解",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the problem-understanding section to state observed data or inputs, unknown targets or outputs, and the time/data coordinate system.",
            "missing_items": missing,
        })


def _check_data_source_section_context(
    text: str,
    guidance_context: dict[str, object],
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    required_text = "\n".join(str(section) for section in required_sections)
    if "数据与来源" not in required_text or "最终答案与应填表格" not in required_text:
        return

    section = _markdown_section(text, "数据与来源")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(
        lowered,
        ("source", "file", "table", "dataset", "来源", "附件", "数据表", "文件", "source_ref"),
    ):
        missing.append("source_file_or_table")
    if not _contains_any(
        lowered,
        ("observ", "input", "field", "column", "sample", "观测", "输入", "字段", "列", "样本"),
    ):
        missing.append("observed_data_or_fields")
    if not _contains_any(
        lowered,
        ("time", "period", "sample", "coordinate", "unit", "时间", "时刻", "样本", "采样", "口径", "单位"),
    ):
        missing.append("time_or_data_coordinate")

    if missing:
        blocks.append({
            "type": "data_section_missing_observation_context",
            "location": "数据与来源",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the data/source section to name the source files or tables, observed inputs or fields, and the time/sample/data coordinate system.",
            "missing_items": missing,
        })
        return

    registered_sources = _registered_source_refs(result_registry, parameter_registry)
    missing_refs = [source for source in registered_sources if source not in section]
    if registered_sources and len(missing_refs) == len(registered_sources):
        blocks.append({
            "type": "data_section_missing_registered_source_reference",
            "location": "数据与来源",
            "route": "writer",
            "severity": "high",
            "fix": "Reference at least one concrete source file, table, or data ID from parameter_registry or result_registry in the data/source section.",
            "registered_sources": registered_sources[:10],
        })


def _check_identifiability_section_structure(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    if "可辨识性分析" not in "\n".join(str(section) for section in required_sections):
        return

    section = _markdown_section(text, "可辨识性分析")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(
        lowered,
        ("observ", "观测", "输入", "equation", "方程", "设计矩阵", "design matrix", "jacobian", "矩阵"),
    ):
        missing.append("observation_or_equation")
    if not _contains_any(
        lowered,
        ("unknown", "未知", "parameter", "参数", "dimension", "维数", "自由度", "degree"),
    ):
        missing.append("unknown_or_parameter_dimension")
    if not _contains_any(
        lowered,
        ("rank", "秩", "jacobian", "设计矩阵", "design matrix", "自由度", "degree"),
    ):
        missing.append("rank_or_degrees_of_freedom")
    if not _contains_any(
        lowered,
        ("unique", "唯一", "不可", "non-unique", "missing", "缺少", "规范化", "assumption", "先验", "代表", "阻断"),
    ):
        missing.append("uniqueness_or_missing_information_conclusion")

    if missing:
        blocks.append({
            "type": "identifiability_section_missing_structure",
            "location": "可辨识性分析",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the identifiability section to state observations/equations, unknown parameters or dimensions, rank/degrees of freedom, and the uniqueness or missing-information conclusion.",
            "missing_items": missing,
        })


def _check_route_tradeoff_section_structure(
    text: str,
    guidance_context: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return
    if "建模路线取舍" not in "\n".join(str(section) for section in required_sections):
        return

    section = _markdown_section(text, "建模路线取舍")
    if not section.strip():
        return

    lowered = section.lower()
    missing: list[str] = []
    if not _contains_any(lowered, ("recommended", "recommend", "chosen", "采用", "推荐", "选择")):
        missing.append("recommended_route")
    if not _contains_any(lowered, ("rejected", "excluded", "not recommended", "排除", "不推荐", "放弃")):
        missing.append("rejected_or_excluded_route")
    if not _contains_any(
        lowered,
        ("because", "reason", "criteria", "criterion", "tradeoff", "why", "原因", "理由", "准则", "取舍", "因为"),
    ):
        missing.append("reason_or_selection_criterion")
    if not _contains_any(
        lowered,
        ("boundary", "applicable", "applicability", "condition", "limit", "scope", "适用", "边界", "条件", "局限", "范围"),
    ):
        missing.append("applicability_boundary")
    if not _contains_any(lowered, ("evidence", "source", "result_", "data", "证据", "来源", "数据")):
        missing.append("evidence_source")

    if missing:
        blocks.append({
            "type": "route_tradeoff_section_missing_structure",
            "location": "建模路线取舍",
            "route": "writer",
            "severity": "high",
            "fix": "Rewrite the route tradeoff section to include recommended routes, rejected routes, reasons or criteria, applicability boundaries, and evidence sources.",
            "missing_items": missing,
        })


def _check_blocked_disclosure(
    text: str,
    result_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    blocked = result_registry.get("blocked_results", [])
    if not isinstance(blocked, list) or not blocked:
        return

    lowered = text.lower()
    if any(token in lowered for token in _DISCLOSURE_TOKENS):
        return

    blocks.append({
        "type": "blocked_items_not_disclosed",
        "location": "guidance.md",
        "route": "writer",
        "severity": "high",
        "fix": "Disclose blocked, partial, or unverified results in the final guidance.",
    })
