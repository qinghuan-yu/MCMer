"""result legacy responsibilities."""

from __future__ import annotations

import re
from typing import Any
from app.utils.problem_text import _parse_numeric_value, _slugify_identifier


STRONG_RESULT_KEYS = {
    "id",
    "name",
    "value",
    "computed_value",
    "paper_value",
    "verified",
    "status",
    "formula",
    "inputs",
    "source_data",
    "code_cell",
}


BLOCKED_STATUS_HINTS = {"blocked", "failed", "mismatch", "阻断", "失败", "不通过"}


LEGACY_RESULT_CONTEXT_KEYS = {
    "title",
    "name",
    "case",
    "problem_id",
    "工况",
    "场景",
    "项目",
    "对象",
}


LEGACY_RESULT_IGNORED_KEYS = {
    "title",
    "name",
    "case",
    "problem_id",
    "工况",
    "场景",
    "项目",
    "对象",
    "说明",
    "描述",
    "建议",
    "结论",
    "限制因素",
}


def _looks_like_strong_result_entry(entry: Any) -> bool:
    return isinstance(entry, dict) and any(key in entry for key in STRONG_RESULT_KEYS)


def _normalize_legacy_scalar_result(
    *,
    section: str,
    source_file: str,
    path_parts: list[str],
    raw_value: Any,
    unit: str = "",
    status: str = "unverified",
    verified: bool = False,
    warnings: list[str] | None = None,
    source: str = "legacy_nested_results",
) -> dict[str, Any]:
    label = " / ".join(part for part in path_parts if part)
    text_value = str(raw_value).strip() if raw_value is not None else ""
    numeric_value = _parse_numeric_value(raw_value)
    return {
        "id": _slugify_identifier(f"{section}_{label}", "result"),
        "name": label or f"{section}_result",
        "section": section,
        "value": numeric_value if numeric_value is not None else text_value,
        "paper_value": text_value,
        "unit": unit,
        "formula": "",
        "formula_id": "",
        "inputs": {},
        "unit_conversion": "",
        "source_data": [],
        "code_cell": "",
        "evidence": f"{label}: {text_value}" if label else text_value,
        "source": source,
        "source_file": source_file,
        "generated_files": [],
        "status": status,
        "verified": verified,
        "warnings": warnings or [],
    }


def _split_legacy_metric_key(raw_key: str) -> tuple[str, str]:
    key = str(raw_key or "").strip()
    if not key:
        return "result", ""

    match = re.match(r"^(?P<label>.+?)_(?P<unit>[A-Za-z%°/·*^\-\d]+)$", key)
    if not match:
        return key, ""

    return match.group("label").strip() or key, match.group("unit").strip()


def _extract_legacy_key_result_metrics(
    item: dict[str, Any],
    *,
    section: str,
    source_file: str,
    is_verification_payload: bool,
) -> list[dict[str, Any]]:
    context = ""
    for key in LEGACY_RESULT_CONTEXT_KEYS:
        value = item.get(key)
        if value is not None and str(value).strip():
            context = str(value).strip()
            break

    entries: list[dict[str, Any]] = []
    for raw_key, raw_value in item.items():
        if raw_key in LEGACY_RESULT_IGNORED_KEYS:
            continue
        numeric_value = _parse_numeric_value(raw_value)
        if numeric_value is None:
            continue

        metric_name, metric_unit = _split_legacy_metric_key(str(raw_key))
        path_parts = [part for part in [context, metric_name] if part]
        entries.append(
            _normalize_legacy_scalar_result(
                section=section,
                source_file=source_file,
                path_parts=path_parts,
                raw_value=raw_value,
                unit=metric_unit,
                status="verified" if is_verification_payload else "unverified",
                verified=is_verification_payload,
                warnings=["legacy_key_result_metric"] if is_verification_payload else ["legacy_key_result_metric_requires_verification"],
                source="legacy_key_result_metric",
            )
        )

    return entries


def _collect_legacy_results_from_mapping(
    node: Any,
    *,
    section: str,
    source_file: str,
    path_parts: list[str],
    inherited_unit: str = "",
    status: str = "unverified",
    verified: bool = False,
    warnings: list[str] | None = None,
    source: str = "legacy_nested_results",
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    if isinstance(node, dict):
        local_unit = inherited_unit
        if isinstance(node.get("unit"), (str, int, float)):
            local_unit = str(node.get("unit") or "").strip()

        for key, value in node.items():
            if key in {"unit", "description", "conclusion", "limitations", "applicability", "method"}:
                continue
            entries.extend(
                _collect_legacy_results_from_mapping(
                    value,
                    section=section,
                    source_file=source_file,
                    path_parts=path_parts + [str(key)],
                    inherited_unit=local_unit,
                    status=status,
                    verified=verified,
                    warnings=warnings,
                    source=source,
                )
            )
        return entries

    if isinstance(node, list):
        for index, item in enumerate(node, start=1):
            if isinstance(item, (dict, list)):
                entries.extend(
                    _collect_legacy_results_from_mapping(
                        item,
                        section=section,
                        source_file=source_file,
                        path_parts=path_parts + [str(index)],
                        inherited_unit=inherited_unit,
                        status=status,
                        verified=verified,
                        warnings=warnings,
                        source=source,
                    )
                )
                continue
            text = str(item).strip()
            if not text:
                continue
            entries.append(
                _normalize_legacy_scalar_result(
                    section=section,
                    source_file=source_file,
                    path_parts=path_parts + [str(index)],
                    raw_value=text,
                    unit=inherited_unit,
                    status=status,
                    verified=verified,
                    warnings=warnings,
                    source=source,
                )
            )
        return entries

    if node is None:
        return entries

    text = str(node).strip()
    if not text:
        return entries
    entries.append(
        _normalize_legacy_scalar_result(
            section=section,
            source_file=source_file,
            path_parts=path_parts,
            raw_value=node,
            unit=inherited_unit,
            status=status,
            verified=verified,
            warnings=warnings,
            source=source,
        )
    )
    return entries


def _extract_legacy_registry_entries(payload: dict[str, Any], section: str, source_file: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    verified_entries: list[dict[str, Any]] = []
    blocked_entries: list[dict[str, Any]] = []

    analysis_type = str(payload.get("analysis_type") or section)
    is_verification_payload = "复核" in analysis_type or analysis_type.lower() == "verification"

    problems = payload.get("problems")
    if isinstance(problems, dict):
        for problem_key, problem_payload in problems.items():
            if not isinstance(problem_payload, dict):
                continue
            problem_title = str(problem_payload.get("title") or problem_key)
            if isinstance(problem_payload.get("results"), dict):
                blocked_entries.extend(
                    _collect_legacy_results_from_mapping(
                        problem_payload.get("results"),
                        section=section,
                        source_file=source_file,
                        path_parts=[problem_title],
                        status="unverified",
                        verified=False,
                        warnings=["legacy_nested_result_requires_verification"],
                    )
                )
            subproblems = problem_payload.get("subproblems")
            if not isinstance(subproblems, dict):
                continue
            for sub_key, sub_payload in subproblems.items():
                if not isinstance(sub_payload, dict) or not isinstance(sub_payload.get("results"), dict):
                    continue
                sub_title = str(sub_payload.get("title") or sub_key)
                blocked_entries.extend(
                    _collect_legacy_results_from_mapping(
                        sub_payload.get("results"),
                        section=section,
                        source_file=source_file,
                        path_parts=[problem_title, sub_title],
                        status="unverified",
                        verified=False,
                        warnings=["legacy_nested_result_requires_verification"],
                    )
                )

    for item in payload.get("key_results", []):
        if _looks_like_strong_result_entry(item) or not isinstance(item, dict):
            continue
        metric_entries = _extract_legacy_key_result_metrics(
            item,
            section=section,
            source_file=source_file,
            is_verification_payload=is_verification_payload,
        )
        if metric_entries:
            verified_entries.extend(metric_entries)
        title = str(item.get("title") or item.get("problem_id") or f"{section}_review")
        findings = item.get("main_findings")
        if not isinstance(findings, list) or not findings:
            continue
        raw_status = str(item.get("verification_status") or payload.get("review_status") or "").strip().lower()
        is_blocked = raw_status in BLOCKED_STATUS_HINTS
        entry = _normalize_legacy_scalar_result(
            section=section,
            source_file=source_file,
            path_parts=[title],
            raw_value="；".join(str(finding).strip() for finding in findings if str(finding).strip()),
            status="blocked" if is_blocked else ("verified" if is_verification_payload else "unverified"),
            verified=(not is_blocked) and is_verification_payload,
            warnings=["legacy_review_summary"],
            source="legacy_review_key_results",
        )
        if is_blocked or not is_verification_payload:
            blocked_entries.append(entry)
        else:
            verified_entries.append(entry)

    for item in payload.get("blocking_items", []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("item") or item.get("title") or "blocking_item")
        reason = str(item.get("reason") or item.get("recommendation") or "阻断项")
        blocked_entries.append(
            _normalize_legacy_scalar_result(
                section=section,
                source_file=source_file,
                path_parts=[label],
                raw_value=reason,
                status="blocked",
                verified=False,
                warnings=["blocking_item"],
                source="verification_blocking_item",
            )
        )

    for item in payload.get("prohibited_conclusions", []):
        text = str(item).strip()
        if not text:
            continue
        blocked_entries.append(
            _normalize_legacy_scalar_result(
                section=section,
                source_file=source_file,
                path_parts=["prohibited_conclusion"],
                raw_value=text,
                status="blocked",
                verified=False,
                warnings=["prohibited_conclusion"],
                source="verification_prohibited_conclusion",
            )
        )

    return verified_entries, blocked_entries


def _normalize_legacy_result_text(entry: str, section: str, source_file: str) -> dict[str, Any]:
    text = str(entry or "").strip()
    value_match = re.search(r"(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(?P<unit>[A-Za-z%°/·*^\-\d]+)?", text)
    formula_match = re.search(r"(?P<formula>[A-Za-zΑ-Ωα-ωPTEKσδfMRTLde_0-9\s\-+*/^().]+=[^，。；;]+)", text)
    blocked_hints = ["问题", "偏差", "不匹配", "无法", "失败", "警告", "风险", "待", "需", "需要", "谨慎"]
    has_blocked_hint = any(hint in text for hint in blocked_hints)
    verified = False
    status = "blocked" if has_blocked_hint else "unverified"
    name = text[:60] or f"{section}_result"

    return {
        "id": _slugify_identifier(f"{section}_{name}", "result"),
        "name": name,
        "section": section,
        "value": value_match.group("value") if value_match else "",
        "paper_value": "",
        "unit": (value_match.group("unit") or "").strip() if value_match else "",
        "formula": (formula_match.group("formula") or "").strip() if formula_match else "",
        "formula_id": "",
        "inputs": {},
        "unit_conversion": "",
        "source_data": [],
        "code_cell": "",
        "evidence": text,
        "source": "legacy_key_results",
        "source_file": source_file,
        "generated_files": [],
        "status": status,
        "verified": verified,
        "warnings": ["legacy_unstructured_result", "requires_structured_evidence"],
    }
