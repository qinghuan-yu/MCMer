"""
通用工具函数
"""
import os
import json
import re
from pathlib import Path
from typing import Any, Optional


def _resolve_markdown_image_path(md_path: str, image_target: str) -> Path | None:
    target = (image_target or "").strip().strip("<>").strip()
    if not target or re.match(r"^[a-zA-Z]+://", target):
        return None

    if " \"" in target:
        target = target.split(' "', 1)[0].strip()

    md_dir = Path(md_path).parent
    raw_candidate = Path(target)

    search_candidates: list[Path] = []
    if raw_candidate.is_absolute():
        search_candidates.append(raw_candidate)
    else:
        search_candidates.extend(
            [
                md_dir / raw_candidate,
                md_dir / raw_candidate.name,
                md_dir / "charts" / raw_candidate.name,
                md_dir / "images" / raw_candidate.name,
                md_dir / "figures" / raw_candidate.name,
            ]
        )

    for candidate in search_candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate.absolute()
        if resolved.exists() and resolved.is_file():
            return resolved

    if not raw_candidate.is_absolute() and raw_candidate.name:
        for folder_name in ["charts", "images", "figures"]:
            folder = md_dir / folder_name
            if not folder.exists() or not folder.is_dir():
                continue
            matches = list(folder.rglob(raw_candidate.name))
            if matches:
                return matches[0]

    return None


def get_current_files(work_dir: str, sub_dir: str = "") -> str:
    """获取当前工作目录下的文件列表"""
    path = Path(work_dir)
    if sub_dir:
        path = path / sub_dir

    if not path.exists():
        return "目录不存在"

    files = []
    for f in sorted(path.iterdir()):
        if f.is_file():
            size_kb = f.stat().st_size / 1024
            files.append(f"- {f.name} ({size_kb:.1f} KB)")

    if not files:
        return "目录为空"
    return "\n".join(files)


def read_file_content(filepath: str) -> Optional[str]:
    """读取文件内容"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def save_json(data: dict, filepath: str) -> None:
    """保存 JSON 文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(filepath: str) -> Optional[dict]:
    """加载 JSON 文件"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def normalize_math_markdown(markdown_text: str) -> str:
    """规范化 Markdown 中的 LaTeX 公式分隔符，便于前端渲染与 DOCX 导出。"""
    if not markdown_text:
        return markdown_text

    def normalize_math_body(math_body: str) -> str:
        body = math_body.strip()
        body = re.sub(r"\\\\(?=[A-Za-z])", r"\\", body)
        return body

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
            lambda m: f"\n$$\n{normalize_math_body(m.group(1))}\n$$\n",
            segment,
        )
        segment = re.sub(
            r"\\\(\s*([\s\S]*?)\s*\\\)",
            lambda m: f"${normalize_math_body(m.group(1))}$",
            segment,
        )
        segment = re.sub(
            r"\$\$\s*([\s\S]*?)\s*\$\$",
            lambda m: f"$$\n{normalize_math_body(m.group(1))}\n$$",
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


def build_paper_audit_manifest(question_text: str, markdown_text: str) -> dict[str, Any]:
    """构建终审所需的结构化抽取清单。"""
    formulas = extract_markdown_formulas(markdown_text)
    tables = extract_markdown_tables(markdown_text)
    references = extract_reference_entries(markdown_text)
    problem_parameters = extract_problem_parameters(question_text)

    placeholder_markers = []
    for marker in ["TODO", "TBD", "待补", "占位", "xx", "xxx", "et al.", "作者待补", "年份待补"]:
        if marker.lower() in (markdown_text or "").lower():
            placeholder_markers.append(marker)

    return {
        "formula_count": len(formulas),
        "table_count": len(tables),
        "reference_count": len(references),
        "problem_parameter_count": len(problem_parameters),
        "formulas": formulas,
        "tables": tables,
        "references": references,
        "problem_parameters": problem_parameters,
        "reference_placeholder_markers": placeholder_markers,
    }


def md_to_docx(md_path: str, docx_path: str) -> bool:
    """尝试将 markdown 转为 docx，优先保留 LaTeX 公式为 Word 数学对象。"""
    try:
        import pypandoc

        pypandoc.convert_file(
            md_path,
            "docx",
            outputfile=docx_path,
            extra_args=[
                f"--resource-path={Path(md_path).parent}{os.pathsep}{Path(md_path).parent / 'charts'}"
            ],
        )
        # pypandoc 在 outputfile 模式下通常返回空字符串
        return os.path.exists(docx_path)
    except Exception:
        pass

    try:
        from docx import Document
        from docx.shared import Inches
        from docx.shared import Pt
    except Exception:
        return False

    try:
        markdown = normalize_math_markdown(Path(md_path).read_text(encoding="utf-8"))
    except Exception:
        return False

    document = Document()
    normal_style = document.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(11)

    lines = markdown.splitlines()
    in_code_block = False
    code_buffer: list[str] = []
    table_buffer: list[str] = []

    def flush_code() -> None:
        nonlocal code_buffer
        if not code_buffer:
            return
        paragraph = document.add_paragraph()
        run = paragraph.add_run("\n".join(code_buffer))
        run.font.name = "Consolas"
        run.font.size = Pt(10)
        code_buffer = []

    def flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        rows = []
        for raw in table_buffer:
            stripped = raw.strip()
            if not stripped:
                continue
            if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", stripped):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            rows.append(cells)
        if rows:
            column_count = max(len(row) for row in rows)
            table = document.add_table(rows=len(rows), cols=column_count)
            table.style = "Table Grid"
            for row_index, row in enumerate(rows):
                for col_index, cell in enumerate(row):
                    table.cell(row_index, col_index).text = cell
        table_buffer = []

    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("```"):
            flush_table()
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_buffer.append(stripped)
            continue

        if "|" in stripped and stripped.count("|") >= 2:
            table_buffer.append(stripped)
            continue

        flush_table()

        if not stripped.strip():
            document.add_paragraph("")
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            level = min(len(heading_match.group(1)), 6)
            document.add_heading(heading_match.group(2).strip(), level=level)
            continue

        bullet_match = re.match(r"^\s*[-*+]\s+(.*)$", stripped)
        if bullet_match:
            document.add_paragraph(bullet_match.group(1).strip(), style="List Bullet")
            continue

        ordered_match = re.match(r"^\s*\d+[.)]\s+(.*)$", stripped)
        if ordered_match:
            document.add_paragraph(ordered_match.group(1).strip(), style="List Number")
            continue

        image_match = re.match(r"!\[(.*?)\]\((.*?)\)", stripped.strip())
        if image_match:
            alt_text = image_match.group(1).strip() or "图片"
            path_text = image_match.group(2).strip()
            image_path = _resolve_markdown_image_path(md_path, path_text)
            if image_path:
                picture_paragraph = document.add_paragraph()
                picture_paragraph.alignment = 1
                try:
                    picture_paragraph.add_run().add_picture(str(image_path), width=Inches(6))
                except Exception:
                    picture_paragraph.add_run().add_picture(str(image_path))

                caption = document.add_paragraph(alt_text)
                caption.alignment = 1
            else:
                document.add_paragraph(f"[图片缺失] {alt_text}: {path_text}")
            continue

        document.add_paragraph(stripped)

    flush_table()
    flush_code()

    try:
        document.save(docx_path)
    except Exception:
        return False
    return os.path.exists(docx_path)
