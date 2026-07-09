"""Machine checks for high-trust guidance deliverables."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_NUMBER_RE = re.compile(r"(?<![\w.])-?(?:\d+\.\d+|\d+)(?:[eE][+-]?\d+)?(?![\w.])")
_URL_RE = re.compile(r"https?://\S+")
_DISCLOSURE_TOKENS = (
    "blocked",
    "partial",
    "unverified",
    "阻断",
    "未完成",
    "无法",
    "待确认",
    "部分",
)
_FINAL_ANSWER_EVIDENCE_TOKENS = ("result_", "param_", "blocked", "阻断")
_FINAL_ANSWER_STATUS_TOKENS = (
    "source_locked",
    "evidence_verified",
    "chosen_under_prior",
    "candidate_design",
    "blocked",
    "partial",
    "unverified",
    "assumption",
    "prior",
    "representative",
    "contest",
    "candidate",
    "题设锁定",
    "数据锁定",
    "证据自洽",
    "依赖先验",
    "依赖假设",
    "代表",
    "候选",
    "竞赛",
    "规范化",
    "阻断",
)
_EVIDENCE_ID_RE = re.compile(r"\b(?:result|param)_[A-Za-z0-9_.:-]+\b")
_FINAL_ANSWER_VALUE_HEADERS = (
    "答案",
    "当前可填内容",
    "可填内容",
    "函数",
    "数值",
    "观测时刻",
    "answer",
    "value",
)


def build_guidance_audit_report(
    *,
    guidance_markdown: str,
    guidance_context: dict[str, object] | None,
    result_registry: dict[str, object] | None,
    parameter_registry: dict[str, object] | None = None,
    figure_registry: dict[str, object] | None = None,
    artifact_root: Path | None = None,
) -> dict[str, object]:
    """Audit whether a guidance document is traceable enough to trust."""
    text = guidance_markdown or ""
    context = guidance_context if isinstance(guidance_context, dict) else {}
    results = result_registry if isinstance(result_registry, dict) else {}
    parameters = parameter_registry if isinstance(parameter_registry, dict) else {}
    figures = figure_registry if isinstance(figure_registry, dict) else {}

    blocks: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []

    _check_required_sections(text, context, blocks)
    _check_blocked_disclosure(text, results, blocks)
    if not _is_blocking_diagnostic(text):
        _check_reader_guidance_value(text, context, blocks)
        _check_route_tradeoff_section_structure(text, context, blocks)
        _check_identifiability_section_structure(text, context, blocks)
        _check_final_answer_evidence_binding(text, context, results, parameters, blocks)
        _check_parameter_coverage(text, parameters, blocks)
        _check_minimum_guidance_evidence(results, parameters, blocks)
        _check_registered_numbers(text, results, parameters, blocks)
        _check_figure_coverage(text, figures, artifact_root, blocks)

    status = "PASS"
    if blocks:
        status = "BLOCK"
    elif warnings:
        status = "PARTIAL"

    return {
        "artifact_type": "guidance",
        "status": status,
        "scores": _score_report(
            text=text,
            blocks=blocks,
            warnings=warnings,
            result_registry=results,
            parameter_registry=parameters,
        ),
        "blocks": blocks,
        "warnings": warnings,
    }


def _is_blocking_diagnostic(text: str) -> bool:
    return text.startswith((
        "# 计算链路阻断诊断",
        "# 建模链路阻断诊断",
        "# 结果复核阻断诊断",
    ))


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


def _check_parameter_coverage(
    text: str,
    parameter_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    parameters = parameter_registry.get("parameters", [])
    if not isinstance(parameters, list) or not parameters:
        return

    missing: list[str] = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        parameter_id = str(parameter.get("id") or "").strip()
        symbol = str(parameter.get("symbol") or "").strip()
        if not parameter_id and not symbol:
            continue
        if (parameter_id and parameter_id in text) or (symbol and symbol in text):
            continue
        missing.append(parameter_id or symbol)

    if missing:
        blocks.append({
            "type": "missing_parameter_registry_coverage",
            "location": "参数与来源表",
            "route": "writer",
            "severity": "high",
            "fix": "Add every registered core parameter to the parameter/source table.",
            "missing_parameter_ids": missing,
        })


def _check_minimum_guidance_evidence(
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    verified = result_registry.get("verified_results", [])
    parameters = parameter_registry.get("parameters", [])
    has_verified = isinstance(verified, list) and bool(verified)
    has_parameters = isinstance(parameters, list) and bool(parameters)
    if has_verified or has_parameters:
        return

    blocks.append({
        "type": "no_verified_guidance_evidence",
        "location": "guidance.md",
        "route": "writer",
        "severity": "high",
        "fix": "Do not publish guidance as trusted until it contains verified results or parameter evidence.",
    })


def _check_registered_numbers(
    text: str,
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
    blocks: list[dict[str, object]],
) -> None:
    text_numbers = _extract_numbers(text)
    if not text_numbers:
        return

    registered = _registered_numbers(result_registry, parameter_registry)
    unregistered = sorted(number for number in text_numbers if not _is_registered_number(number, registered))
    if not unregistered:
        return

    blocks.append({
        "type": "unregistered_numeric_claim",
        "location": "guidance.md",
        "route": "writer",
        "severity": "high",
        "fix": "Bind every concrete numeric claim to result_registry or parameter_registry.",
        "numbers": unregistered[:20],
    })


def _extract_numbers(text: str) -> set[str]:
    numbers: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        value = match.group(0)
        if _is_probable_structure_number(text, match.start(), match.end(), value):
            continue
        numbers.add(_normalize_number(value))
    return numbers


def _is_probable_structure_number(text: str, start: int, end: int, value: str) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    local_start = start - line_start
    local_end = end - line_start
    suffix = text[end:end + 1]

    if _is_inside_url(line, local_start, local_end):
        return True
    if re.match(r"^#{1,6}\s+\d+(?:\.\d+)+\b", line):
        return True
    if line.startswith(("#", "-", "*")) and len(value) <= 2:
        return True
    if suffix == "." and value.isdigit() and len(value) <= 2:
        return True
    if value.isdigit() and int(value) <= 25 and line.startswith("|"):
        return True
    return False


def _is_inside_url(line: str, start: int, end: int) -> bool:
    for match in _URL_RE.finditer(line):
        if start >= match.start() and end <= match.end():
            return True
    return False


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(rf"^##+\s+{re.escape(heading)}\s*$", text, re.MULTILINE)
    if not match:
        return ""

    rest = text[match.end():]
    next_heading = re.search(r"^##+\s+", rest, re.MULTILINE)
    if not next_heading:
        return rest
    return rest[:next_heading.start()]


def _final_answer_table_rows_missing_evidence(section: str) -> list[str]:
    missing_rows: list[str] = []
    seen_header = False

    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            seen_header = False
            continue
        if _is_markdown_table_separator(line):
            continue
        if not seen_header:
            seen_header = True
            continue
        if not _contains_any(line.lower(), _FINAL_ANSWER_EVIDENCE_TOKENS):
            missing_rows.append(line)

    return missing_rows


def _unknown_final_answer_evidence_refs(
    section: str,
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
) -> list[str]:
    known_refs = _registered_result_ids(result_registry) | _registered_parameter_ids(parameter_registry)
    refs = sorted(set(_EVIDENCE_ID_RE.findall(section)))
    return [ref for ref in refs if ref not in known_refs]


def _final_answer_table_rows_missing_claim_status(section: str) -> list[str]:
    missing_rows: list[str] = []
    seen_header = False

    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            seen_header = False
            continue
        if _is_markdown_table_separator(line):
            continue
        if not seen_header:
            seen_header = True
            continue
        if not _contains_any(line.lower(), _FINAL_ANSWER_STATUS_TOKENS):
            missing_rows.append(line)

    return missing_rows


def _final_answer_table_rows_missing_concrete_answer(section: str) -> list[str]:
    missing_rows: list[str] = []

    for header, row in _markdown_table_rows(section):
        answer_indices = [
            index
            for index, cell in enumerate(header)
            if _contains_any(cell.lower(), _FINAL_ANSWER_VALUE_HEADERS)
        ]
        for index in answer_indices:
            value = row[index] if index < len(row) else ""
            if _is_vague_registry_pointer(value):
                missing_rows.append(_markdown_row(row))
                break

    return missing_rows


def _is_vague_registry_pointer(value: str) -> bool:
    text = value.strip().lower().strip("`")
    if not text:
        return True

    text = _EVIDENCE_ID_RE.sub("", text)
    vague_words = (
        "见",
        "参考",
        "详见",
        "查看",
        "see",
        "refer",
        "registry",
        "注册表",
        "证据",
        "结果",
        "result",
        "parameter",
        "param",
    )
    for word in vague_words:
        text = text.replace(word, "")
    text = re.sub(r"[\s`'\"：:，,。.;；|/\\()[\]{}<>_-]+", "", text)
    return not text


def _markdown_table_rows(section: str) -> list[tuple[list[str], list[str]]]:
    rows: list[tuple[list[str], list[str]]] = []
    header: list[str] | None = None

    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            header = None
            continue
        if _is_markdown_table_separator(line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        rows.append((header, cells))

    return rows


def _markdown_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _registered_result_ids(result_registry: dict[str, object]) -> set[str]:
    ids: set[str] = set()
    for group_name in ("verified_results", "blocked_results", "warning_results"):
        group = result_registry.get(group_name, [])
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if item_id:
                ids.add(item_id)
    return ids


def _registered_parameter_ids(parameter_registry: dict[str, object]) -> set[str]:
    parameters = parameter_registry.get("parameters", [])
    if not isinstance(parameters, list):
        return set()

    ids: set[str] = set()
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        parameter_id = str(parameter.get("id") or "").strip()
        if parameter_id:
            ids.add(parameter_id)
    return ids


def _is_markdown_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _registered_numbers(
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
) -> set[str]:
    numbers: set[str] = set()
    registered_result_groups = (
        result_registry.get("verified_results", []),
        result_registry.get("blocked_results", []),
        result_registry.get("warning_results", []),
    )
    for item in _walk_values(list(registered_result_groups)):
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            numbers.add(_normalize_number(item))
        elif isinstance(item, str):
            numbers.update(_extract_numbers(item))

    for item in _walk_values(parameter_registry.get("parameters", [])):
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            numbers.add(_normalize_number(item))
        elif isinstance(item, str):
            numbers.update(_extract_numbers(item))
    return numbers


def _is_registered_number(number: str, registered: set[str]) -> bool:
    if number in registered:
        return True
    try:
        numeric = float(number)
    except ValueError:
        return False

    decimals = _decimal_places(number)
    tolerance = 0.5 * (10 ** (-decimals)) if decimals > 0 else 0.0
    if tolerance <= 0.0:
        return False

    for candidate in registered:
        try:
            registered_numeric = float(candidate)
        except ValueError:
            continue
        if abs(numeric - registered_numeric) <= tolerance:
            return True
    return False


def _decimal_places(number: str) -> int:
    mantissa = number.lower().split("e", 1)[0]
    if "." not in mantissa:
        return 0
    return len(mantissa.rsplit(".", 1)[1])


def _walk_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        output: list[Any] = []
        for child in value.values():
            output.extend(_walk_values(child))
        return output
    if isinstance(value, list):
        output = []
        for child in value:
            output.extend(_walk_values(child))
        return output
    return [value]


def _normalize_number(value: str | int | float) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    return f"{numeric:.12g}"


def _score_report(
    *,
    text: str,
    blocks: list[dict[str, object]],
    warnings: list[dict[str, object]],
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
) -> dict[str, float]:
    verified = result_registry.get("verified_results", [])
    blocked = result_registry.get("blocked_results", [])
    parameters = parameter_registry.get("parameters", [])

    verified_count = len(verified) if isinstance(verified, list) else 0
    blocked_count = len(blocked) if isinstance(blocked, list) else 0
    parameter_count = len(parameters) if isinstance(parameters, list) else 0

    block_types = {str(block.get("type")) for block in blocks}
    warning_penalty = min(len(warnings) * 0.08, 0.4)

    return {
        "result_traceability": 0.0 if "unregistered_numeric_claim" in block_types else (1.0 if verified_count else 0.5),
        "parameter_provenance": 0.0 if "missing_parameter_registry_coverage" in block_types else (1.0 if parameter_count else 0.4),
        "blocked_disclosure": 0.0 if "blocked_items_not_disclosed" in block_types else (1.0 if blocked_count else 1.0),
        "guidance_actionability": max(0.0, min(1.0, (0.8 if text.strip() else 0.0) - warning_penalty)),
    }


def report_to_json(report: dict[str, object]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def sanitize_unregistered_numeric_claims(markdown: str, report: dict[str, object]) -> str:
    """Hide unsupported numeric claims so the guidance stays conservative."""
    if not markdown or not isinstance(report, dict):
        return markdown

    numbers: list[str] = []
    blocks = report.get("blocks", [])
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "unregistered_numeric_claim":
                continue
            block_numbers = block.get("numbers", [])
            if isinstance(block_numbers, list):
                numbers.extend(str(number) for number in block_numbers if str(number).strip())

    sanitized = markdown
    unsupported = {_normalize_number(number) for number in numbers}

    def replace_if_unsupported(match: re.Match[str]) -> str:
        value = match.group(0)
        normalized = _normalize_number(value)
        if normalized in unsupported or _is_registered_number(normalized, unsupported):
            return "未注册数值（需补算或登记来源）"
        return value

    return _NUMBER_RE.sub(replace_if_unsupported, sanitized)
