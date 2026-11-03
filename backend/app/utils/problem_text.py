"""problem text responsibilities."""

from __future__ import annotations

import re
from typing import Any
from app.utils.markdown import extract_markdown_formulas, extract_problem_parameters


def _slugify_identifier(text: str, fallback: str = "item") -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_]+", "_", (text or "").strip())
    value = re.sub(r"_+", "_", value).strip("_")
    return value or fallback


def _parse_numeric_value(raw_value: Any) -> float | None:
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if raw_value is None:
        return None
    text = str(raw_value).strip().replace(",", "")
    if not text:
        return None
    suffix = 0.01 if text.endswith("%") else 1.0
    if suffix != 1.0:
        text = text[:-1]
    try:
        return float(text) * suffix
    except Exception:
        return None


def _deduplicate_dict_rows(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    unique_rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        marker = tuple(row.get(key) for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        unique_rows.append(row)
    return unique_rows


def extract_symbol_assignments(text: str) -> dict[str, dict[str, Any]]:
    assignments: dict[str, dict[str, Any]] = {}
    pattern = re.compile(
        r"(?P<name>[A-Za-zΑ-Ωα-ω_][A-Za-z0-9_Α-Ωα-ω]*)\s*(?:=|：|:)\s*(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?)(?:\s*(?P<unit>[A-Za-z%°/·*^\-\d\.]+))?"
    )

    for line_number, raw_line in enumerate((text or "").splitlines(), start=1):
        for match in pattern.finditer(raw_line):
            name = match.group("name")
            assignments[name] = {
                "name": name,
                "value": match.group("value"),
                "numeric_value": _parse_numeric_value(match.group("value")),
                "unit": (match.group("unit") or "").strip(),
                "line": line_number,
                "source": raw_line.strip(),
            }
    return assignments


def extract_locked_parameters(question_text: str) -> dict[str, dict[str, Any]]:
    locked = extract_symbol_assignments(question_text)
    for item in locked.values():
        item["can_override"] = False
    return locked


def infer_problem_categories(question_text: str, breakdown_text: str = "") -> list[str]:
    text = f"{question_text}\n{breakdown_text}".lower()
    category_rules = [
        ("数据拟合型", ["拟合", "回归", "curve fit", "regression"]),
        ("优化决策型", ["最优", "优化", "决策", "规划"]),
        ("物理机理型", ["力", "力矩", "应力", "应变", "物理", "机理", "锚杆", "围岩"]),
        ("评价决策型", ["评价", "评分", "指标体系", "层次分析"]),
        ("预测型", ["预测", "forecast", "时间序列"]),
        ("图论/路径型", ["路径", "最短路", "网络", "图论"]),
        ("微分方程型", ["微分方程", "偏微分", "ode", "pde"]),
        ("多目标规划型", ["多目标", "pareto", "权衡"]),
        ("参数敏感性分析型", ["敏感性", "灵敏度", "扰动分析"]),
    ]
    categories = [name for name, keywords in category_rules if any(keyword in text for keyword in keywords)]
    return categories or ["通用建模型"]


def extract_breakdown_tasks(breakdown_text: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for line in (breakdown_text or "").splitlines():
        stripped = line.strip()
        if re.match(r"^(\d+\.|[-*])\s+", stripped):
            tasks.append({"text": re.sub(r"^(\d+\.|[-*])\s+", "", stripped), "source": stripped})
    return tasks[:12]


def build_problem_facts(question_text: str, breakdown_text: str) -> dict[str, Any]:
    locked_parameters = extract_locked_parameters(question_text)
    parameter_lines = extract_problem_parameters(question_text)
    data_tables = []
    for line in (question_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(keyword in stripped for keyword in ["表", "数据", "附件", "附录"]):
            data_tables.append({"text": stripped})

    given_formulas = extract_markdown_formulas(question_text)
    tasks = extract_breakdown_tasks(breakdown_text)
    problem_types = infer_problem_categories(question_text, breakdown_text)

    return {
        "problem_types": problem_types,
        "constants": [
            {
                "name": name,
                "value": item.get("value", ""),
                "unit": item.get("unit", ""),
                "source": item.get("source", ""),
                "can_override": False,
            }
            for name, item in locked_parameters.items()
        ],
        "given_formulas": given_formulas,
        "data_tables": data_tables,
        "tasks": tasks,
        "forbidden_changes": [
            {
                "name": name,
                "locked_value": item.get("value", ""),
                "unit": item.get("unit", ""),
                "reason": "题设显式给定，后续阶段不得擅自修改",
            }
            for name, item in locked_parameters.items()
        ],
        "locked_parameters": locked_parameters,
        "parameter_lines": parameter_lines,
    }
