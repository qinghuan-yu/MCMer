"""Build a lightweight parameter provenance registry from verified results."""

from __future__ import annotations

import re
from typing import Any


def build_parameter_registry(result_registry: dict[str, object] | None) -> dict[str, object]:
    registry = result_registry if isinstance(result_registry, dict) else {}
    verified_results = registry.get("verified_results", [])
    parameters: dict[tuple[str, str], dict[str, object]] = {}

    if isinstance(verified_results, list):
        for result in verified_results:
            if not isinstance(result, dict):
                continue
            for candidate in _parameter_candidates_from_result(result):
                key = (str(candidate.get("symbol") or "").lower(), str(candidate.get("value") or ""))
                if not key[0]:
                    continue
                existing = parameters.get(key)
                if existing:
                    _merge_linked_result(existing, candidate)
                    continue
                parameters[key] = candidate

    ordered = sorted(parameters.values(), key=lambda item: str(item.get("id") or ""))
    summary = _summary(ordered)
    return {
        "parameters": ordered,
        "summary": summary,
    }


def _parameter_candidates_from_result(result: dict[str, Any]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    raw_parameters = result.get("parameters")

    if isinstance(raw_parameters, list):
        for item in raw_parameters:
            if isinstance(item, dict):
                candidate = _normalize_parameter(item, result, default_source_type="estimated_from_data")
                if candidate:
                    candidates.append(candidate)
    elif isinstance(raw_parameters, dict):
        for symbol, value in raw_parameters.items():
            candidate = _normalize_parameter(
                {"symbol": symbol, "value": value},
                result,
                default_source_type="estimated_from_data",
            )
            if candidate:
                candidates.append(candidate)

    raw_inputs = result.get("inputs")
    if isinstance(raw_inputs, dict):
        for symbol, value in raw_inputs.items():
            if isinstance(value, dict):
                payload = {"symbol": symbol, **value}
            else:
                payload = {"symbol": symbol, "value": value}
            candidate = _normalize_parameter(payload, result, default_source_type="problem_or_input")
            if candidate:
                candidates.append(candidate)

    return candidates


def _normalize_parameter(
    payload: dict[str, Any],
    result: dict[str, Any],
    *,
    default_source_type: str,
) -> dict[str, object] | None:
    symbol = str(payload.get("symbol") or payload.get("id") or payload.get("name") or "").strip()
    if not symbol:
        return None

    source_type = str(payload.get("source_type") or default_source_type)
    value = payload.get("value")
    source_data = result.get("source_data")
    source_ref = _source_ref(payload.get("source_ref"), source_data)
    result_id = str(result.get("id") or "").strip()

    return {
        "id": f"param_{_slug(symbol)}",
        "symbol": symbol,
        "name": str(payload.get("name") or symbol),
        "meaning": str(payload.get("meaning") or payload.get("description") or ""),
        "unit": str(payload.get("unit") or ""),
        "value": value,
        "value_type": str(payload.get("value_type") or _value_type(source_type)),
        "source_type": source_type,
        "source_ref": source_ref,
        "source_location": str(payload.get("source_location") or ""),
        "estimation_method": str(payload.get("estimation_method") or payload.get("method") or ""),
        "linked_result_ids": [result_id] if result_id else [],
        "confidence": str(payload.get("confidence") or result.get("confidence_level") or ""),
        "trust_status": str(payload.get("trust_status") or _trust_status(source_type)),
        "notes": str(payload.get("notes") or ""),
    }


def _merge_linked_result(existing: dict[str, object], candidate: dict[str, object]) -> None:
    current = existing.get("linked_result_ids")
    incoming = candidate.get("linked_result_ids")
    linked: list[str] = []
    if isinstance(current, list):
        linked.extend(str(item) for item in current if str(item))
    if isinstance(incoming, list):
        linked.extend(str(item) for item in incoming if str(item))
    existing["linked_result_ids"] = sorted(set(linked))


def _source_ref(explicit: Any, source_data: Any) -> str:
    if explicit:
        return str(explicit)
    if isinstance(source_data, list) and source_data:
        return str(source_data[0])
    if isinstance(source_data, str):
        return source_data
    return ""


def _value_type(source_type: str) -> str:
    if source_type in {"problem_or_input", "problem_statement", "uploaded_data"}:
        return "source_locked"
    if "assum" in source_type:
        return "assumed"
    return "estimated"


def _trust_status(source_type: str) -> str:
    if source_type in {"problem_or_input", "problem_statement", "uploaded_data"}:
        return "source_locked"
    if "assum" in source_type:
        return "assumed"
    if "blocked" in source_type:
        return "blocked"
    return "estimated"


def _summary(parameters: list[dict[str, object]]) -> dict[str, int]:
    by_status: dict[str, int] = {}
    for parameter in parameters:
        status = str(parameter.get("trust_status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "total_count": len(parameters),
        "verified_count": by_status.get("verified", 0),
        "estimated_count": by_status.get("estimated", 0),
        "assumed_count": by_status.get("assumed", 0),
        "source_locked_count": by_status.get("source_locked", 0),
        "blocked_count": by_status.get("blocked", 0),
    }


def _slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower()).strip("_")
    return slug or "unknown"
