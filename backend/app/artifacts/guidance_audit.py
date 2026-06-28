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

    _check_required_sections(text, context, warnings)
    _check_blocked_disclosure(text, results, blocks)
    if not _is_blocking_diagnostic(text):
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
    warnings: list[dict[str, object]],
) -> None:
    required_sections = guidance_context.get("required_sections", [])
    if not isinstance(required_sections, list):
        return

    for section in required_sections:
        section_text = str(section).strip()
        if section_text and section_text not in text:
            warnings.append({
                "type": "missing_expected_section",
                "location": section_text,
                "route": "writer",
                "severity": "medium",
                "fix": f"Add or clearly label the section: {section_text}",
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
