"""guidance audit evidence responsibilities."""

from __future__ import annotations

import re
from typing import Any


_NUMBER_RE = re.compile(r"(?<![\w.])-?(?:\d+\.\d+|\d+)(?:[eE][+-]?\d+)?(?![\w.])")


_URL_RE = re.compile(r"https?://\S+")


def _registered_source_refs(
    result_registry: dict[str, object],
    parameter_registry: dict[str, object],
) -> list[str]:
    sources: list[str] = []
    parameters = parameter_registry.get("parameters", [])
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            for key in ("source_ref", "source_location"):
                value = str(parameter.get(key) or "").strip()
                if value and value not in sources:
                    sources.append(value)

    for group_name in ("verified_results", "blocked_results", "warning_results"):
        group = result_registry.get(group_name, [])
        if not isinstance(group, list):
            continue
        for result in group:
            if not isinstance(result, dict):
                continue
            for key in ("source_data", "input_files", "source_ref"):
                value = result.get(key)
                candidates = value if isinstance(value, list) else [value]
                for candidate in candidates:
                    source = str(candidate or "").strip()
                    if source and source not in sources:
                        sources.append(source)
    return sources


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
    if re.match(r"^#{1,6}\s+(?:子问题\s+)?\d+(?:\.\d+)+(?:\b|-)", line):
        return True
    if line.startswith(("#", "-", "*")) and len(value) <= 2:
        return True
    if suffix == "." and value.isdigit() and len(value) <= 2:
        return True
    if line.startswith("|"):
        first_cell = line.split("|", 2)[1].strip()
        if re.fullmatch(
            r"(?:problem)?\d+(?:\.\d+)*(?:-[^|\s]+)?",
            first_cell,
            re.IGNORECASE,
        ):
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
