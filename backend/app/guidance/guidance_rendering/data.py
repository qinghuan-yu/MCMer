"""Guidance 职责边界：guidance_rendering.data。"""

from __future__ import annotations

from typing import Any


_READER_DECISION_TOPIC_LABELS = {
    "problem_context": "观测量/未知量/时间口径",
    "identifiability": "可辨识性与缺失信息",
    "route_choice": "推荐路线与排除路线",
    "final_answers": "最终答案、证据与写作动作",
    "claim_sources": "数据/证据、假设/先验、候选/代表解来源分层",
}

def _guidance_notes(result_registry: dict[str, Any]) -> list[str]:
    summary = result_registry.get("summary", {})
    if not isinstance(summary, dict):
        return []
    notes = summary.get("guidance_notes", [])
    if not isinstance(notes, list):
        return []
    return [str(note).strip() for note in notes if str(note).strip()]

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
