"""renderer requests responsibilities."""

from __future__ import annotations

import re
from typing import Any
from app.artifacts.protocols import canonical_caption_for_role, normalize_figure_role
from app.core.skills.renderer_contracts import RendererResult


def _comparison_categories(columns: dict[str, list[float]], count: int) -> list[str]:
    for name, values in columns.items():
        lowered = name.lower()
        if any(token in lowered for token in ["method", "category", "group", "class", "方案", "类别", "方法"]):
            labels = [str(value) for value in values[:count]]
            if labels:
                return labels
    return [f"C{i + 1}" for i in range(count)]


def _linked_result_ids(request: dict[str, Any], result_registry: dict[str, Any]) -> list[str]:
    return [
        str(item).strip()
        for item in (request.get("linked_result_ids", []) if isinstance(request.get("linked_result_ids"), list) else [])
        if str(item).strip()
    ] or [
        str(item.get("id") or "").strip()
        for item in (result_registry.get("verified_results", []) if isinstance(result_registry, dict) else [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ][:12]


def _request_title(request: dict[str, Any], role: str, chart_language: str) -> str:
    expected_content = request.get("expected_content", [])
    if isinstance(expected_content, list) and expected_content and str(expected_content[0]).strip():
        return str(expected_content[0]).strip()
    return canonical_caption_for_role(role, chart_language) or role


def _subproblem_id(request: dict[str, Any]) -> str:
    return _safe_subproblem_id(request.get("subproblem_id") or request.get("problem_section"))


def _request_id_and_data(request: dict[str, Any]) -> tuple[str, list[str]]:
    role = normalize_figure_role(request.get("semantic_role") or request.get("role")) or "figure"
    req_id = _safe_request_id(request.get("id"), request.get("subproblem_id"), role)
    required_data = request.get("required_data", [])
    required_data_list = [
        str(item).strip()
        for item in required_data
        if str(item).strip()
    ] if isinstance(required_data, list) else []
    return req_id, required_data_list


def _safe_request_id(raw_id: Any, raw_subproblem: Any, role: str) -> str:
    explicit = "" if _looks_serialized_mapping(raw_id) else _slug(raw_id)
    if explicit:
        return explicit
    subproblem_id = _safe_subproblem_id(raw_subproblem)
    role_slug = _slug(role) or "figure"
    return f"fig_{subproblem_id}_{role_slug}"


def _looks_serialized_mapping(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return "{" in text and "}" in text and any(key in text for key in ("number", "title", "keywords", "'number'", '"number"'))


def _safe_subproblem_id(raw: Any) -> str:
    if isinstance(raw, dict):
        number = str(raw.get("number") or raw.get("id") or raw.get("name") or "").strip()
        mapped = _problem_number_slug(number)
        if mapped:
            return mapped
        title = _slug(raw.get("title"))
        if title:
            return title
        return "problem"
    mapped = _problem_number_slug(str(raw or "").strip())
    if mapped:
        return mapped
    return _slug(raw) or "problem"


def _problem_number_slug(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"(?:problem|question|q)\s*([0-9]+)", text, re.IGNORECASE)
    if match:
        return f"problem_{match.group(1)}"
    match = re.search(r"第\s*([一二三四五六七八九十0-9]+)\s*[题問问]", text)
    if match:
        value = _chinese_number_to_int(match.group(1))
        if value:
            return f"problem_{value}"
    if text.strip().isdigit():
        return f"problem_{text.strip()}"
    return ""


def _chinese_number_to_int(text: str) -> int:
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + digits.get(text[1:], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(text, 0)


def _slug(value: Any) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_")
    return text


def _model_contract(request: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    depends_on = request.get("depends_on", {}) if isinstance(request.get("depends_on"), dict) else {}

    def _clean_list(key: str) -> list[str]:
        value = depends_on.get(key, [])
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    return _clean_list("model_objects"), _clean_list("formulas"), _clean_list("source_problem_refs")


def _artifact_result(req_id: str, artifact: dict[str, Any]) -> RendererResult:
    artifact_path = str(artifact.get("path") or "").strip() if isinstance(artifact, dict) else ""
    if not artifact_path:
        return RendererResult(missing=[{"figure_request_id": req_id, "required": True, "satisfied": False, "reason": "renderer_failed"}])
    return RendererResult(
        created_images=[artifact_path],
        satisfied=[{"figure_request_id": req_id, "required": True, "satisfied": True}],
    )
