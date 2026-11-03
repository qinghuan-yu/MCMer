"""markdown responsibilities."""

from __future__ import annotations

import re
from typing import Any


def normalize_math_markdown(markdown_text: str) -> str:
    """规范化 Markdown 中的 LaTeX 公式分隔符，便于前端渲染与 DOCX 导出。"""
    if not markdown_text:
        return markdown_text

    outer_fence_match = re.fullmatch(
        r"\s*```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*",
        markdown_text.replace("\r\n", "\n"),
        flags=re.IGNORECASE,
    )
    if outer_fence_match:
        markdown_text = outer_fence_match.group(1)

    def normalize_math_body(math_body: str) -> str:
        body = math_body.strip()
        body = re.sub(r"\\\\(?=[A-Za-z])", r"\\", body)
        body = re.sub(
            r"(?<!\\)\\\[(\d+(?:\.\d+)?(?:pt|em|ex|cm|mm|in))\]",
            r"\\\\[\1]",
            body,
        )
        return body

    def normalize_block_math(math_body: str) -> str:
        body = normalize_math_body(math_body)
        tag_match = re.search(r"\\tag\{([^{}]+)\}\s*$", body)
        if not tag_match:
            return f"$$\n{body}\n$$\n"
        equation_no = tag_match.group(1).strip()
        body = re.sub(r"\s*\\tag\{[^{}]+\}\s*$", "", body).rstrip()
        return f"$$\n{body}\n$$\n（{equation_no}）\n"

    code_fence_pattern = re.compile(r"(```[\s\S]*?```)")
    segments = code_fence_pattern.split(markdown_text.replace("\r\n", "\n"))
    normalized_segments: list[str] = []

    for segment in segments:
        if segment.startswith("```"):
            normalized_segments.append(segment)
            continue

        segment = (
            segment
            .replace("\\\\[", "\\[")
            .replace("\\\\]", "\\]")
            .replace("\\\\(", "\\(")
            .replace("\\\\)", "\\)")
        )

        segment = re.sub(
            r"\\\[\s*([\s\S]*?)\s*\\\]",
            lambda m: f"\n{normalize_block_math(m.group(1))}",
            segment,
        )
        segment = re.sub(
            r"\\\(\s*([\s\S]*?)\s*\\\)",
            lambda m: f"${normalize_math_body(m.group(1))}$",
            segment,
        )
        segment = re.sub(
            r"\$\$\s*([\s\S]*?)\s*\$\$",
            lambda m: normalize_block_math(m.group(1)).rstrip(),
            segment,
        )
        segment = re.sub(
            r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
            lambda m: f"${normalize_math_body(m.group(1))}$",
            segment,
            flags=re.S,
        )
        segment = re.sub(r"\n{3,}", "\n\n", segment)
        normalized_segments.append(segment)

    return "".join(normalized_segments).strip() + "\n"


def extract_markdown_formulas(markdown_text: str) -> list[dict[str, Any]]:
    """抽取 Markdown 中的内联与块级公式，供终审复核使用。"""
    text = markdown_text or ""
    formulas: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    patterns = [
        ("block", re.compile(r"\$\$\s*([\s\S]*?)\s*\$\$")),
        ("block", re.compile(r"\\\[\s*([\s\S]*?)\s*\\\]")),
        ("inline", re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.S)),
        ("inline", re.compile(r"\\\((.+?)\\\)", re.S)),
    ]

    for formula_type, pattern in patterns:
        for match in pattern.finditer(text):
            formula = " ".join((match.group(1) or "").split())
            if not formula:
                continue
            start = match.start()
            line_number = text.count("\n", 0, start) + 1
            line_start = text.rfind("\n", 0, start)
            line_end = text.find("\n", match.end())
            snippet_start = 0 if line_start < 0 else line_start + 1
            snippet_end = len(text) if line_end < 0 else line_end
            raw_line = text[snippet_start:snippet_end].strip()
            number_match = re.search(r"[（(]\s*(\d+)\s*[)）]", raw_line)
            equation_no = number_match.group(1) if number_match else ""
            key = (formula_type, formula, equation_no)
            if key in seen:
                continue
            seen.add(key)
            formulas.append(
                {
                    "type": formula_type,
                    "line": line_number,
                    "equation_no": equation_no,
                    "expression": formula,
                    "raw_context": raw_line,
                }
            )

    return formulas


def extract_markdown_tables(markdown_text: str) -> list[dict[str, Any]]:
    """抽取 Markdown 表格及其中的数值，供终审复核使用。"""
    lines = (markdown_text or "").splitlines()
    tables: list[dict[str, Any]] = []
    numeric_pattern = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
    index = 1
    current_block: list[tuple[int, str]] = []

    def flush_table() -> None:
        nonlocal current_block, index
        if len(current_block) < 2:
            current_block = []
            return

        stripped_lines = [line.strip() for _, line in current_block if line.strip()]
        if len(stripped_lines) < 2:
            current_block = []
            return

        separator = stripped_lines[1]
        if not re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", separator):
            current_block = []
            return

        header = [cell.strip() for cell in stripped_lines[0].strip("|").split("|")]
        rows: list[list[str]] = []
        numeric_values: list[dict[str, Any]] = []

        for row_offset, raw in enumerate(stripped_lines[2:], start=1):
            row = [cell.strip() for cell in raw.strip("|").split("|")]
            rows.append(row)
            for col_index, cell in enumerate(row):
                values = numeric_pattern.findall(cell)
                if not values:
                    continue
                numeric_values.append(
                    {
                        "row": row_offset,
                        "column": header[col_index] if col_index < len(header) else f"col_{col_index + 1}",
                        "cell": cell,
                        "values": values,
                    }
                )

        tables.append(
            {
                "table_index": index,
                "start_line": current_block[0][0],
                "headers": header,
                "rows": rows,
                "numeric_values": numeric_values,
            }
        )
        index += 1
        current_block = []

    for line_number, line in enumerate(lines, start=1):
        if "|" in line and line.strip().startswith("|"):
            current_block.append((line_number, line))
        else:
            flush_table()
    flush_table()
    return tables


def extract_reference_entries(markdown_text: str) -> list[str]:
    """抽取参考文献段落或引用条目。"""
    lines = (markdown_text or "").splitlines()
    entries: list[str] = []
    in_reference_section = False

    for raw_line in lines:
        line = raw_line.strip()
        if re.match(r"^#{1,6}\s*(参考文献|References)\s*$", line, re.I):
            in_reference_section = True
            continue

        if in_reference_section:
            if re.match(r"^#{1,6}\s+", line):
                break
            if line:
                entries.append(line)
            continue

        if re.match(r"^\[(\d+|[A-Za-z]+)\]\s+", line):
            entries.append(line)

    return entries


def extract_problem_parameters(question_text: str) -> list[dict[str, Any]]:
    """从原题中抽取带数值的参数语句，供终审检查“题设是否被擅自修改”。"""
    numeric_pattern = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
    parameters: list[dict[str, Any]] = []

    for line_number, raw_line in enumerate((question_text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        numbers = numeric_pattern.findall(line)
        if not numbers:
            continue
        parameters.append(
            {
                "line": line_number,
                "text": line,
                "numbers": numbers,
            }
        )

    return parameters


def enforce_markdown_image_whitelist(markdown_text: str, allowed_images: list[str] | None) -> str:
    """Remove Markdown image references that are not explicitly allowed."""
    if allowed_images is None:
        return markdown_text
    allowed = {str(item).strip().replace("\\", "/").lstrip("./") for item in allowed_images if str(item).strip()}
    if not allowed:
        return re.sub(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*$\n?", "", markdown_text or "")

    def _strip_image_title(raw: str) -> str:
        """Extract the URL portion from inside ![alt](...), handling:
        - bare path:             output/foo.png
        - path + title:          output/foo.png "title"
        - angle-bracket + title: <output/foo bar.png> "title"
        """
        target = raw.strip()
        # Angle-bracket URL: <url> [optional title]
        if target.startswith("<"):
            end = target.find(">")
            if end != -1:
                return target[1:end].strip()
        # Bare path, possibly followed by a title separated by " or '
        for sep in (' "', " '"):
            if sep in target:
                return target.split(sep, 1)[0].strip()
        return target

    def replacement(match: re.Match[str]) -> str:
        raw_target = _strip_image_title(match.group(2)).replace("\\", "/").lstrip("./")
        return match.group(0) if raw_target in allowed else ""

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replacement, markdown_text or "")
