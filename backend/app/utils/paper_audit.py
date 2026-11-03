"""paper audit responsibilities."""

from __future__ import annotations

import re
from typing import Any
from app.utils.markdown import extract_markdown_formulas, extract_markdown_tables, extract_problem_parameters, extract_reference_entries
from app.utils.problem_text import _deduplicate_dict_rows, extract_symbol_assignments


PLACEHOLDER_PATTERNS = [
    "张三", "李四", "王五", "赵六", "陈七", "刘八",
    "某某", "xxx", "xx", "todo", "tbd", "待补充", "参考文献待完善", "作者待补", "年份待补",
]


BAD_MATH_PATTERNS = [
    r"=\s*$",
    r"\(\s*\)",
    r"\[\s*\]",
    r"\$\$\s*\$\$",
    r"其中，\s*为",
]


UNIT_RULES = {
    "MPa": "N/mm^2",
    "kN*mm": "N*m",
    "GPa_to_MPa": 1000,
    "P_kN_to_N": 1000,
}


def detect_placeholder_references(markdown_text: str, references: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lowered_patterns = [pattern.lower() for pattern in PLACEHOLDER_PATTERNS]
    for index, reference in enumerate(references, start=1):
        lower_reference = reference.lower()
        for pattern in lowered_patterns:
            if pattern in lower_reference:
                findings.append(
                    {
                        "type": "fake_reference",
                        "location": f"参考文献[{index}]",
                        "route": "writer",
                        "severity": "critical",
                        "fix": "删除或替换为真实检索得到的参考文献条目",
                        "evidence": reference,
                    }
                )
                break

    if not references and re.search(r"^#{1,6}\s*(参考文献|references)\s*$", markdown_text or "", re.I | re.M):
        findings.append(
            {
                "type": "fake_reference",
                "location": "参考文献章节",
                "route": "writer",
                "severity": "major",
                "fix": "若未使用外部文献，请删除参考文献章节或明确写未引入外部文献",
                "evidence": "存在参考文献标题但未提供真实条目",
            }
        )

    return findings


def detect_bad_math_issues(markdown_text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    text = markdown_text or ""
    for pattern in BAD_MATH_PATTERNS:
        for match in re.finditer(pattern, text, re.M):
            line_number = text.count("\n", 0, match.start()) + 1
            findings.append(
                {
                    "type": "formula_missing",
                    "location": f"第{line_number}行附近",
                    "route": "writer",
                    "severity": "major",
                    "fix": "补全缺失或残缺的公式表达，并核对公式编号与变量定义",
                    "evidence": match.group(0),
                }
            )
    return _deduplicate_dict_rows(findings, ["type", "location", "evidence"])


def compare_locked_parameters(problem_facts: dict[str, Any], markdown_text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    locked_parameters = (problem_facts or {}).get("locked_parameters", {})
    paper_assignments = extract_symbol_assignments(markdown_text)

    for name, locked in locked_parameters.items():
        paper_item = paper_assignments.get(name)
        if not paper_item:
            continue
        locked_value = locked.get("numeric_value")
        paper_value = paper_item.get("numeric_value")
        if locked_value is not None and paper_value is not None and abs(locked_value - paper_value) > 1e-9:
            findings.append(
                {
                    "type": "parameter_changed",
                    "location": f"参数 {name}",
                    "route": "modeling",
                    "severity": "critical",
                    "fix": f"恢复题设锁定参数 {name}={locked.get('value')} {locked.get('unit', '')}".strip(),
                    "evidence": f"题设为 {locked.get('value')}，论文中出现 {paper_item.get('value')}",
                }
            )
        locked_unit = str(locked.get("unit") or "").strip()
        paper_unit = str(paper_item.get("unit") or "").strip()
        if locked_unit and paper_unit and locked_unit != paper_unit:
            findings.append(
                {
                    "type": "unit_mismatch",
                    "location": f"参数 {name}",
                    "route": "modeling",
                    "severity": "major",
                    "fix": f"统一参数 {name} 的单位为 {locked_unit}",
                    "evidence": f"题设单位为 {locked_unit}，论文中出现 {paper_unit}",
                }
            )

    return _deduplicate_dict_rows(findings, ["type", "location", "evidence"])


def build_unit_audit_findings(problem_facts: dict[str, Any], result_registry: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    locked_parameters = (problem_facts or {}).get("locked_parameters", {})
    registry_results = (result_registry or {}).get("verified_results", []) + (result_registry or {}).get("blocked_results", [])

    for entry in registry_results:
        if not str(entry.get("unit") or "").strip():
            findings.append(
                {
                    "type": "unit_missing",
                    "location": entry.get("name") or entry.get("id") or "未命名结果",
                    "route": "solver",
                    "severity": "major",
                    "fix": "为该结果补充明确单位，并在论文与表格中保持一致",
                    "evidence": f"结果 {entry.get('id', '')} 缺少单位字段",
                }
            )

        inputs = entry.get("inputs", {}) if isinstance(entry.get("inputs"), dict) else {}
        for param_name, input_item in inputs.items():
            if not isinstance(input_item, dict):
                continue
            locked = locked_parameters.get(param_name)
            if not locked:
                continue
            locked_unit = str(locked.get("unit") or "").strip()
            input_unit = str(input_item.get("unit") or "").strip()
            if locked_unit and input_unit and locked_unit != input_unit:
                findings.append(
                    {
                        "type": "unit_mismatch",
                        "location": entry.get("name") or entry.get("id") or param_name,
                        "route": "solver",
                        "severity": "major",
                        "fix": f"将输入参数 {param_name} 的单位统一为 {locked_unit}",
                        "evidence": f"题设单位 {locked_unit}，结果登记中使用 {input_unit}",
                    }
                )

    return _deduplicate_dict_rows(findings, ["type", "location", "evidence"])


def build_paper_audit_report(
    question_text: str,
    markdown_text: str,
    problem_facts: dict[str, Any],
    result_registry: dict[str, Any],
    audit_manifest: dict[str, Any],
    delivery_audit_text: str = "",
) -> dict[str, Any]:
    references = (audit_manifest or {}).get("references", [])
    blocks: list[dict[str, Any]] = []
    blocks.extend(detect_placeholder_references(markdown_text, references))
    blocks.extend(detect_bad_math_issues(markdown_text))
    blocks.extend(compare_locked_parameters(problem_facts, markdown_text))
    blocks.extend(build_unit_audit_findings(problem_facts, result_registry))

    for entry in (result_registry or {}).get("blocked_results", []):
        name = str(entry.get("name") or entry.get("id") or "").strip()
        if not name or name not in (markdown_text or ""):
            continue
        blocks.append(
            {
                "type": "numeric_mismatch",
                "location": name,
                "route": "solver",
                "severity": "critical",
                "fix": "重新计算该结果，并确保正文只引用 verified_results 中的数值",
                "evidence": f"结果登记状态为 {entry.get('status', 'unverified')}，但正文仍引用该结果",
            }
        )

    blocks = _deduplicate_dict_rows(blocks, ["type", "location", "evidence"])
    status = "BLOCK" if blocks else "PASS"
    return {
        "status": status,
        "blocks": blocks,
        "programmatic_checks": {
            "formula_count": (audit_manifest or {}).get("formula_count", 0),
            "table_count": (audit_manifest or {}).get("table_count", 0),
            "reference_count": (audit_manifest or {}).get("reference_count", 0),
            "locked_parameter_count": len((problem_facts or {}).get("locked_parameters", {})),
            "verified_result_count": len((result_registry or {}).get("verified_results", [])),
            "blocked_result_count": len((result_registry or {}).get("blocked_results", [])),
        },
        "llm_summary": (delivery_audit_text or "").strip(),
    }


def validate_paper_audit_report(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("status") not in {"PASS", "BLOCK"}:
        return False
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict):
            return False
        required = ["type", "location", "route", "severity", "fix"]
        if any(not str(block.get(key, "")).strip() for key in required):
            return False
    return True


def build_paper_audit_manifest(question_text: str, markdown_text: str) -> dict[str, Any]:
    """构建终审所需的结构化抽取清单。"""
    formulas = extract_markdown_formulas(markdown_text)
    tables = extract_markdown_tables(markdown_text)
    references = extract_reference_entries(markdown_text)
    problem_parameters = extract_problem_parameters(question_text)

    placeholder_markers = []
    for marker in ["TODO", "TBD", "待补", "占位", "xx", "xxx", "et al.", "作者待补", "年份待补"]:
        if marker.lower() in (markdown_text or "").lower():
            placeholder_markers.append(marker)

    return {
        "formula_count": len(formulas),
        "table_count": len(tables),
        "reference_count": len(references),
        "problem_parameter_count": len(problem_parameters),
        "formulas": formulas,
        "tables": tables,
        "references": references,
        "problem_parameters": problem_parameters,
        "reference_placeholder_markers": placeholder_markers,
    }
