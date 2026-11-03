"""result registry responsibilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE
from app.artifacts.visualization_schema import VisualizationContract, supported_views_for_result_type
from app.utils.file_io import load_json
from app.utils.problem_text import _deduplicate_dict_rows, _slugify_identifier
from app.utils.result_audit import _audit_torque_coefficient_artifacts
from app.utils.result_legacy import _extract_legacy_registry_entries, _looks_like_strong_result_entry, _normalize_legacy_result_text


def _normalize_result_warnings(raw_warnings: Any) -> list[str]:
    if isinstance(raw_warnings, list):
        return [str(item).strip() for item in raw_warnings if str(item).strip()]
    if raw_warnings is None:
        return []
    text = str(raw_warnings).strip()
    return [text] if text else []


def _normalize_registry_warning_entry(entry: str, section: str, source_file: str) -> dict[str, Any]:
    text = str(entry or "").strip()
    name = text[:60] or f"{section}_warning"
    severity = _classify_registry_warning_severity(text)
    status = "blocked" if severity == "blocker" else "warning"
    return {
        "id": _slugify_identifier(f"{section}_warning_{name}", "warning"),
        "name": name,
        "section": section,
        "value": "",
        "paper_value": "",
        "unit": "",
        "formula": "",
        "formula_id": "",
        "inputs": {},
        "unit_conversion": "",
        "source_data": [],
        "code_cell": "",
        "evidence": text,
        "source": "structured_result_warning",
        "source_file": source_file,
        "generated_files": [],
        "status": status,
        "severity": severity,
        "verified": False,
        "warnings": [f"reported_by_stage_{severity}"],
    }


def _classify_registry_warning_severity(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return "info"

    blocker_keywords = [
        "blocked",
        "block",
        "mismatch",
        "失败",
        "错误",
        "无法",
        "缺失",
        "未找到",
        "不一致",
        "超时",
        "终止",
        "不可",
    ]
    warning_keywords = [
        "warning",
        "警告",
        "注意",
        "可能",
        "建议",
        "风险",
        "有限",
        "fallback",
        "字体",
        "兼容",
        "需人工",
    ]

    if any(keyword in lowered for keyword in blocker_keywords):
        return "blocker"
    if any(keyword in lowered for keyword in warning_keywords):
        return "warning"
    return "info"


def normalize_result_registry_entry(entry: Any, section: str, source_file: str) -> dict[str, Any]:
    if isinstance(entry, str):
        return _normalize_legacy_result_text(entry, section, source_file)

    name = str(entry.get("name") or entry.get("id") or f"{section}_result").strip()
    entry_id = str(entry.get("id") or _slugify_identifier(f"{section}_{name}", "result")).strip()
    status = str(entry.get("status") or ("verified" if entry.get("verified") is True else "unverified")).strip().lower()
    warnings = _normalize_result_warnings(entry.get("warnings"))
    verified = bool(entry.get("verified") is True or status == "verified")
    if warnings:
        verified = verified and all("无法确认" not in warning and "不可靠" not in warning for warning in warnings)
    source = str(entry.get("source", "")).strip()
    source_data = _coerce_text_list(entry.get("source_data"))
    generated_files = _coerce_text_list(entry.get("generated_files"))
    data_artifacts = list(dict.fromkeys([*source_data, *generated_files]))
    result_type = _infer_result_type(entry)
    has_artifact_evidence = bool(source_data or generated_files)
    if (
        status == "unverified"
        and source in {"artifact_salvage", "artifact_salvage_summary"}
        and has_artifact_evidence
        and entry.get("value", entry.get("computed_value", "")) not in (None, "", [])
    ):
        status = "verified"
        verified = True
        warnings.append("artifact_verified_from_generated_file")

    return {
        "id": entry_id,
        "name": name,
        "section": section,
        "value": entry.get("value", entry.get("computed_value", "")),
        "paper_value": entry.get("paper_value", ""),
        "unit": entry.get("unit", ""),
        "formula": entry.get("formula", ""),
        "formula_id": entry.get("formula_id", ""),
        "inputs": entry.get("inputs", entry.get("input_values", {})) if isinstance(entry.get("inputs", entry.get("input_values", {})), dict) else {},
        "unit_conversion": entry.get("unit_conversion", ""),
        "source_data": source_data,
        "code_cell": entry.get("code_cell", ""),
        "evidence": entry.get("evidence", ""),
        "source": source,
        "source_file": source_file,
        "generated_files": generated_files,
        "status": status,
        "verified": verified,
        "confidence_level": _determine_confidence_level(entry, status, verified, source, warnings),
        "warnings": warnings,
        "result_type": result_type,
        "claim_text": str(entry.get("claim_text") or entry.get("evidence") or name).strip(),
        "data_artifacts": data_artifacts or source_data or [source_file],
        "columns": entry.get("columns") if isinstance(entry.get("columns"), dict) else {},
        "visualization_contract": _default_visualization_contract(
            entry=entry,
            entry_id=entry_id,
            result_type=result_type,
            source_data=data_artifacts or source_data or [source_file],
        ),
    }


def _determine_confidence_level(
    entry: dict[str, Any],
    status: str,
    verified: bool,
    source: str,
    warnings: list[str],
) -> str:
    """Determine the confidence level of a result.

    Levels:
    - verified_exact: independently recalculated and matched
    - verified_source: from registered data files, not independently verified
    - verified_baseline: timeout degradation estimate
    - salvaged_pending_review: from artifact salvage, needs explicit review
    - unverified: not verified
    - blocked: blocked by error or mismatch
    """
    if status == "blocked":
        return "blocked"

    if not verified:
        return "unverified"

    # Check for salvage origin
    if source in {"artifact_salvage", "artifact_salvage_summary"}:
        return "salvaged_pending_review"

    # Check for timeout/degradation warnings
    timeout_hints = {"超时", "timeout", "降级", "degraded", "coarse", "粗略", "估算", "estimate"}
    if any(any(hint in w.lower() for hint in timeout_hints) for w in warnings):
        return "verified_baseline"

    # Check if independently verified (has verification evidence)
    has_verification = bool(entry.get("code_cell") or entry.get("evidence"))
    if has_verification:
        return "verified_exact"

    # Default: from source data
    return "verified_source"


def _coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def _infer_result_type(entry: dict[str, Any]) -> str:
    raw = str(entry.get("result_type") or entry.get("type") or "").strip().lower()
    if raw:
        return raw

    merged = " ".join(
        str(entry.get(key) or "")
        for key in ("id", "name", "formula", "evidence", "source")
    ).lower()
    if any(token in merged for token in ("r²", "r^2", "rmse", "mae", "mape", "fit", "拟合")):
        return "regression_model"
    if any(token in merged for token in ("optimization", "optimal", "最优", "优化")):
        return "optimization_result"
    if any(token in merged for token in ("classification", "confusion", "auc", "roc", "分类")):
        return "classification_model"
    if any(token in merged for token in ("mean", "std", "median", "statistics", "统计")):
        return "descriptive_statistics"
    return "unknown"


def _default_visualization_contract(
    *,
    entry: dict[str, Any],
    entry_id: str,
    result_type: str,
    source_data: list[str],
) -> dict[str, Any]:
    raw_contract = entry.get("visualization_contract")
    if isinstance(raw_contract, dict):
        contract = VisualizationContract.from_legacy(raw_contract, result_id=entry_id)
        candidate_views = contract.candidate_views or supported_views_for_result_type(result_type)
        source = contract.source_data or source_data or ["result_registry.json"]
        return VisualizationContract(
            result_id=entry_id,
            candidate_views=candidate_views,
            required_views=contract.required_views,
            fallback_views=contract.fallback_views or [RESULT_OVERVIEW_ROLE],
            source_data=source,
            language=contract.language,
            visualization_exemption=contract.visualization_exemption,
        ).to_dict()

    candidate_views = supported_views_for_result_type(result_type)
    if not candidate_views:
        candidate_views = [RESULT_OVERVIEW_ROLE]
    return VisualizationContract(
        result_id=entry_id,
        candidate_views=candidate_views,
        required_views=[],
        fallback_views=[RESULT_OVERVIEW_ROLE],
        source_data=source_data or ["result_registry.json"],
        visualization_exemption="",
    ).to_dict()


def _ensure_visualization_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return entry
    enriched = dict(entry)
    entry_id = str(enriched.get("id") or "").strip()
    if not entry_id:
        return enriched

    source_data = _coerce_text_list(enriched.get("source_data"))
    generated_files = _coerce_text_list(enriched.get("generated_files"))
    data_artifacts = _coerce_text_list(enriched.get("data_artifacts")) or list(
        dict.fromkeys([*source_data, *generated_files])
    )
    result_type = str(enriched.get("result_type") or "").strip() or _infer_result_type(enriched)

    enriched["source_data"] = source_data
    enriched["generated_files"] = generated_files
    enriched["data_artifacts"] = data_artifacts or source_data or [str(enriched.get("source_file") or "result_registry.json")]
    enriched["result_type"] = result_type
    enriched["claim_text"] = str(
        enriched.get("claim_text")
        or enriched.get("evidence")
        or enriched.get("name")
        or entry_id
    ).strip()
    enriched["columns"] = enriched.get("columns") if isinstance(enriched.get("columns"), dict) else {}
    enriched["visualization_contract"] = _default_visualization_contract(
        entry=enriched,
        entry_id=entry_id,
        result_type=result_type,
        source_data=enriched["data_artifacts"],
    )
    return enriched


def build_result_registry(work_dir: str, structured_result_files: list[str]) -> dict[str, Any]:
    verified_results: list[dict[str, Any]] = []
    blocked_results: list[dict[str, Any]] = []
    warning_results: list[dict[str, Any]] = []
    source_files: list[str] = []

    for filename in structured_result_files:
        if not filename:
            continue
        path = Path(work_dir) / filename
        payload = load_json(str(path))
        if not isinstance(payload, dict):
            continue
        source_files.append(filename)
        section = str(payload.get("section") or path.stem)
        for raw_entry in payload.get("key_results", []):
            if isinstance(raw_entry, dict) and not _looks_like_strong_result_entry(raw_entry):
                continue
            if not isinstance(raw_entry, (dict, str)):
                continue
            normalized = normalize_result_registry_entry(raw_entry, section, filename)
            if normalized["verified"] and normalized["status"] not in {"mismatch", "blocked", "failed"}:
                verified_results.append(normalized)
            else:
                blocked_results.append(normalized)
        extra_verified, extra_blocked = _extract_legacy_registry_entries(payload, section, filename)
        verified_results.extend(extra_verified)
        blocked_results.extend(extra_blocked)
        for warning in _normalize_result_warnings(payload.get("warnings")):
            normalized_warning = _normalize_registry_warning_entry(warning, section, filename)
            if normalized_warning.get("severity") == "blocker":
                blocked_results.append(normalized_warning)
            else:
                warning_results.append(normalized_warning)

    blocked_results.extend(_audit_torque_coefficient_artifacts(work_dir))

    verified_results = [_ensure_visualization_metadata(entry) for entry in verified_results]
    verified_results = _deduplicate_dict_rows(verified_results, ["id", "source_file"])
    blocked_results = _deduplicate_dict_rows(blocked_results, ["id", "source_file"])
    warning_results = _deduplicate_dict_rows(warning_results, ["id", "source_file"])

    source_file_set = set(source_files)
    verified_source_files = {str(entry.get("source_file", "")) for entry in verified_results if entry.get("source_file")}
    blocked_source_files = {str(entry.get("source_file", "")) for entry in blocked_results if entry.get("source_file")}
    denominator = len(source_file_set) or 1
    coverage_ratio = round(len(verified_source_files) / denominator, 4)
    if not verified_results:
        coverage_status = "no_verified_results"
    elif blocked_results:
        coverage_status = "partial"
    else:
        coverage_status = "ready"

    blocked_reasons: list[str] = []
    for entry in blocked_results:
        reason = str(entry.get("evidence") or entry.get("name") or "").strip()
        if not reason:
            continue
        if reason not in blocked_reasons:
            blocked_reasons.append(reason)
        if len(blocked_reasons) >= 5:
            break

    return {
        "registry_version": "0.2",
        "source_files": source_files,
        "verified_results": verified_results,
        "blocked_results": blocked_results,
        "warning_results": warning_results,
        "summary": {
            "verified_count": len(verified_results),
            "blocked_count": len(blocked_results),
            "warning_count": len([entry for entry in warning_results if entry.get("severity") == "warning"]),
            "info_count": len([entry for entry in warning_results if entry.get("severity") == "info"]),
            "coverage_status": coverage_status,
            "coverage_ratio": coverage_ratio,
            "source_file_count": len(source_file_set),
            "verified_source_file_count": len(verified_source_files),
            "blocked_source_file_count": len(blocked_source_files),
            "top_blocked_reasons": blocked_reasons,
        },
    }
