"""guidance audit tables responsibilities."""

from __future__ import annotations

import re


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
